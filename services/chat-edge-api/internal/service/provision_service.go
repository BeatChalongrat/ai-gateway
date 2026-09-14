// ProvisionService — lazily mirrors Portal users + tenants into the
// chat-edge identity schema on their first chat request.
//
// Why mirror instead of cross-DB FK: chat.conversations + chat.messages
// FK to identity.users.id. We can't FK to a row in a different
// service's DB, so we shadow the Portal's user with the SAME UUID.
// The Portal remains the source of truth for the user's password,
// email verification, plan, credits, etc; we keep a "shell" row for
// referential integrity within the chat schema.
//
// The provision is idempotent (INSERT … ON CONFLICT DO NOTHING) and
// memoised in-process via a sync.Map so a hot user only pays the DB
// roundtrip once per process lifetime. Cache misses just re-issue
// the (cheap, indexed) upsert — never a correctness issue.
package service

import (
	"regexp"
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ProvisionService owns the upsert path used by the auth middleware
// after parsing a Portal JWT.
type ProvisionService struct {
	pool *pgxpool.Pool
	// testAccount (FIX119) — an email matching it is provisioned with
	// is_test = true. nil means nothing is a test account.
	testAccount *regexp.Regexp

	// cache marks (userID, tenantID) pairs that have been provisioned
	// during this process. Membership doesn't expire — once a row is
	// in the DB it stays there. Bounded by the active user set.
	cache sync.Map
}

func NewProvisionService(pool *pgxpool.Pool) *ProvisionService {
	return &ProvisionService{pool: pool}
}

// EnsureUserExists makes sure identity.users + identity.tenants rows
// exist for the given Portal identity. Idempotent.
//
//   - If “tenantID“ is uuid.Nil, a personal tenant is auto-minted
//     using the user's UUID as a stable derivation (slug, name).
//   - If the row already exists in identity.users with a different
//     tenant_id (e.g. Portal moved the user to a new tenant), the
//     stored row is updated to match. We don't move conversation
//     rows — those stay under their original tenant.
//
// Returns the effective tenantID (auto-minted or the input).
func (s *ProvisionService) EnsureUserExists(
	ctx context.Context,
	userID uuid.UUID,
	email, tenantRole string,
	portalTenantID string,
) (uuid.UUID, error) {
	if userID == uuid.Nil {
		return uuid.Nil, fmt.Errorf("provision: empty user id")
	}

	effectiveTenant, tenantSlug := deriveEffectiveTenant(userID, portalTenantID)

	cacheKey := userID.String() + ":" + effectiveTenant.String()
	if _, hit := s.cache.Load(cacheKey); hit {
		return effectiveTenant, nil
	}

	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return uuid.Nil, fmt.Errorf("provision: begin: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	// Tenant first — FK constraint on users requires it.
	tenantName := deriveTenantName(email, userID)
	if _, err := tx.Exec(ctx,
		`INSERT INTO identity.tenants (id, name, slug, is_personal)
		 VALUES ($1, $2, $3, TRUE)
		 ON CONFLICT (id) DO UPDATE
		   SET name = EXCLUDED.name,
		       slug = EXCLUDED.slug,
		       updated_at = NOW()`,
		effectiveTenant, tenantName, tenantSlug,
	); err != nil {
		return uuid.Nil, fmt.Errorf("provision: tenant upsert: %w", err)
	}

	// Slug collisions (extremely unlikely with UUID-derived slugs) get
	// retried with a UUID-suffixed slug to keep the unique constraint
	// happy. We don't need this path in practice, but guarding makes
	// the failure mode explicit.
	displayName := strings.TrimSpace(email)
	if displayName == "" {
		displayName = userID.String()
	}
	if _, err := tx.Exec(ctx,
		`UPDATE identity.users
		    SET id = $1,
		        tenant_id = $2,
		        name = $3,
		        updated_at = NOW()
		  WHERE email = LOWER($4)
		    AND id <> $1`,
		userID, effectiveTenant, displayName, displayName,
	); err != nil {
		return uuid.Nil, fmt.Errorf("provision: user email reconcile: %w", err)
	}
	// The role column has a CHECK constraint allowing only
	// 'user' / 'admin' / 'super_admin'. Portal sends "owner"/"member"
	// at a different semantic level; we map both to "user" for the
	// chat schema — admin status in chat is unrelated to Portal
	// tenant ownership today. The owner/member distinction is
	// already carried on the request context.
	if _, err := tx.Exec(ctx,
		`INSERT INTO identity.users
		   (id, tenant_id, email, name, password_hash, role, is_active, provisioned_from, is_test)
		 VALUES ($1, $2, LOWER($3), $4, '', 'user', TRUE, 'portal', $5)
		 ON CONFLICT (id) DO UPDATE
		   SET tenant_id = EXCLUDED.tenant_id,
		       email     = EXCLUDED.email,
		       is_test   = EXCLUDED.is_test,
		       updated_at = NOW()`,
		userID, effectiveTenant, displayName, displayName, s.IsTestAccount(email),
	); err != nil {
		return uuid.Nil, fmt.Errorf("provision: user upsert: %w", err)
	}

	if err := tx.Commit(ctx); err != nil {
		return uuid.Nil, fmt.Errorf("provision: commit: %w", err)
	}

	s.cache.Store(cacheKey, time.Now())
	_ = tenantRole // reserved for future RBAC mapping (owner → admin?)
	return effectiveTenant, nil
}

func deriveEffectiveTenant(userID uuid.UUID, portalTenantID string) (uuid.UUID, string) {
	portalTenantID = strings.TrimSpace(portalTenantID)
	if portalTenantID == "" {
		return userID, deriveTenantSlug(userID)
	}
	if parsed, err := uuid.Parse(portalTenantID); err == nil {
		return parsed, portalTenantID
	}
	return uuid.NewSHA1(uuid.NameSpaceURL, []byte(portalTenantID)), portalTenantID
}

func deriveTenantName(email string, userID uuid.UUID) string {
	email = strings.TrimSpace(email)
	if email == "" {
		return userID.String()[:8] + "'s workspace"
	}
	local := email
	if i := strings.IndexByte(email, '@'); i > 0 {
		local = email[:i]
	}
	if local == "" {
		local = userID.String()[:8]
	}
	return local + "'s workspace"
}

func deriveTenantSlug(userID uuid.UUID) string {
	// Slug only needs to be unique + URL-safe. UUID hex is both.
	return "u-" + strings.ReplaceAll(userID.String(), "-", "")[:24]
}

// SetTestAccountPattern installs the FIX119 test-account matcher
// (config TEST_ACCOUNT_EMAIL_PATTERN). nil disables labelling.
func (s *ProvisionService) SetTestAccountPattern(re *regexp.Regexp) { s.testAccount = re }

// IsTestAccount reports whether an email belongs to a test account.
func (s *ProvisionService) IsTestAccount(email string) bool {
	return s.testAccount != nil && s.testAccount.MatchString(strings.ToLower(strings.TrimSpace(email)))
}
