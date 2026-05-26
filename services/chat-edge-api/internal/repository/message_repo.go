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

// MessageRepo persists conversation messages.
//
// The table is named ``chat.messages_masked`` for schema lineage —
// the original gateway design stored masked-only content. Web Chat
// mode stores REAL content in the same column; the rename to
// ``content`` is a Phase 2 migration. The column name on disk is
// ``content_masked`` to keep migrations forward-compatible.
type MessageRepo struct {
	db DB
}

func NewMessageRepo(db *pgxpool.Pool) *MessageRepo {
	return &MessageRepo{db: db}
}

func (r *MessageRepo) WithTx(tx pgx.Tx) *MessageRepo {
	return &MessageRepo{db: tx}
}

// Create inserts a new message row. ``ContentMasked`` carries the
// real text in Web Chat mode; the column rename is deferred to keep
// schemas backward-compatible with the gateway-only build.
func (r *MessageRepo) Create(ctx context.Context, m *model.Message) (*model.Message, error) {
	const q = `
		INSERT INTO chat.messages_masked
			(conversation_id, tenant_id, user_id, role, content_masked,
			 status, model_id, token_usage_input, token_usage_output, secure_trace_id)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
		RETURNING id, created_at
	`
	err := r.db.QueryRow(ctx, q,
		m.ConversationID, m.TenantID, m.UserID, m.Role, m.ContentMasked,
		m.Status, m.ModelID, m.TokenUsageInput, m.TokenUsageOutput, m.SecureTraceID,
	).Scan(&m.ID, &m.CreatedAt)
	if err != nil {
		return nil, fmt.Errorf("create message: %w", err)
	}
	return m, nil
}

// ListByConversation returns the message history for one
// conversation, ordered ASCENDING by ``created_at`` so the chat UI
// renders oldest-first. ``cursor`` is used for backwards-paging
// (load older messages); empty cursor → latest page.
//
// Returns (rows, hasMore, error).
func (r *MessageRepo) ListByConversation(
	ctx context.Context,
	tenantID, convID uuid.UUID,
	limit int,
	cursor uuid.UUID,
) ([]model.Message, bool, error) {
	// Composite cursor (created_at, id) so rows that tie on the
	// microsecond timestamp aren't silently dropped between pages.
	var cursorTs *string
	var cursorID *string
	if cursor != uuid.Nil {
		var ts string
		err := r.db.QueryRow(ctx,
			`SELECT created_at::text FROM chat.messages_masked WHERE id = $1 AND tenant_id = $2`,
			cursor, tenantID,
		).Scan(&ts)
		if err != nil {
			if !errors.Is(err, pgx.ErrNoRows) {
				return nil, false, fmt.Errorf("cursor lookup: %w", err)
			}
			cursorTs = nil
		} else {
			cursorTs = &ts
			cid := cursor.String()
			cursorID = &cid
		}
	}

	// limit+1 lets us tell ``has_more``. The cursor for the next
	// page is the LAST served row (see conversation_repo for the
	// rationale) — never the probe row.
	const q = `
		SELECT id, conversation_id, tenant_id, user_id, role, content_masked,
		       status, model_id, token_usage_input, token_usage_output,
		       secure_trace_id, created_at
		FROM chat.messages_masked
		WHERE conversation_id = $1
		  AND tenant_id = $2
		  AND (
		    $3::timestamptz IS NULL
		    OR (created_at, id) < ($3::timestamptz, $4::uuid)
		  )
		ORDER BY created_at DESC, id DESC
		LIMIT $5
	`
	rows, err := r.db.Query(ctx, q, convID, tenantID, cursorTs, cursorID, limit+1)
	if err != nil {
		return nil, false, fmt.Errorf("list messages: %w", err)
	}
	defer rows.Close()

	out := make([]model.Message, 0, limit)
	for rows.Next() {
		var m model.Message
		if err := rows.Scan(
			&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
			&m.ContentMasked, &m.Status, &m.ModelID,
			&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
			&m.CreatedAt,
		); err != nil {
			return nil, false, fmt.Errorf("list scan: %w", err)
		}
		out = append(out, m)
	}
	if err := rows.Err(); err != nil {
		return nil, false, err
	}

	hasMore := len(out) > limit
	if hasMore {
		out = out[:limit]
	}
	// Flip to ascending for the wire response.
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, hasMore, nil
}

// ListContextByConversation is a tighter variant used by the
// MessageService to build the conversation context that ships to
// the Gateway. Returns the most recent ``limit`` messages in
// CHRONOLOGICAL order (oldest → newest) and includes all statuses
// so a paused / cancelled mid-stream still gets accounted for in
// future turns. Limit is hard-capped at 200.
func (r *MessageRepo) ListContextByConversation(
	ctx context.Context,
	tenantID, convID uuid.UUID,
	limit int,
) ([]model.Message, error) {
	if limit > 200 {
		limit = 200
	}
	const q = `
		SELECT id, conversation_id, tenant_id, user_id, role, content_masked,
		       status, model_id, token_usage_input, token_usage_output,
		       secure_trace_id, created_at
		FROM chat.messages_masked
		WHERE conversation_id = $1 AND tenant_id = $2
		ORDER BY created_at DESC
		LIMIT $3
	`
	rows, err := r.db.Query(ctx, q, convID, tenantID, limit)
	if err != nil {
		return nil, fmt.Errorf("list context messages: %w", err)
	}
	defer rows.Close()

	out := make([]model.Message, 0, limit)
	for rows.Next() {
		var m model.Message
		if err := rows.Scan(
			&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
			&m.ContentMasked, &m.Status, &m.ModelID,
			&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
			&m.CreatedAt,
		); err != nil {
			return nil, fmt.Errorf("context scan: %w", err)
		}
		out = append(out, m)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, nil
}

// GetByID returns a single message scoped by tenant. Used by the
// regenerate flow to look up the target assistant row.
func (r *MessageRepo) GetByID(ctx context.Context, tenantID, msgID uuid.UUID) (*model.Message, error) {
	const q = `
		SELECT id, conversation_id, tenant_id, user_id, role, content_masked,
		       status, model_id, token_usage_input, token_usage_output,
		       secure_trace_id, created_at
		FROM chat.messages_masked
		WHERE id = $1 AND tenant_id = $2
	`
	m := &model.Message{}
	err := r.db.QueryRow(ctx, q, msgID, tenantID).Scan(
		&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
		&m.ContentMasked, &m.Status, &m.ModelID,
		&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
		&m.CreatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get message by id: %w", err)
	}
	return m, nil
}

// UpdateStatus flips a single message's status. Used by regenerate
// (status=regenerated on the superseded assistant row). The status
// column is CHECK-constrained at the DB level — invalid values
// raise a 23514 violation that the caller surfaces as a 500.
func (r *MessageRepo) UpdateStatus(ctx context.Context, msgID uuid.UUID, status string) error {
	const q = `UPDATE chat.messages_masked SET status = $2 WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, msgID, status); err != nil {
		return fmt.Errorf("update message status: %w", err)
	}
	return nil
}

// ListContextBeforeMessage returns every message created strictly
// BEFORE ``before``, in chronological order, capped at ``limit``.
// Used by regenerate to rebuild the gateway context up to (but
// excluding) the assistant message being replaced.
func (r *MessageRepo) ListContextBeforeMessage(
	ctx context.Context,
	tenantID, convID uuid.UUID,
	before time.Time,
	limit int,
) ([]model.Message, error) {
	if limit <= 0 {
		limit = 50
	}
	if limit > 200 {
		limit = 200
	}
	const q = `
		SELECT id, conversation_id, tenant_id, user_id, role, content_masked,
		       status, model_id, token_usage_input, token_usage_output,
		       secure_trace_id, created_at
		FROM chat.messages_masked
		WHERE conversation_id = $1 AND tenant_id = $2 AND created_at < $3
		ORDER BY created_at DESC
		LIMIT $4
	`
	rows, err := r.db.Query(ctx, q, convID, tenantID, before, limit)
	if err != nil {
		return nil, fmt.Errorf("list context before message: %w", err)
	}
	defer rows.Close()

	out := make([]model.Message, 0, limit)
	for rows.Next() {
		var m model.Message
		if err := rows.Scan(
			&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
			&m.ContentMasked, &m.Status, &m.ModelID,
			&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
			&m.CreatedAt,
		); err != nil {
			return nil, fmt.Errorf("context before scan: %w", err)
		}
		out = append(out, m)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, nil
}

// UpdateContent overwrites the content + status + token usage of
// an existing row. Used by the streaming path to finalize the
// in-flight assistant message after the stream completes (it was
// inserted with status="streaming" + empty content for the
// streaming-start log; on completion we replace those fields with
// the accumulated content and final usage).
func (r *MessageRepo) UpdateContent(
	ctx context.Context,
	msgID uuid.UUID,
	content, status string,
	tokenInput, tokenOutput *int,
) error {
	const q = `
		UPDATE chat.messages_masked
		SET content_masked = $2, status = $3,
		    token_usage_input = $4, token_usage_output = $5
		WHERE id = $1
	`
	if _, err := r.db.Exec(ctx, q, msgID, content, status, tokenInput, tokenOutput); err != nil {
		return fmt.Errorf("update message content: %w", err)
	}
	return nil
}
