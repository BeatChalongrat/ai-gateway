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
// (so secret generation lives in one place — the service layer) and
// the snapshot: the message ids frozen at share time (T3).
func (r *ShareRepo) Create(
	ctx context.Context,
	tenantID, convID uuid.UUID,
	token string,
	expiresAt *time.Time,
	snapshotIDs []uuid.UUID,
) (*model.SharedConversation, error) {
	const q = `
		INSERT INTO chat.shared_conversations
			(conversation_id, tenant_id, share_token, expires_at, snapshot_message_ids)
		VALUES ($1, $2, $3, $4, $5::uuid[])
		RETURNING id, created_at
	`
	s := &model.SharedConversation{
		ConversationID:     convID,
		TenantID:           tenantID,
		ShareToken:         token,
		ExpiresAt:          expiresAt,
		SnapshotMessageIDs: snapshotIDs,
	}
	if err := r.db.QueryRow(ctx, q, convID, tenantID, token, expiresAt,
		uuidStrings(snapshotIDs)).Scan(
		&s.ID, &s.CreatedAt,
	); err != nil {
		return nil, fmt.Errorf("create share: %w", err)
	}
	return s, nil
}

// SetSnapshot backfills the frozen id list onto a legacy (pre-033)
// row that the migration backfill missed — the Resolve path's lazy
// self-heal.
func (r *ShareRepo) SetSnapshot(
	ctx context.Context, shareID uuid.UUID, snapshotIDs []uuid.UUID,
) error {
	const q = `
		UPDATE chat.shared_conversations
		SET snapshot_message_ids = $2::uuid[]
		WHERE id = $1
	`
	if _, err := r.db.Exec(ctx, q, shareID, uuidStrings(snapshotIDs)); err != nil {
		return fmt.Errorf("set share snapshot: %w", err)
	}
	return nil
}

// uuidStrings renders ids as text for a $n::uuid[] parameter — pgx
// encodes []string arrays natively; google/uuid slices would need a
// registered codec.
func uuidStrings(ids []uuid.UUID) []string {
	out := make([]string, len(ids))
	for i, id := range ids {
		out[i] = id.String()
	}
	return out
}

// scanSnapshotIDs converts the text[]-projected snapshot column back
// into ids. A NULL column scans as a nil []string (legacy pre-033
// row) and stays nil, so Resolve can tell "no snapshot yet" from
// "snapshot of an empty conversation" ('{}' → empty non-nil).
func scanSnapshotIDs(raw []string) ([]uuid.UUID, error) {
	if raw == nil {
		return nil, nil
	}
	out := make([]uuid.UUID, 0, len(raw))
	for _, s := range raw {
		id, err := uuid.Parse(s)
		if err != nil {
			return nil, fmt.Errorf("snapshot id %q: %w", s, err)
		}
		out = append(out, id)
	}
	return out, nil
}

// GetByConversation returns the share row for a conversation, if
// any. Used by the front-end to pre-fill the share modal.
func (r *ShareRepo) GetByConversation(
	ctx context.Context, tenantID, convID uuid.UUID,
) (*model.SharedConversation, error) {
	const q = `
		SELECT id, conversation_id, tenant_id, share_token, expires_at, created_at,
		       snapshot_message_ids::text[]
		FROM chat.shared_conversations
		WHERE conversation_id = $1 AND tenant_id = $2
	`
	s := &model.SharedConversation{}
	var rawSnapshot []string
	err := r.db.QueryRow(ctx, q, convID, tenantID).Scan(
		&s.ID, &s.ConversationID, &s.TenantID, &s.ShareToken,
		&s.ExpiresAt, &s.CreatedAt, &rawSnapshot,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get share by conv: %w", err)
	}
	if s.SnapshotMessageIDs, err = scanSnapshotIDs(rawSnapshot); err != nil {
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
		SELECT id, conversation_id, tenant_id, share_token, expires_at, created_at,
		       snapshot_message_ids::text[]
		FROM chat.shared_conversations
		WHERE share_token = $1
	`
	s := &model.SharedConversation{}
	var rawSnapshot []string
	err := r.db.QueryRow(ctx, q, token).Scan(
		&s.ID, &s.ConversationID, &s.TenantID, &s.ShareToken,
		&s.ExpiresAt, &s.CreatedAt, &rawSnapshot,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get share by token: %w", err)
	}
	if s.SnapshotMessageIDs, err = scanSnapshotIDs(rawSnapshot); err != nil {
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
