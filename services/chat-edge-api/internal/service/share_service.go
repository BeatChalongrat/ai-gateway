// ShareService — public read-only share-link CRUD.
//
// Tokens are 22 chars of URL-safe base64 from crypto/rand (~128 bits
// of entropy). On the read path the service enforces expiry filtering
// itself so the handler can map expired-vs-missing to the same 404 —
// no information leak about whether a token ever existed.
package service

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

var (
	ErrShareNotFound     = errors.New("share: not found")
	ErrShareExpired      = errors.New("share: expired")
	ErrShareConvNotFound = errors.New("share: conversation not found")
	ErrShareBadExpiry    = errors.New("share: unsupported expires_in value")
)

// ShareService owns share-link lifecycle.
type ShareService struct {
	shares        *repository.ShareRepo
	conversations *repository.ConversationRepo
	messages      *repository.MessageRepo
	files         *repository.FileRepo
}

func NewShareService(
	shares *repository.ShareRepo,
	conversations *repository.ConversationRepo,
	messages *repository.MessageRepo,
	files *repository.FileRepo,
) *ShareService {
	return &ShareService{
		shares:        shares,
		conversations: conversations,
		messages:      messages,
		files:         files,
	}
}

// ---------------------------------------------------------------------------
// Create / revoke
// ---------------------------------------------------------------------------

// Create or refresh a share link. Calling twice on the same
// conversation rotates the token and replaces the expiry — there's
// only one active link per conversation.
func (s *ShareService) Create(
	ctx context.Context,
	tenantID, userID, convID uuid.UUID,
	expiresIn string,
) (*model.SharedConversation, error) {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrShareConvNotFound
		}
		return nil, err
	}
	if conv.UserID != userID {
		return nil, ErrShareConvNotFound
	}

	expiresAt, err := parseExpiresIn(expiresIn)
	if err != nil {
		return nil, err
	}

	// Drop any existing share for this conversation so we don't
	// accumulate dead rows. Best-effort — ErrNotFound is fine.
	if err := s.shares.DeleteByConversation(ctx, tenantID, convID); err != nil &&
		!errors.Is(err, repository.ErrNotFound) {
		return nil, err
	}

	token, err := newShareToken()
	if err != nil {
		return nil, err
	}
	return s.shares.Create(ctx, tenantID, convID, token, expiresAt)
}

// Revoke deletes the share row. ErrShareNotFound when nothing to do.
func (s *ShareService) Revoke(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) error {
	// Re-verify ownership before deleting so a wrong-tenant caller
	// can't probe other tenants' share existence via 404 / 204
	// timing.
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrShareConvNotFound
		}
		return err
	}
	if conv.UserID != userID {
		return ErrShareConvNotFound
	}
	if err := s.shares.DeleteByConversation(ctx, tenantID, convID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrShareNotFound
		}
		return err
	}
	return nil
}

// GetForOwner returns the current share row (if any) for the owner's
// modal pre-fill. Returns ErrShareNotFound when nothing's set.
func (s *ShareService) GetForOwner(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) (*model.SharedConversation, error) {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrShareConvNotFound
		}
		return nil, err
	}
	if conv.UserID != userID {
		return nil, ErrShareConvNotFound
	}
	row, err := s.shares.GetByConversation(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrShareNotFound
		}
		return nil, err
	}
	return row, nil
}

// ---------------------------------------------------------------------------
// Public read
// ---------------------------------------------------------------------------

// SharedView is the wire shape the public endpoint returns. Kept
// inside the service so the handler stays slim.
type SharedView struct {
	Conversation *model.Conversation
	Messages     []model.Message
	FilesByMsg   map[uuid.UUID][]model.ConversationFile
	ExpiresAt    *time.Time
}

// Resolve looks up a token, enforces expiry, and pulls the whole
// conversation thread for read-only rendering. The same 404 covers
// both "no such token" and "expired" so the response doesn't leak
// share-existence information.
func (s *ShareService) Resolve(ctx context.Context, token string) (*SharedView, error) {
	row, err := s.shares.GetByToken(ctx, token)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrShareNotFound
		}
		return nil, err
	}
	if row.ExpiresAt != nil && row.ExpiresAt.Before(time.Now()) {
		return nil, ErrShareNotFound
	}
	conv, err := s.conversations.GetByID(ctx, row.TenantID, row.ConversationID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrShareNotFound
		}
		return nil, err
	}
	// Pull a generous slice (1000) — same limit as the export path.
	msgs, _, err := s.messages.ListByConversation(ctx, row.TenantID, row.ConversationID, 1000, uuid.Nil)
	if err != nil {
		return nil, err
	}

	// File metadata only (not download urls — the public view is
	// read-only; binary download requires auth).
	filesByMsg := map[uuid.UUID][]model.ConversationFile{}
	if s.files != nil && len(msgs) > 0 {
		ids := make([]uuid.UUID, 0, len(msgs))
		for i := range msgs {
			ids = append(ids, msgs[i].ID)
		}
		filesByMsg, _ = s.files.ListByMessageIDs(ctx, row.TenantID, ids)
	}

	return &SharedView{
		Conversation: conv,
		Messages:     msgs,
		FilesByMsg:   filesByMsg,
		ExpiresAt:    row.ExpiresAt,
	}, nil
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// parseExpiresIn maps the wire string ("1h" / "24h" / "7d" /
// "never") onto an absolute deadline. Anything outside the
// allowlist returns ErrShareBadExpiry.
func parseExpiresIn(s string) (*time.Time, error) {
	switch s {
	case "", "never":
		return nil, nil
	case "1h":
		t := time.Now().Add(1 * time.Hour)
		return &t, nil
	case "24h":
		t := time.Now().Add(24 * time.Hour)
		return &t, nil
	case "7d":
		t := time.Now().Add(7 * 24 * time.Hour)
		return &t, nil
	default:
		return nil, fmt.Errorf("%w: %s", ErrShareBadExpiry, s)
	}
}

// newShareToken returns 22 chars of URL-safe base64 (no padding) —
// ~132 bits of entropy from 16 raw bytes. Collisions are
// astronomically unlikely; the UNIQUE constraint on share_token
// makes a re-attempt cheap if one ever happened.
func newShareToken() (string, error) {
	var buf [16]byte
	if _, err := rand.Read(buf[:]); err != nil {
		return "", fmt.Errorf("share token: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(buf[:]), nil
}
