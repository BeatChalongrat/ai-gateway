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

// ErrQuestionNotPending is returned by Claim when the row is not pending
// any more (already answered, cancelled, superseded, expired, or missing).
var ErrQuestionNotPending = errors.New("question is not pending")

// PendingQuestionRepo is the persistence for FIX74 parked turns
// (migration 042). Every state change is a single conditional UPDATE so
// two answers racing each other (two tabs) resolve at the database.
type PendingQuestionRepo struct {
	db DB
}

func NewPendingQuestionRepo(db *pgxpool.Pool) *PendingQuestionRepo {
	return &PendingQuestionRepo{db: db}
}

const pendingQuestionColumns = `id, tenant_id, user_id, conversation_id, message_id, tool_call_id,
	questions_json, state_json, status, created_at, expires_at, closed_at`

func scanPendingQuestion(row pgx.Row) (*model.PendingQuestion, error) {
	var q model.PendingQuestion
	if err := row.Scan(&q.ID, &q.TenantID, &q.UserID, &q.ConversationID, &q.MessageID, &q.ToolCallID,
		&q.Questions, &q.State, &q.Status, &q.CreatedAt, &q.ExpiresAt, &q.ClosedAt); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	return &q, nil
}

// Create parks a turn. The partial unique index refuses a second pending
// question in the same conversation — the caller treats that as "one card
// at a time" and answers the later call with the batch-contract text.
func (r *PendingQuestionRepo) Create(ctx context.Context, q *model.PendingQuestion) (*model.PendingQuestion, error) {
	if q.ID == uuid.Nil {
		q.ID = uuid.New()
	}
	if q.Status == "" {
		q.Status = model.PendingQuestionPending
	}
	const stmt = `
		INSERT INTO chat.pending_questions
			(id, tenant_id, user_id, conversation_id, message_id, tool_call_id,
			 questions_json, state_json, status, expires_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
		RETURNING ` + pendingQuestionColumns
	return scanPendingQuestion(r.db.QueryRow(ctx, stmt, q.ID, q.TenantID, q.UserID, q.ConversationID, q.MessageID,
		q.ToolCallID, q.Questions, q.State, q.Status, q.ExpiresAt))
}

func (r *PendingQuestionRepo) GetByID(ctx context.Context, tenantID, id uuid.UUID) (*model.PendingQuestion, error) {
	const stmt = `SELECT ` + pendingQuestionColumns + ` FROM chat.pending_questions WHERE id = $1 AND tenant_id = $2`
	return scanPendingQuestion(r.db.QueryRow(ctx, stmt, id, tenantID))
}

// PendingForConversation returns the conversation's pending question, or
// ErrNotFound. Expiry is the caller's to judge (IsPending(now)) so the
// read path can close a stale row honestly instead of hiding it.
func (r *PendingQuestionRepo) PendingForConversation(ctx context.Context, tenantID, convID uuid.UUID) (*model.PendingQuestion, error) {
	const stmt = `SELECT ` + pendingQuestionColumns + ` FROM chat.pending_questions
		WHERE conversation_id = $1 AND tenant_id = $2 AND status = 'pending'
		ORDER BY created_at DESC LIMIT 1`
	return scanPendingQuestion(r.db.QueryRow(ctx, stmt, convID, tenantID))
}

// Claim moves a pending, unexpired question to `status` (answered or
// skipped) and returns it. The conditional UPDATE is the whole two-tabs
// rule: whoever's UPDATE matches first wins; the other gets
// ErrQuestionNotPending.
func (r *PendingQuestionRepo) Claim(ctx context.Context, tenantID, id uuid.UUID, status string, now time.Time) (*model.PendingQuestion, error) {
	const stmt = `
		UPDATE chat.pending_questions SET status = $3, closed_at = $4
		WHERE id = $1 AND tenant_id = $2 AND status = 'pending' AND expires_at > $4
		RETURNING ` + pendingQuestionColumns
	q, err := scanPendingQuestion(r.db.QueryRow(ctx, stmt, id, tenantID, status, now))
	if errors.Is(err, ErrNotFound) {
		return nil, ErrQuestionNotPending
	}
	return q, err
}

// Close settles a pending row without an answer (cancelled / superseded /
// expired). Idempotent: a row that is no longer pending is left alone.
func (r *PendingQuestionRepo) Close(ctx context.Context, id uuid.UUID, status string, now time.Time) (bool, error) {
	const stmt = `UPDATE chat.pending_questions SET status = $2, closed_at = $3
		WHERE id = $1 AND status = 'pending'`
	tag, err := r.db.Exec(ctx, stmt, id, status, now)
	if err != nil {
		return false, fmt.Errorf("close pending question: %w", err)
	}
	return tag.RowsAffected() == 1, nil
}

// CloseForConversation settles whatever is pending in a conversation
// (a new message supersedes it; Stop cancels it). Returns the closed row
// so the caller can settle the assistant message too.
func (r *PendingQuestionRepo) CloseForConversation(ctx context.Context, tenantID, convID uuid.UUID, status string, now time.Time) (*model.PendingQuestion, error) {
	const stmt = `UPDATE chat.pending_questions SET status = $3, closed_at = $4
		WHERE conversation_id = $1 AND tenant_id = $2 AND status = 'pending'
		RETURNING ` + pendingQuestionColumns
	return scanPendingQuestion(r.db.QueryRow(ctx, stmt, convID, tenantID, status, now))
}
