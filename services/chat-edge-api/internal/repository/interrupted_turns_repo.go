package repository

// FIX131 E1 — the rows the interrupted-turn sweep reads and settles.
//
// A turn's assistant row is inserted with status 'streaming' and is
// settled by the finish callback of the process that ran it. When that
// process died first (a restart, a crash, a kill at the end of a stop
// grace period), or when the finish wrote nothing on purpose (FIX125 M2:
// the conversation was deleted while the turn ran), nothing ever settles
// the row: it says 'streaming' for good and a reload shows a spinner, or
// an error card over a row the list still calls streaming.
//
// These two queries are the database half of the sweep
// (service/interrupted_sweep.go). WHICH rows may be settled is decided
// there, from the run leases — a row is only settled when no live process
// holds its lease. Here: list the candidates, and settle a given set with
// the same guarded UPDATE MarkInterrupted uses, so a row that a live
// finish callback settled in the meantime keeps its own ending.

import (
	"context"
	"fmt"
	"time"

	"github.com/google/uuid"
)

// InterruptedCounts is what one MarkInterruptedMany call settled.
type InterruptedCounts struct {
	// Settled is the number of rows flipped from streaming to the
	// interrupted ending (rows another writer settled first are not
	// counted).
	Settled int
	// WithContent — settled rows that kept partial text.
	WithContent int
	// DeletedConversation — settled rows whose conversation was deleted.
	DeletedConversation int
	// IDs are the rows that were settled.
	IDs []uuid.UUID
}

// ListStreamingAssistantIDs returns assistant rows still marked streaming
// that were created before olderThan, oldest first, at most limit. Every
// tenant, deleted conversations included: a row nobody will ever finish
// is stuck wherever it lives.
func (r *MessageRepo) ListStreamingAssistantIDs(ctx context.Context, olderThan time.Time, limit int) ([]uuid.UUID, error) {
	if limit <= 0 {
		limit = 500
	}
	const q = `
		SELECT id FROM chat.messages_masked
		WHERE role = 'assistant' AND status = 'streaming' AND created_at < $1
		ORDER BY created_at
		LIMIT $2`
	rows, err := r.db.Query(ctx, q, olderThan, limit)
	if err != nil {
		return nil, fmt.Errorf("list streaming assistant rows: %w", err)
	}
	defer rows.Close()
	var out []uuid.UUID
	for rows.Next() {
		var id uuid.UUID
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scan streaming assistant row: %w", err)
		}
		out = append(out, id)
	}
	return out, rows.Err()
}

// MarkInterruptedMany settles the given rows as interrupted — status
// 'error', terminal_cause 'interrupted', error_code GENERATION_INTERRUPTED
// — exactly as MarkInterrupted does for one row. Guarded: only a row still
// 'streaming' flips, so a completed / cancelled / error ending written by a
// live finish callback is never overwritten. Content is never touched: the
// partial answer a person already read stays on the row.
func (r *MessageRepo) MarkInterruptedMany(ctx context.Context, ids []uuid.UUID) (InterruptedCounts, error) {
	var out InterruptedCounts
	if len(ids) == 0 {
		return out, nil
	}
	const q = `
		UPDATE chat.messages_masked AS m
		SET status = 'error', terminal_cause = 'interrupted', error_code = 'GENERATION_INTERRUPTED'
		WHERE m.id = ANY($1) AND m.role = 'assistant' AND m.status = 'streaming'
		RETURNING m.id,
		          m.content_masked <> '',
		          EXISTS (SELECT 1 FROM chat.conversations c WHERE c.id = m.conversation_id AND c.is_deleted)`
	rows, err := r.db.Query(ctx, q, ids)
	if err != nil {
		return out, fmt.Errorf("mark interrupted (many): %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var id uuid.UUID
		var withContent, deleted bool
		if err := rows.Scan(&id, &withContent, &deleted); err != nil {
			return out, fmt.Errorf("scan interrupted row: %w", err)
		}
		out.Settled++
		out.IDs = append(out.IDs, id)
		if withContent {
			out.WithContent++
		}
		if deleted {
			out.DeletedConversation++
		}
	}
	return out, rows.Err()
}
