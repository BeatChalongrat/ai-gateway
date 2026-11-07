package repository

// FIX132 B — the knowledge base's background indexing queue.
//
// knowledge_documents is the queue: 'pending' rows wait (until
// next_attempt_at, when set), a worker claims one with FOR UPDATE SKIP
// LOCKED in a single short statement (no transaction is held while a
// document is extracted or embedded — FIX129's lesson), and every later
// write by that worker is FENCED on the claim: `AND status = 'processing'
// AND claimed_at = <the worker's claim>`. claimed_at is renewed on every
// batch, so it doubles as a heartbeat. A write that matches no row means
// the document was deleted, re-indexed or reclaimed meanwhile; the worker
// stops without writing anything.
//
// Chunks are inserted in the same transaction as the fenced progress update
// that locks the document row, so a concurrent delete (which cascades to
// chunks) or re-index (which locks the row first, then deletes chunks)
// either happens before the batch — and the fence fails — or after it —
// and removes the batch with the rest. No orphan chunks either way.

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// ErrClaimLost — the document was deleted, re-indexed or reclaimed since
// this worker claimed it.
var ErrClaimLost = errors.New("knowledge: document claim lost")

// ClaimNextDocument claims the oldest pending document whose retry time has
// come: status → processing, claimed_at → now. nil, nil when there is none.
func (r *KnowledgeRepo) ClaimNextDocument(ctx context.Context) (*model.KnowledgeDocument, error) {
	q := `
		UPDATE chat.knowledge_documents
		SET status = 'processing', claimed_at = clock_timestamp(),
		    progress_done = 0, progress_total = 0, updated_at = NOW()
		WHERE id = (
			SELECT id FROM chat.knowledge_documents
			WHERE status = 'pending'
			  AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
			ORDER BY COALESCE(next_attempt_at, created_at), created_at
			LIMIT 1
			FOR UPDATE SKIP LOCKED
		)
		RETURNING ` + kbDocColumns
	d := &model.KnowledgeDocument{}
	if err := scanDoc(r.db.QueryRow(ctx, q), d); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		return nil, fmt.Errorf("claim knowledge document: %w", err)
	}
	return d, nil
}

// fenced runs one UPDATE on the claimed document and returns the renewed
// claim; ErrClaimLost when the claim no longer holds.
func (r *KnowledgeRepo) fenced(ctx context.Context, db DB, docID uuid.UUID, claim time.Time, set string, args ...any) (time.Time, error) {
	all := append([]any{docID, claim}, args...)
	q := `UPDATE chat.knowledge_documents SET ` + set + `, updated_at = NOW()
		WHERE id = $1 AND status = 'processing' AND claimed_at = $2
		RETURNING COALESCE(claimed_at, NOW())`
	var next time.Time
	if err := db.QueryRow(ctx, q, all...).Scan(&next); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return time.Time{}, ErrClaimLost
		}
		return time.Time{}, fmt.Errorf("knowledge document update: %w", err)
	}
	return next, nil
}

// Heartbeat renews the claim (before and after long steps such as OCR).
func (r *KnowledgeRepo) Heartbeat(ctx context.Context, docID uuid.UUID, claim time.Time) (time.Time, error) {
	return r.fenced(ctx, r.db, docID, claim, `claimed_at = clock_timestamp()`)
}

// SetProgressTotal records how many chunks the document will have.
func (r *KnowledgeRepo) SetProgressTotal(ctx context.Context, docID uuid.UUID, claim time.Time, total, chars int) (time.Time, error) {
	return r.fenced(ctx, r.db, docID, claim,
		`claimed_at = clock_timestamp(), progress_done = 0, progress_total = $3, char_count = $4`, total, chars)
}

// CommitIndexBatch stores one batch of embedded chunks and the progress it
// represents, in one transaction behind the fence. first = this is the
// document's first batch in this claim: chunks left by an interrupted
// earlier run are deleted first.
func (r *KnowledgeRepo) CommitIndexBatch(
	ctx context.Context, docID uuid.UUID, claim time.Time,
	chunks []model.KnowledgeChunk, done int, first bool,
) (time.Time, error) {
	if r.pool == nil {
		return time.Time{}, errors.New("knowledge repo: no pool for transactions")
	}
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return time.Time{}, fmt.Errorf("begin index batch: %w", err)
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	next, err := r.fenced(ctx, tx, docID, claim,
		`claimed_at = clock_timestamp(), progress_done = $3`, done)
	if err != nil {
		return time.Time{}, err
	}
	txr := &KnowledgeRepo{db: tx}
	if first {
		if err := txr.DeleteChunksForDocument(ctx, docID); err != nil {
			return time.Time{}, err
		}
	}
	if err := txr.InsertChunkBatch(ctx, chunks); err != nil {
		return time.Time{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return time.Time{}, fmt.Errorf("commit index batch: %w", err)
	}
	return next, nil
}

// FinishIndexed marks the document searchable.
func (r *KnowledgeRepo) FinishIndexed(ctx context.Context, docID uuid.UUID, claim time.Time, chunks, chars int) error {
	_, err := r.fenced(ctx, r.db, docID, claim, `
		status = 'indexed', chunk_count = $3, char_count = $4,
		progress_done = $3, progress_total = $3, indexed_at = NOW(),
		error_code = NULL, error_message = NULL, attempts = 0,
		next_attempt_at = NULL, claimed_at = NULL`, chunks, chars)
	return err
}

// FailDocument marks the document failed with a closed-set code and a
// sentence; its partial chunks go with it.
func (r *KnowledgeRepo) FailDocument(ctx context.Context, docID uuid.UUID, claim time.Time, code, message string) error {
	if r.pool == nil {
		return errors.New("knowledge repo: no pool for transactions")
	}
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	if _, err := r.fenced(ctx, tx, docID, claim, `
		status = 'failed', error_code = $3, error_message = $4, chunk_count = 0,
		progress_done = 0, next_attempt_at = NULL, claimed_at = NULL`, code, message); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `DELETE FROM chat.knowledge_chunks WHERE document_id = $1`, docID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// RetryLater hands the document back to the queue after kb-models could not
// serve it: pending again at next_attempt_at, attempts + countAttempt, with
// the reason kept in error_code / error_message so the list can say why it
// is waiting. Partial chunks are removed (the next run starts over).
func (r *KnowledgeRepo) RetryLater(ctx context.Context, docID uuid.UUID, claim time.Time, delay time.Duration, countAttempt bool, code, message string) error {
	if r.pool == nil {
		return errors.New("knowledge repo: no pool for transactions")
	}
	inc := 0
	if countAttempt {
		inc = 1
	}
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	if _, err := r.fenced(ctx, tx, docID, claim, `
		status = 'pending', attempts = COALESCE(attempts, 0) + $3,
		next_attempt_at = NOW() + make_interval(secs => $4),
		error_code = $5, error_message = $6, progress_done = 0, claimed_at = NULL`,
		inc, delay.Seconds(), code, message); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `DELETE FROM chat.knowledge_chunks WHERE document_id = $1`, docID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// ReleaseClaim hands a document back untouched (the process is shutting
// down): pending, no attempt counted, picked up by the next process at once.
func (r *KnowledgeRepo) ReleaseClaim(ctx context.Context, docID uuid.UUID, claim time.Time) error {
	_, err := r.fenced(ctx, r.db, docID, claim, `status = 'pending', progress_done = 0, claimed_at = NULL`)
	return err
}

// ReleaseStaleClaims sends 'processing' documents whose claim has not been
// renewed for olderThan (their worker died — a restart, a crash) back to
// 'pending'. Returns how many.
func (r *KnowledgeRepo) ReleaseStaleClaims(ctx context.Context, olderThan time.Duration) (int64, error) {
	tag, err := r.db.Exec(ctx, `
		UPDATE chat.knowledge_documents
		SET status = 'pending', claimed_at = NULL, progress_done = 0, updated_at = NOW()
		WHERE status = 'processing'
		  AND COALESCE(claimed_at, updated_at) < NOW() - make_interval(secs => $1)`,
		olderThan.Seconds())
	if err != nil {
		return 0, fmt.Errorf("release stale knowledge claims: %w", err)
	}
	return tag.RowsAffected(), nil
}

// ResetDocumentForReindex puts a document back in the queue from scratch:
// the row is locked FIRST (so an in-flight batch either commits before this
// and is deleted below, or fails its fence after), then its chunks go.
func (r *KnowledgeRepo) ResetDocumentForReindex(ctx context.Context, tenantID, docID uuid.UUID) error {
	if r.pool == nil {
		return errors.New("knowledge repo: no pool for transactions")
	}
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	tag, err := tx.Exec(ctx, `
		UPDATE chat.knowledge_documents
		SET status = 'pending', chunk_count = 0, progress_done = 0, progress_total = 0,
		    error_code = NULL, error_message = NULL, attempts = 0, next_attempt_at = NULL,
		    claimed_at = NULL, indexed_at = NULL, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2`, docID, tenantID)
	if err != nil {
		return fmt.Errorf("reset document: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	if _, err := tx.Exec(ctx, `DELETE FROM chat.knowledge_chunks WHERE document_id = $1`, docID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// IndexingCounts is how many of a knowledge base's documents are waiting,
// being indexed, or failed.
type IndexingCounts struct {
	Pending    int `json:"pending"`
	Processing int `json:"processing"`
	Failed     int `json:"failed"`
}

// CountIndexing returns the counts per knowledge base (absent = all zero).
func (r *KnowledgeRepo) CountIndexing(ctx context.Context, kbIDs []uuid.UUID) (map[uuid.UUID]IndexingCounts, error) {
	out := make(map[uuid.UUID]IndexingCounts, len(kbIDs))
	if len(kbIDs) == 0 {
		return out, nil
	}
	rows, err := r.db.Query(ctx, `
		SELECT knowledge_base_id,
		       COUNT(*) FILTER (WHERE status = 'pending'),
		       COUNT(*) FILTER (WHERE status = 'processing'),
		       COUNT(*) FILTER (WHERE status = 'failed')
		FROM chat.knowledge_documents
		WHERE knowledge_base_id = ANY($1)
		GROUP BY knowledge_base_id`, kbIDs)
	if err != nil {
		return nil, fmt.Errorf("count indexing: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var id uuid.UUID
		var c IndexingCounts
		if err := rows.Scan(&id, &c.Pending, &c.Processing, &c.Failed); err != nil {
			return nil, err
		}
		out[id] = c
	}
	return out, rows.Err()
}

// NotReadyDocuments returns how many of the knowledge base's documents are
// still pending or processing, and up to `limit` of their filenames (oldest
// first) — the "N files are still being processed" note.
func (r *KnowledgeRepo) NotReadyDocuments(ctx context.Context, kbID uuid.UUID, limit int) (int, []string, error) {
	rows, err := r.db.Query(ctx, `
		SELECT original_filename, COUNT(*) OVER ()
		FROM chat.knowledge_documents
		WHERE knowledge_base_id = $1 AND status IN ('pending', 'processing')
		ORDER BY created_at
		LIMIT $2`, kbID, max(limit, 1))
	if err != nil {
		return 0, nil, fmt.Errorf("not-ready documents: %w", err)
	}
	defer rows.Close()
	var names []string
	total := 0
	for rows.Next() {
		var name string
		if err := rows.Scan(&name, &total); err != nil {
			return 0, nil, err
		}
		if len(names) < limit {
			names = append(names, name)
		}
	}
	return total, names, rows.Err()
}
