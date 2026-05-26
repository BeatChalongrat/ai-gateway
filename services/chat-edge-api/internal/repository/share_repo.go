package repository

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// ShareRepo persists chat.shared_conversations.
type ShareRepo struct {
	db DB
}

func NewShareRepo(db *pgxpool.Pool) *ShareRepo {
	return &ShareRepo{db: db}
}

// Create inserts a new share row. Caller supplies the random token
// (so secret generation lives in one place — the service layer).
func (r *ShareRepo) Create(
	ctx context.Context,
	tenantID, convID uuid.UUID,
	token string,
	expiresAt *time.Time,
) (*model.SharedConversation, error) {
	const q = `
		INSERT INTO chat.shared_conversations
			(conversation_id, tenant_id, share_token, expires_at)
		VALUES ($1, $2, $3, $4)
		RETURNING id, created_at
	`
	s := &model.SharedConversation{
		ConversationID: convID,
		TenantID:       tenantID,
		ShareToken:     token,
		ExpiresAt:      expiresAt,
	}
	if err := r.db.QueryRow(ctx, q, convID, tenantID, token, expiresAt).Scan(
		&s.ID, &s.CreatedAt,
	); err != nil {
		return nil, fmt.Errorf("create share: %w", err)
	}
	return s, nil
}

// GetByConversation returns the share row for a conversation, if
// any. Used by the front-end to pre-fill the share modal.
func (r *ShareRepo) GetByConversation(
	ctx context.Context, tenantID, convID uuid.UUID,
) (*model.SharedConversation, error) {
	const q = `
		SELECT id, conversation_id, tenant_id, share_token, expires_at, created_at
		FROM chat.shared_conversations
		WHERE conversation_id = $1 AND tenant_id = $2
	`
	s := &model.SharedConversation{}
	err := r.db.QueryRow(ctx, q, convID, tenantID).Scan(
		&s.ID, &s.ConversationID, &s.TenantID, &s.ShareToken,
		&s.ExpiresAt, &s.CreatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get share by conv: %w", err)
	}
	return s, nil
}

// GetByToken is the load-bearing public lookup. The handler treats
// expiry filtering at the service layer so the row can still be
// surfaced to the owner (e.g. for revoke). NotFound when no row.
func (r *ShareRepo) GetByToken(
	ctx context.Context, token string,
) (*model.SharedConversation, error) {
	const q = `
		SELECT id, conversation_id, tenant_id, share_token, expires_at, created_at
		FROM chat.shared_conversations
		WHERE share_token = $1
	`
	s := &model.SharedConversation{}
	err := r.db.QueryRow(ctx, q, token).Scan(
		&s.ID, &s.ConversationID, &s.TenantID, &s.ShareToken,
		&s.ExpiresAt, &s.CreatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get share by token: %w", err)
	}
	return s, nil
}

// DeleteByConversation revokes the share link for a conversation.
// Returns ErrNotFound if no share existed (caller decides whether
// that's a 404 or a 204).
func (r *ShareRepo) DeleteByConversation(
	ctx context.Context, tenantID, convID uuid.UUID,
) error {
	const q = `
		DELETE FROM chat.shared_conversations
		WHERE conversation_id = $1 AND tenant_id = $2
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID)
	if err != nil {
		return fmt.Errorf("delete share: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}
