// AuthService — register / login / refresh / logout business logic.
//
// All wire-format parsing (request decode, cookie set, JSON
// response) lives in the handler layer; this service deals in
// typed inputs and typed errors. Handlers translate the typed
// errors into HTTP status codes.
package service

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/mail"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/auth"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// Typed errors returned by AuthService. Handlers translate these
// into HTTP statuses + JSON error codes. The mapping is one-way:
// the service never sees the HTTP layer.
var (
	ErrInvalidCredentials = errors.New("auth: invalid credentials")
	ErrAccountInactive    = errors.New("auth: account inactive")
	ErrEmailExists        = errors.New("auth: email exists")
	ErrInvalidInvitation  = errors.New("auth: invalid invitation")
	ErrValidation         = errors.New("auth: validation failed")
	ErrTokenInvalid       = errors.New("auth: token invalid")
	ErrTokenReplay        = errors.New("auth: token replay")
)

// Result types — handler builds JSON + cookies from these.

// AuthResult is what register / login / refresh hand back to the
// handler layer. Carries everything the handler needs to set
// cookies and build the JSON response body.
//
// CRITICAL: the RawRefreshToken is COOKIE-ONLY. The handler MUST
// set it as a cookie and MUST NOT include it in the JSON body —
// keeping it out of the body is what makes the HttpOnly contract
// meaningful (a successful login response should not echo the
// long-lived credential anywhere XSS can scrape).
type AuthResult struct {
	UserID           uuid.UUID
	TenantID         uuid.UUID
	Role             string
	AccessToken      string
	RawRefreshToken  string
	CSRFToken        string
	RefreshTokenTTL  time.Duration
}

// AuthService is the auth domain service.
type AuthService struct {
	pool        *pgxpool.Pool
	tenants     *repository.TenantRepo
	users       *repository.UserRepo
	refresh     *repository.RefreshTokenRepo
	invitations *repository.InvitationTokenRepo

	userJWTSecret string
	csrfSecret    string
	accessTTL     time.Duration
	refreshTTL    time.Duration
}

// NewAuthService wires the dependencies.
//
// ``pool`` is held directly so the register flow can run tenant +
// user creation inside a single transaction — the Session 2
// orphan-tenant bug. All repos still use the pool for non-tx
// queries via their default constructors.
func NewAuthService(
	pool *pgxpool.Pool,
	tenants *repository.TenantRepo,
	users *repository.UserRepo,
	refresh *repository.RefreshTokenRepo,
	invitations *repository.InvitationTokenRepo,
	userJWTSecret, csrfSecret string,
	accessTTL, refreshTTL time.Duration,
) *AuthService {
	return &AuthService{
		pool:          pool,
		tenants:       tenants,
		users:         users,
		refresh:       refresh,
		invitations:   invitations,
		userJWTSecret: userJWTSecret,
		csrfSecret:    csrfSecret,
		accessTTL:     accessTTL,
		refreshTTL:    refreshTTL,
	}
}

// ---------------------------------------------------------------------------
// Register
// ---------------------------------------------------------------------------

// RegisterInput is the typed payload for AuthService.Register. The
// invitation token is optional — empty string means "no invitation
// (auto-create personal tenant)".
type RegisterInput struct {
	Email           string
	Password        string
	Name            string
	InvitationToken string
}

// Register creates a new user (with a personal tenant or via
// invitation) and returns the full auth bundle.
//
// The tenant + user inserts (and the invitation consume, when one
// is supplied) ALL run inside a single transaction so a failed
// user insert (duplicate email) rolls back the just-created
// tenant. This closes the Session 2 orphan-tenant bug.
func (s *AuthService) Register(ctx context.Context, in RegisterInput) (*AuthResult, error) {
	email := strings.ToLower(strings.TrimSpace(in.Email))
	name := strings.TrimSpace(in.Name)

	if err := validateEmail(email); err != nil {
		return nil, err
	}
	if err := validatePassword(in.Password); err != nil {
		return nil, err
	}
	if err := validateName(name); err != nil {
		return nil, err
	}

	// Hash password BEFORE the tx so a failing KDF doesn't leave a
	// dangling tx open.
	hash, err := auth.HashPassword(in.Password)
	if err != nil {
		return nil, fmt.Errorf("register: hash password: %w", err)
	}

	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return nil, fmt.Errorf("register: begin tx: %w", err)
	}
	// Default-rollback. The success path explicitly commits below.
	committed := false
	defer func() {
		if !committed {
			_ = tx.Rollback(ctx)
		}
	}()

	tenantsTx := s.tenants.WithTx(tx)
	usersTx := s.users.WithTx(tx)
	invitationsTx := s.invitations.WithTx(tx)

	// Resolve the tenant the new user joins.
	var tenantID uuid.UUID
	var role string
	var invitation *model.InvitationToken
	if in.InvitationToken != "" {
		inv, err := invitationsTx.GetInvitationByToken(ctx, in.InvitationToken)
		if err != nil {
			return nil, ErrInvalidInvitation
		}
		if !inv.Usable(time.Now().UTC()) {
			return nil, ErrInvalidInvitation
		}
		tenantID = inv.TenantID
		role = "user"
		invitation = inv
	} else {
		// Auto-create a personal tenant. Runs INSIDE the tx so a
		// later user-insert failure rolls back the tenant row.
		t, err := s.createPersonalTenantTx(ctx, tenantsTx, email, name)
		if err != nil {
			return nil, err
		}
		tenantID = t.ID
		role = "admin" // admin of their own personal tenant
	}

	u, err := usersTx.CreateUser(ctx, &model.User{
		TenantID:     tenantID,
		Email:        email,
		Name:         name,
		PasswordHash: hash,
		Role:         role,
		IsActive:     true,
	})
	if err != nil {
		if errors.Is(err, repository.ErrDuplicate) {
			return nil, ErrEmailExists
		}
		return nil, err
	}

	// Consume the invitation if one was supplied. Runs inside the
	// same tx so a downstream failure (none today, but defensive)
	// would un-use the invitation alongside rolling back the user.
	if invitation != nil {
		if err := invitationsTx.UseInvitation(ctx, invitation.ID, u.ID); err != nil {
			return nil, err
		}
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, fmt.Errorf("register: commit: %w", err)
	}
	committed = true

	return s.issueAuthBundle(ctx, u)
}

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------

// Login validates credentials and issues a fresh token family.
// Returns ErrInvalidCredentials on bad email / wrong password /
// inactive account — the handler maps all three to 401 with a
// generic message so the client can't probe which is wrong.
func (s *AuthService) Login(ctx context.Context, email, password string) (*AuthResult, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	u, err := s.users.GetUserByEmail(ctx, email)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrInvalidCredentials
		}
		return nil, err
	}
	if !u.IsActive {
		return nil, ErrInvalidCredentials
	}
	if err := auth.VerifyPassword(password, u.PasswordHash); err != nil {
		return nil, ErrInvalidCredentials
	}
	return s.issueAuthBundle(ctx, u)
}

// ---------------------------------------------------------------------------
// Refresh
// ---------------------------------------------------------------------------

// Refresh rotates the access + refresh tokens.
//
// Token rotation: the presented token is revoked and a new one
// minted under the same family. The CSRF token is also rotated so
// every refresh re-anchors the double-submit binding.
//
// Replay detection: if the presented token is ALREADY revoked, we
// treat it as a leaked-credential signal and revoke the whole
// family, then return ErrTokenReplay so the handler can 401 + clear
// cookies + force re-login.
func (s *AuthService) Refresh(ctx context.Context, rawToken string) (*AuthResult, error) {
	hash := hashRefreshToken(rawToken)
	tok, err := s.refresh.GetRefreshTokenByHash(ctx, hash)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrTokenInvalid
		}
		return nil, err
	}

	now := time.Now().UTC()

	// Replay detection — a revoked token being presented again
	// (re-use of a value that should have been discarded) is a
	// stolen-token signal. Burn the whole family.
	if tok.RevokedAt != nil {
		_ = s.refresh.RevokeRefreshTokenFamily(ctx, tok.FamilyID)
		return nil, ErrTokenReplay
	}

	if now.After(tok.ExpiresAt) {
		return nil, ErrTokenInvalid
	}

	u, err := s.users.GetUserByID(ctx, tok.UserID)
	if err != nil {
		return nil, ErrTokenInvalid
	}
	if !u.IsActive {
		return nil, ErrTokenInvalid
	}

	// Revoke the current token + mint a new one under the SAME
	// family.
	if err := s.refresh.RevokeRefreshToken(ctx, tok.ID); err != nil {
		return nil, err
	}
	rawNew, hashNew, err := generateRefreshToken()
	if err != nil {
		return nil, err
	}
	newRow := &model.RefreshToken{
		UserID:    u.ID,
		TokenHash: hashNew,
		FamilyID:  tok.FamilyID,
		ExpiresAt: now.Add(s.refreshTTL),
	}
	if err := s.refresh.CreateRefreshToken(ctx, newRow); err != nil {
		return nil, err
	}

	accessTok, err := auth.IssueAccessToken(s.userJWTSecret, u.ID, u.TenantID, u.Role, s.accessTTL)
	if err != nil {
		return nil, err
	}
	csrf, err := auth.IssueCSRFToken(s.csrfSecret, u.ID.String())
	if err != nil {
		return nil, err
	}

	return &AuthResult{
		UserID:          u.ID,
		TenantID:        u.TenantID,
		Role:            u.Role,
		AccessToken:     accessTok,
		RawRefreshToken: rawNew,
		CSRFToken:       csrf,
		RefreshTokenTTL: s.refreshTTL,
	}, nil
}

// ---------------------------------------------------------------------------
// Logout
// ---------------------------------------------------------------------------

// Logout revokes the WHOLE family the presented token belongs to.
// This signs the user out of every device that shared the family.
// Idempotent — calling logout on an already-revoked family is a
// no-op (we still clear the caller's cookies in the handler).
func (s *AuthService) Logout(ctx context.Context, rawToken string) error {
	if rawToken == "" {
		return nil
	}
	tok, err := s.refresh.GetRefreshTokenByHash(ctx, hashRefreshToken(rawToken))
	if err != nil {
		// Unknown / malformed token → treat as already-logged-out.
		return nil
	}
	return s.refresh.RevokeRefreshTokenFamily(ctx, tok.FamilyID)
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

// issueAuthBundle builds an AuthResult for a freshly-authenticated
// user (register, login). Shared so register and login emit
// identical bundles.
func (s *AuthService) issueAuthBundle(ctx context.Context, u *model.User) (*AuthResult, error) {
	rawRefresh, hash, err := generateRefreshToken()
	if err != nil {
		return nil, err
	}
	familyID := uuid.New()
	now := time.Now().UTC()
	if err := s.refresh.CreateRefreshToken(ctx, &model.RefreshToken{
		UserID:    u.ID,
		TokenHash: hash,
		FamilyID:  familyID,
		ExpiresAt: now.Add(s.refreshTTL),
	}); err != nil {
		return nil, err
	}

	accessTok, err := auth.IssueAccessToken(
		s.userJWTSecret, u.ID, u.TenantID, u.Role, s.accessTTL,
	)
	if err != nil {
		return nil, err
	}
	csrf, err := auth.IssueCSRFToken(s.csrfSecret, u.ID.String())
	if err != nil {
		return nil, err
	}

	return &AuthResult{
		UserID:          u.ID,
		TenantID:        u.TenantID,
		Role:            u.Role,
		AccessToken:     accessTok,
		RawRefreshToken: rawRefresh,
		CSRFToken:       csrf,
		RefreshTokenTTL: s.refreshTTL,
	}, nil
}

// createPersonalTenantTx is the tx-scoped variant used by Register.
// Takes the tx-bound TenantRepo so slug-check + insert happen on the
// same transaction (slug uniqueness must hold across the commit
// boundary).
func (s *AuthService) createPersonalTenantTx(
	ctx context.Context, tenants *repository.TenantRepo, email, name string,
) (*model.Tenant, error) {
	return s.createPersonalTenantOn(ctx, tenants, email, name)
}

// createPersonalTenant builds a tenant slug from the email local-
// part + a 4-char random suffix and retries on collision (up to 5
// times, after which we give up rather than spin forever).
func (s *AuthService) createPersonalTenant(ctx context.Context, email, name string) (*model.Tenant, error) {
	return s.createPersonalTenantOn(ctx, s.tenants, email, name)
}

// createPersonalTenantOn is the shared core; takes whichever
// TenantRepo handle (pool-bound or tx-bound) the caller supplies.
func (s *AuthService) createPersonalTenantOn(
	ctx context.Context, tenants *repository.TenantRepo, email, name string,
) (*model.Tenant, error) {
	local := emailLocalPart(email)
	// Normalize: lowercase alphanumeric + dash. Strip everything
	// else so slugs always render as URL-safe strings.
	local = slugify(local)
	if local == "" {
		local = "user"
	}

	const maxRetries = 5
	for attempt := 0; attempt < maxRetries; attempt++ {
		suffix, err := randomSlugSuffix()
		if err != nil {
			return nil, err
		}
		slug := local + "-" + suffix

		// Pre-check the slug; speeds up the common path and keeps
		// the unique-violation branch a true "race lost" path.
		exists, err := tenants.SlugExists(ctx, slug)
		if err != nil {
			return nil, err
		}
		if exists {
			continue
		}
		t, err := tenants.CreateTenant(ctx, &model.Tenant{
			Name:       name,
			Slug:       slug,
			IsPersonal: true,
		})
		if err != nil {
			if errors.Is(err, repository.ErrDuplicate) {
				continue // race; retry
			}
			return nil, err
		}
		return t, nil
	}
	return nil, fmt.Errorf("register: tenant slug collision exhausted retries")
}

// validateEmail rejects empty / malformed strings.
func validateEmail(email string) error {
	if email == "" {
		return fmt.Errorf("%w: email is required", ErrValidation)
	}
	if _, err := mail.ParseAddress(email); err != nil {
		return fmt.Errorf("%w: email is malformed", ErrValidation)
	}
	return nil
}

// validatePassword pins the minimum length floor (8 codepoints).
func validatePassword(pw string) error {
	if utf8.RuneCountInString(pw) < 8 {
		return fmt.Errorf("%w: password must be at least 8 characters", ErrValidation)
	}
	return nil
}

func validateName(name string) error {
	if name == "" {
		return fmt.Errorf("%w: name is required", ErrValidation)
	}
	return nil
}

// emailLocalPart returns the substring before the @. Falls back to
// the whole string if no @ is present (defensive — validateEmail
// should have rejected such inputs upstream).
func emailLocalPart(email string) string {
	if i := strings.IndexByte(email, '@'); i >= 0 {
		return email[:i]
	}
	return email
}

// slugify lowercases + keeps only [a-z0-9-]. Non-Latin scripts (the
// product is bilingual Thai/English) are stripped entirely; the
// random suffix carries the uniqueness so a Thai-only email like
// ``ลูกค้า@example.com`` becomes ``user-a3f2``.
func slugify(s string) string {
	s = strings.ToLower(s)
	var b strings.Builder
	for _, r := range s {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case r == '-' || r == '.' || r == '_':
			b.WriteByte('-')
		}
	}
	out := strings.Trim(b.String(), "-")
	if len(out) > 32 {
		out = out[:32]
	}
	return out
}

// randomSlugSuffix returns a 4-char hex string for tenant slugs.
func randomSlugSuffix() (string, error) {
	buf := make([]byte, 2)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("random suffix: %w", err)
	}
	return hex.EncodeToString(buf), nil
}

// generateRefreshToken returns (rawToken, sha256Hex) for a fresh
// 32-byte random refresh token. The raw value is what the cookie
// carries; the hash is what gets persisted.
func generateRefreshToken() (raw, hash string, err error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", "", fmt.Errorf("refresh token: %w", err)
	}
	raw = base64.RawURLEncoding.EncodeToString(buf)
	hash = hashRefreshToken(raw)
	return raw, hash, nil
}

// hashRefreshToken returns the SHA-256 hex digest of the raw token
// value. Used to look up the stored row without ever holding the
// raw value at rest.
func hashRefreshToken(raw string) string {
	sum := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(sum[:])
}
