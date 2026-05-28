package repository

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type ConversationRepo struct {
	db DB
}

func NewConversationRepo(db *pgxpool.Pool) *ConversationRepo {
	return &ConversationRepo{db: db}
}

func (r *ConversationRepo) WithTx(tx pgx.Tx) *ConversationRepo {
	return &ConversationRepo{db: tx}
}

// Create inserts a new conversation. “message_count“ defaults to
// 0 via the column default; the title defaults to "New conversation"
// likewise. Callers should populate “tenant_id“, “user_id“,
// “model_id“ only; everything else is server-side.
func (r *ConversationRepo) Create(ctx context.Context, c *model.Conversation) (*model.Conversation, error) {
	const q = `
		INSERT INTO chat.conversations (tenant_id, user_id, model_id)
		VALUES ($1, $2, $3)
		RETURNING id, title, message_count, is_deleted, deleted_at, created_at, updated_at, knowledge_base_id, memory_enabled
	`
	err := r.db.QueryRow(ctx, q, c.TenantID, c.UserID, c.ModelID).Scan(
		&c.ID, &c.Title, &c.MessageCount, &c.IsDeleted,
		&c.DeletedAt, &c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
	)
	if err != nil {
		return nil, fmt.Errorf("create conversation: %w", err)
	}
	return c, nil
}

// GetByID returns the conversation only when the tenant scope
// matches AND the row isn't soft-deleted. The combined predicate
// keeps the ownership and lifecycle gates in a single round-trip
// so handlers can't accidentally skip either check.
func (r *ConversationRepo) GetByID(ctx context.Context, tenantID, convID uuid.UUID) (*model.Conversation, error) {
	const q = `
		SELECT id, tenant_id, user_id, title, model_id, message_count,
		       is_deleted, deleted_at, created_at, updated_at, knowledge_base_id, memory_enabled
		FROM chat.conversations
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	c := &model.Conversation{}
	err := r.db.QueryRow(ctx, q, convID, tenantID).Scan(
		&c.ID, &c.TenantID, &c.UserID, &c.Title, &c.ModelID,
		&c.MessageCount, &c.IsDeleted, &c.DeletedAt,
		&c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get conversation by id: %w", err)
	}
	return c, nil
}

// ListByUser returns the user's conversations sorted by
// “updated_at DESC“. Cursor-based pagination: the caller supplies
// the cursor row's ID; the query looks up that row's updated_at and
// returns the next “limit“ rows after it.
//
// Returns (rows, nextCursor, error). “nextCursor == uuid.Nil“ ⇒
// no more rows.
//
// Pagination predicate is a COMPOSITE “(updated_at, id)“ so rapid
// inserts that tie on microsecond-precision “updated_at“ don't get
// silently dropped. The ORDER BY mirrors the predicate to keep the
// scan deterministic and index-friendly.
func (r *ConversationRepo) ListByUser(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	limit int,
	cursor uuid.UUID,
) ([]model.Conversation, uuid.UUID, error) {
	// Resolve the cursor row's (updated_at, id) tuple so the WHERE
	// clause stays indexable. Empty cursor → start from the most-
	// recently-updated row.
	var cursorTs *string
	var cursorID *string
	if cursor != uuid.Nil {
		var ts string
		err := r.db.QueryRow(ctx,
			`SELECT updated_at::text FROM chat.conversations WHERE id = $1 AND tenant_id = $2`,
			cursor, tenantID,
		).Scan(&ts)
		if err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				// Unknown cursor → start from top.
				cursorTs = nil
			} else {
				return nil, uuid.Nil, fmt.Errorf("cursor lookup: %w", err)
			}
		} else {
			cursorTs = &ts
			cid := cursor.String()
			cursorID = &cid
		}
	}

	// limit+1 lets us tell the caller whether there's another page
	// without a second query. The (limit+1)th row becomes next_cursor.
	const q = `
		SELECT id, tenant_id, user_id, title, model_id, message_count,
		       is_deleted, deleted_at, created_at, updated_at, knowledge_base_id, memory_enabled
		FROM chat.conversations
		WHERE tenant_id = $1
		  AND user_id = $2
		  AND is_deleted = false
		  AND (
		    $3::timestamptz IS NULL
		    OR (updated_at, id) < ($3::timestamptz, $4::uuid)
		  )
		ORDER BY updated_at DESC, id DESC
		LIMIT $5
	`
	rows, err := r.db.Query(ctx, q, tenantID, userID, cursorTs, cursorID, limit+1)
	if err != nil {
		return nil, uuid.Nil, fmt.Errorf("list conversations: %w", err)
	}
	defer rows.Close()

	out := make([]model.Conversation, 0, limit)
	for rows.Next() {
		var c model.Conversation
		if err := rows.Scan(
			&c.ID, &c.TenantID, &c.UserID, &c.Title, &c.ModelID,
			&c.MessageCount, &c.IsDeleted, &c.DeletedAt,
			&c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
		); err != nil {
			return nil, uuid.Nil, fmt.Errorf("list scan: %w", err)
		}
		out = append(out, c)
	}
	if err := rows.Err(); err != nil {
		return nil, uuid.Nil, err
	}

	// has_more iff the limit+1 probe row came back. The cursor for
	// the next page MUST be the last SERVED row (out[limit-1]) — NOT
	// the probe row — otherwise the strict ``<`` predicate skips
	// the probe row on the next query and we silently drop one row
	// per page advance.
	var next uuid.UUID
	if len(out) > limit {
		out = out[:limit]
		next = out[limit-1].ID
	}
	return out, next, nil
}

// SearchByUser runs a server-side ILIKE on conversation titles. We
// don't bother with full-text indexes yet — for SaaS scale (<100k
// per tenant) ILIKE on the BTREE-indexed (tenant_id, user_id)
// partition is fast enough; a GIN trigram index is a future
// migration.
//
// Returns conversations sorted updated_at DESC, capped at “limit“.
func (r *ConversationRepo) SearchByUser(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	query string,
	limit int,
) ([]model.Conversation, error) {
	const q = `
		SELECT id, tenant_id, user_id, title, model_id, message_count,
		       is_deleted, deleted_at, created_at, updated_at, knowledge_base_id, memory_enabled
		FROM chat.conversations
		WHERE tenant_id = $1
		  AND user_id = $2
		  AND is_deleted = false
		  AND title ILIKE $3
		ORDER BY updated_at DESC, id DESC
		LIMIT $4
	`
	rows, err := r.db.Query(ctx, q, tenantID, userID, "%"+query+"%", limit)
	if err != nil {
		return nil, fmt.Errorf("search conversations: %w", err)
	}
	defer rows.Close()
	out := make([]model.Conversation, 0)
	for rows.Next() {
		var c model.Conversation
		if err := rows.Scan(
			&c.ID, &c.TenantID, &c.UserID, &c.Title, &c.ModelID,
			&c.MessageCount, &c.IsDeleted, &c.DeletedAt,
			&c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
		); err != nil {
			return nil, fmt.Errorf("search scan: %w", err)
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// UpdateTitle changes “title“ and bumps “updated_at“. The
// WHERE clause also enforces the tenant scope so a wrong tenant_id
// can't accidentally rename someone else's conversation.
func (r *ConversationRepo) UpdateTitle(ctx context.Context, tenantID, convID uuid.UUID, title string) error {
	const q = `
		UPDATE chat.conversations
		SET title = $3, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, title)
	if err != nil {
		return fmt.Errorf("update title: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// UpdateModel changes the conversation's default model_id and
// bumps “updated_at“. Tenant-scoped via the WHERE clause so a
// wrong tenant_id can't reassign someone else's conversation.
func (r *ConversationRepo) UpdateModel(ctx context.Context, tenantID, convID uuid.UUID, modelID string) error {
	const q = `
		UPDATE chat.conversations
		SET model_id = $3, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, modelID)
	if err != nil {
		return fmt.Errorf("update model: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// UpdateKnowledgeBase binds (or unbinds, with kbID = uuid.Nil) the
// conversation to a knowledge base. The FK on conversations enforces
// tenant-cross-pollination isolation at the service layer — the
// handler is expected to verify the KB belongs to the caller's
// tenant before invoking this method.
func (r *ConversationRepo) UpdateKnowledgeBase(
	ctx context.Context, tenantID, convID uuid.UUID, kbID uuid.UUID,
) error {
	const q = `
		UPDATE chat.conversations
		SET knowledge_base_id = $3, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	// uuid.Nil → NULL so callers can unbind via the same entry point.
	var arg any
	if kbID == uuid.Nil {
		arg = nil
	} else {
		arg = kbID
	}
	tag, err := r.db.Exec(ctx, q, convID, tenantID, arg)
	if err != nil {
		return fmt.Errorf("update knowledge_base_id: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// UpdateMemoryEnabled toggles the per-conversation cross-conversation
// memory layer (S1 — migration 014). Tenant-scoped via the WHERE
// clause; handler/service is expected to have asserted caller
// ownership first.
func (r *ConversationRepo) UpdateMemoryEnabled(
	ctx context.Context, tenantID, convID uuid.UUID, enabled bool,
) error {
	const q = `
		UPDATE chat.conversations
		SET memory_enabled = $3, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, enabled)
	if err != nil {
		return fmt.Errorf("update memory_enabled: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// SoftDelete sets “is_deleted = true“ + “deleted_at = NOW()“.
// Idempotent: re-deleting a soft-deleted row returns ErrNotFound
// (we treat the row as no-longer-present at this layer).
func (r *ConversationRepo) SoftDelete(ctx context.Context, tenantID, convID uuid.UUID) error {
	const q = `
		UPDATE chat.conversations
		SET is_deleted = true, deleted_at = NOW(), updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID)
	if err != nil {
		return fmt.Errorf("soft delete conversation: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// IncrementMessageCount bumps “message_count“ by “delta“ (can
// be negative if a regenerate flow needs to retract). Also touches
// “updated_at“ so the sidebar sort surfaces the active
// conversation. Best-effort logical update — does NOT verify
// ownership (the caller has already done so via GetByID).
func (r *ConversationRepo) IncrementMessageCount(ctx context.Context, convID uuid.UUID, delta int) error {
	const q = `
		UPDATE chat.conversations
		SET message_count = message_count + $2, updated_at = NOW()
		WHERE id = $1
	`
	if _, err := r.db.Exec(ctx, q, convID, delta); err != nil {
		return fmt.Errorf("increment message count: %w", err)
	}
	return nil
}

// UpdateTimestamp touches “updated_at“ without changing anything
// else. Used by the streaming path between user-message persist and
// assistant-message persist so the sidebar shows fresh activity even
// while the assistant reply is still generating.
func (r *ConversationRepo) UpdateTimestamp(ctx context.Context, convID uuid.UUID) error {
	const q = `UPDATE chat.conversations SET updated_at = NOW() WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, convID); err != nil {
		return fmt.Errorf("update timestamp: %w", err)
	}
	return nil
}
