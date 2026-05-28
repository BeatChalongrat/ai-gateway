// ResponseContextSourceRepo persists chat.response_context_sources
// (migration 017 — S7).
//
// Two read patterns dominate:
//   - "all sources for one message" — drives the per-message
//     /messages/{id}/sources surface (if added later)
//   - "all sources for these N messages" — drives the inline
//     enrichment on the conversation message-list endpoint so we
//     don't pay an N+1 cost rendering a 100-message thread.
//
// Every read is hard-scoped by (user_id, tenant_id) per the S0
// ownership-leak audit protocol.
package repository

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type ResponseContextSourceRepo struct {
	db DB
}

func NewResponseContextSourceRepo(db *pgxpool.Pool) *ResponseContextSourceRepo {
	return &ResponseContextSourceRepo{db: db}
}

func (r *ResponseContextSourceRepo) WithTx(tx pgx.Tx) *ResponseContextSourceRepo {
	return &ResponseContextSourceRepo{db: tx}
}

const responseSourceColumns = `
	id, response_message_id, tenant_id, user_id, conversation_id,
	source_type, source_id, display_label, metadata, created_at
`

func scanResponseSource(row pgx.Row, s *model.ResponseContextSource) error {
	var meta []byte
	if err := row.Scan(
		&s.ID, &s.ResponseMessageID, &s.TenantID, &s.UserID, &s.ConversationID,
		&s.SourceType, &s.SourceID, &s.DisplayLabel, &meta, &s.CreatedAt,
	); err != nil {
		return err
	}
	if len(meta) > 0 {
		s.Metadata = json.RawMessage(meta)
	}
	return nil
}

// Create inserts one source row. Caller pre-populates the
// identity + scope fields; we just persist.
func (r *ResponseContextSourceRepo) Create(
	ctx context.Context, s *model.ResponseContextSource,
) error {
	const q = `
		INSERT INTO chat.response_context_sources
			(response_message_id, tenant_id, user_id, conversation_id,
			 source_type, source_id, display_label, metadata)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
		RETURNING id, created_at
	`
	var metaArg any
	if len(s.Metadata) > 0 {
		metaArg = string(s.Metadata)
	}
	if err := r.db.QueryRow(ctx, q,
		s.ResponseMessageID, s.TenantID, s.UserID, s.ConversationID,
		s.SourceType, s.SourceID, s.DisplayLabel, metaArg,
	).Scan(&s.ID, &s.CreatedAt); err != nil {
		return fmt.Errorf("create response source: %w", err)
	}
	return nil
}

// CreateBatch persists many sources in a single transaction-free
// loop. The write path in MessageService.finish is best-effort —
// a single failed source shouldn't block the rest of the batch
// from landing, but logging is up to the caller.
//
// Returns the first error encountered (subsequent inserts still
// attempted) so the caller can decide whether to warn.
func (r *ResponseContextSourceRepo) CreateBatch(
	ctx context.Context, sources []model.ResponseContextSource,
) error {
	var first error
	for i := range sources {
		if err := r.Create(ctx, &sources[i]); err != nil && first == nil {
			first = err
		}
	}
	return first
}

// ListByMessage returns every source row tied to one assistant
// message. Hard-scoped by user_id + tenant_id so a leaked id
// from another user can't surface.
func (r *ResponseContextSourceRepo) ListByMessage(
	ctx context.Context, messageID, userID, tenantID uuid.UUID,
) ([]model.ResponseContextSource, error) {
	q := `SELECT ` + responseSourceColumns + `
	      FROM chat.response_context_sources
	      WHERE response_message_id = $1 AND user_id = $2 AND tenant_id = $3
	      ORDER BY created_at ASC`
	rows, err := r.db.Query(ctx, q, messageID, userID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list response sources: %w", err)
	}
	defer rows.Close()
	out := make([]model.ResponseContextSource, 0)
	for rows.Next() {
		var s model.ResponseContextSource
		if err := scanResponseSource(rows, &s); err != nil {
			return nil, fmt.Errorf("list response sources scan: %w", err)
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

// ListByMessages returns sources keyed by message id — the
// batch-friendly variant the message-list handler uses to avoid
// N+1 reads when rendering a long conversation. Empty input slice
// returns an empty map without hitting the DB.
func (r *ResponseContextSourceRepo) ListByMessages(
	ctx context.Context, messageIDs []uuid.UUID, userID, tenantID uuid.UUID,
) (map[uuid.UUID][]model.ResponseContextSource, error) {
	out := make(map[uuid.UUID][]model.ResponseContextSource)
	if len(messageIDs) == 0 {
		return out, nil
	}
	q := `SELECT ` + responseSourceColumns + `
	      FROM chat.response_context_sources
	      WHERE response_message_id = ANY($1)
	        AND user_id = $2
	        AND tenant_id = $3
	      ORDER BY response_message_id, created_at ASC`
	rows, err := r.db.Query(ctx, q, messageIDs, userID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list response sources batch: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var s model.ResponseContextSource
		if err := scanResponseSource(rows, &s); err != nil {
			return nil, fmt.Errorf("list response sources batch scan: %w", err)
		}
		out[s.ResponseMessageID] = append(out[s.ResponseMessageID], s)
	}
	return out, rows.Err()
}

// HardDeleteAllByUser is the GDPR right-to-erasure cascade for
// response_context_sources. Issues a DELETE for every row
// belonging to the user — the S9 purge flow calls this alongside
// the memory + memory-event + state purges.
func (r *ResponseContextSourceRepo) HardDeleteAllByUser(
	ctx context.Context, userID, tenantID uuid.UUID,
) (int, error) {
	const q = `DELETE FROM chat.response_context_sources WHERE user_id = $1 AND tenant_id = $2`
	tag, err := r.db.Exec(ctx, q, userID, tenantID)
	if err != nil {
		return 0, fmt.Errorf("hard delete response sources: %w", err)
	}
	return int(tag.RowsAffected()), nil
}

// DeleteByMessage clears the source rows for one message. Called
// from the message-delete cascade hook (when added). Tenant-
// scoped via the WHERE clause.
func (r *ResponseContextSourceRepo) DeleteByMessage(
	ctx context.Context, messageID, tenantID uuid.UUID,
) error {
	const q = `
		DELETE FROM chat.response_context_sources
		WHERE response_message_id = $1 AND tenant_id = $2
	`
	if _, err := r.db.Exec(ctx, q, messageID, tenantID); err != nil {
		return fmt.Errorf("delete response sources: %w", err)
	}
	return nil
}
