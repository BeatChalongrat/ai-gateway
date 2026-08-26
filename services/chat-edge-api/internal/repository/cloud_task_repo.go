// CloudTaskRepo persists chat.cloud_scheduled_tasks + chat.cloud_task_runs
// (migration 035 — FIX13 cloud scheduled tasks).
//
// SECURITY MODEL — two access shapes:
//
//   - Every USER-FACING method (the management surface + audit display)
//     filters by (tenant_id, user_id); a task is private to its owner and
//     a leaked id from another user is indistinguishable from a missing
//     row. These back the authenticated CRUD handlers.
//
//   - ListDue is the ONE method with NO user scope: it is the scheduler's
//     cross-user due scan. It is never reachable from an HTTP handler —
//     only the in-process Tick loop calls it.
//
// The stored Prompt is plain text handed to the turn runner; nothing here
// (or downstream) executes it. RecordRun is append-only audit.
package repository

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type CloudTaskRepo struct {
	db DB
}

func NewCloudTaskRepo(db *pgxpool.Pool) *CloudTaskRepo {
	return &CloudTaskRepo{db: db}
}

func (r *CloudTaskRepo) WithTx(tx pgx.Tx) *CloudTaskRepo {
	return &CloudTaskRepo{db: tx}
}

// ErrInvalidCloudTask is returned by Create when a task fails the size /
// interval caps. The handler maps it to 400.
var ErrInvalidCloudTask = errors.New("repository: invalid cloud task")

const cloudTaskColumns = `
	id, tenant_id, user_id, conversation_id,
	name, prompt,
	schedule_kind, interval_seconds,
	next_run_at, enabled,
	max_runs, run_count,
	last_run_at, paused_reason,
	created_at, updated_at
`

func scanCloudTask(row pgx.Row, t *model.CloudTask) error {
	return row.Scan(
		&t.ID, &t.TenantID, &t.UserID, &t.ConversationID,
		&t.Name, &t.Prompt,
		&t.ScheduleKind, &t.IntervalSeconds,
		&t.NextRunAt, &t.Enabled,
		&t.MaxRuns, &t.RunCount,
		&t.LastRunAt, &t.PausedReason,
		&t.CreatedAt, &t.UpdatedAt,
	)
}

// validateCloudTaskCaps enforces the same caps the migration's CHECK
// constraints do (prompt length, name length, 1-hour interval floor) so a
// bad task is rejected with a clear error rather than a raw constraint
// violation. Defensive: the handler validates too, but the repo is last.
func validateCloudTaskCaps(t *model.CloudTask) error {
	if n := strings.TrimSpace(t.Name); n == "" || len(t.Name) > model.CloudTaskNameMaxLen {
		return fmt.Errorf("%w: name must be 1-%d chars", ErrInvalidCloudTask, model.CloudTaskNameMaxLen)
	}
	if l := strings.TrimSpace(t.Prompt); l == "" || len(t.Prompt) > model.CloudTaskPromptMaxLen {
		return fmt.Errorf("%w: prompt must be 1-%d chars", ErrInvalidCloudTask, model.CloudTaskPromptMaxLen)
	}
	if t.IntervalSeconds < model.CloudTaskMinIntervalSeconds {
		return fmt.Errorf("%w: interval_seconds must be >= %d (1-hour floor)", ErrInvalidCloudTask, model.CloudTaskMinIntervalSeconds)
	}
	if t.MaxRuns != nil && *t.MaxRuns <= 0 {
		return fmt.Errorf("%w: max_runs must be > 0 when set", ErrInvalidCloudTask)
	}
	return nil
}

// Create inserts a new task and returns the post-insert row. Enforces the
// caps defensively before touching SQL. tenant_id/user_id come from the
// authenticated caller (never the request body) at the handler.
func (r *CloudTaskRepo) Create(ctx context.Context, t *model.CloudTask) (*model.CloudTask, error) {
	if err := validateCloudTaskCaps(t); err != nil {
		return nil, err
	}
	if strings.TrimSpace(t.ScheduleKind) == "" {
		t.ScheduleKind = "interval"
	}
	const q = `
		INSERT INTO chat.cloud_scheduled_tasks (
			tenant_id, user_id, conversation_id,
			name, prompt, schedule_kind, interval_seconds,
			next_run_at, enabled, max_runs
		)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
		RETURNING ` + cloudTaskColumns
	out := &model.CloudTask{}
	err := scanCloudTask(r.db.QueryRow(ctx, q,
		t.TenantID, t.UserID, t.ConversationID,
		strings.TrimSpace(t.Name), t.Prompt, t.ScheduleKind, t.IntervalSeconds,
		t.NextRunAt, t.Enabled, t.MaxRuns,
	), out)
	if err != nil {
		return nil, fmt.Errorf("create cloud task: %w", err)
	}
	return out, nil
}

// ListByUser returns ALL of the user's tasks, newest first. Hard-scoped.
func (r *CloudTaskRepo) ListByUser(
	ctx context.Context, tenantID, userID uuid.UUID,
) ([]model.CloudTask, error) {
	q := `SELECT ` + cloudTaskColumns + `
	      FROM chat.cloud_scheduled_tasks
	      WHERE tenant_id = $1 AND user_id = $2
	      ORDER BY created_at DESC`
	rows, err := r.db.Query(ctx, q, tenantID, userID)
	if err != nil {
		return nil, fmt.Errorf("list cloud tasks: %w", err)
	}
	defer rows.Close()
	out := make([]model.CloudTask, 0, 8)
	for rows.Next() {
		var t model.CloudTask
		if err := scanCloudTask(rows, &t); err != nil {
			return nil, fmt.Errorf("list cloud tasks scan: %w", err)
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

// GetByID returns a single task, hard-scoped so a leaked id from another
// user returns ErrNotFound.
func (r *CloudTaskRepo) GetByID(
	ctx context.Context, tenantID, userID, id uuid.UUID,
) (*model.CloudTask, error) {
	q := `SELECT ` + cloudTaskColumns + `
	      FROM chat.cloud_scheduled_tasks
	      WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	t := &model.CloudTask{}
	if err := scanCloudTask(r.db.QueryRow(ctx, q, id, tenantID, userID), t); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get cloud task: %w", err)
	}
	return t, nil
}

// SetEnabled toggles a task's enabled flag. Enabling clears any auto-pause
// reason. Tenant + user scoped.
func (r *CloudTaskRepo) SetEnabled(
	ctx context.Context, tenantID, userID, id uuid.UUID, enabled bool,
) error {
	const q = `
		UPDATE chat.cloud_scheduled_tasks
		SET enabled = $4,
		    paused_reason = CASE WHEN $4 THEN NULL ELSE paused_reason END
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	tag, err := r.db.Exec(ctx, q, id, tenantID, userID, enabled)
	if err != nil {
		return fmt.Errorf("set cloud task enabled: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// SetPaused disables a task and records WHY (revoked | max_runs). Called
// only by the runner, which passes the task's own (tenant, user) — so the
// write stays hard-scoped even though the trigger is scheduler-internal.
func (r *CloudTaskRepo) SetPaused(
	ctx context.Context, tenantID, userID, id uuid.UUID, reason string,
) error {
	const q = `
		UPDATE chat.cloud_scheduled_tasks
		SET enabled = false, paused_reason = $4
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	tag, err := r.db.Exec(ctx, q, id, tenantID, userID, reason)
	if err != nil {
		return fmt.Errorf("set cloud task paused: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// Delete HARD-deletes a task. Tenant + user scoped so a leaked id can't
// remove another user's task. Audit rows in cloud_task_runs are left
// intact (append-only history outlives the task).
func (r *CloudTaskRepo) Delete(
	ctx context.Context, tenantID, userID, id uuid.UUID,
) error {
	const q = `DELETE FROM chat.cloud_scheduled_tasks
	           WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	tag, err := r.db.Exec(ctx, q, id, tenantID, userID)
	if err != nil {
		return fmt.Errorf("delete cloud task: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// ListDue is the SCHEDULER'S DUE SCAN — the one deliberately unscoped read.
// It returns enabled tasks whose next_run_at has passed, oldest-due first,
// capped at `limit`. NEVER call this from an HTTP handler; it crosses users
// by design and is only invoked by the in-process Tick loop.
func (r *CloudTaskRepo) ListDue(
	ctx context.Context, now time.Time, limit int,
) ([]model.CloudTask, error) {
	if limit <= 0 {
		limit = 100
	}
	q := `SELECT ` + cloudTaskColumns + `
	      FROM chat.cloud_scheduled_tasks
	      WHERE enabled = true AND next_run_at <= $1
	      ORDER BY next_run_at ASC
	      LIMIT $2`
	rows, err := r.db.Query(ctx, q, now, limit)
	if err != nil {
		return nil, fmt.Errorf("list due cloud tasks: %w", err)
	}
	defer rows.Close()
	out := make([]model.CloudTask, 0, limit)
	for rows.Next() {
		var t model.CloudTask
		if err := scanCloudTask(rows, &t); err != nil {
			return nil, fmt.Errorf("list due scan: %w", err)
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

// AdvanceAfterRun moves next_run_at forward and (optionally) bumps
// run_count + last_run_at after a successful fire. Hard-scoped by the
// task's own (tenant, user).
func (r *CloudTaskRepo) AdvanceAfterRun(
	ctx context.Context, tenantID, userID, taskID uuid.UUID, nextRunAt time.Time, incRun bool,
) error {
	const q = `
		UPDATE chat.cloud_scheduled_tasks
		SET next_run_at = $4,
		    run_count   = run_count + CASE WHEN $5 THEN 1 ELSE 0 END,
		    last_run_at = CASE WHEN $5 THEN now() ELSE last_run_at END
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	tag, err := r.db.Exec(ctx, q, taskID, tenantID, userID, nextRunAt, incRun)
	if err != nil {
		return fmt.Errorf("advance cloud task: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// RecordRun appends one audit row. Append-only: every fire (allowed,
// refused, or errored) writes exactly one row. Never carries a raw
// secret/token — error_detail is bounded + sanitized by the caller.
func (r *CloudTaskRepo) RecordRun(ctx context.Context, run *model.CloudTaskRun) error {
	const q = `
		INSERT INTO chat.cloud_task_runs (
			task_id, tenant_id, user_id, conversation_id,
			outcome, skip_reason, assistant_message_id, policy_action, tokens, error_detail
		)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`
	_, err := r.db.Exec(ctx, q,
		run.TaskID, run.TenantID, run.UserID, run.ConversationID,
		run.Outcome, run.SkipReason, run.AssistantMessageID, run.PolicyAction, run.Tokens, run.ErrorDetail,
	)
	if err != nil {
		return fmt.Errorf("record cloud task run: %w", err)
	}
	return nil
}

const cloudRunColumns = `
	id, task_id, tenant_id, user_id, conversation_id,
	fired_at, outcome, skip_reason,
	assistant_message_id, policy_action, tokens, error_detail
`

func scanCloudRun(row pgx.Row, r *model.CloudTaskRun) error {
	return row.Scan(
		&r.ID, &r.TaskID, &r.TenantID, &r.UserID, &r.ConversationID,
		&r.FiredAt, &r.Outcome, &r.SkipReason,
		&r.AssistantMessageID, &r.PolicyAction, &r.Tokens, &r.ErrorDetail,
	)
}

// ListRunsByTask returns a task's audit timeline, newest first. Hard-scoped
// by (tenant, user) AND by task so a leaked task id from another user
// yields nothing.
func (r *CloudTaskRepo) ListRunsByTask(
	ctx context.Context, tenantID, userID, taskID uuid.UUID,
) ([]model.CloudTaskRun, error) {
	q := `SELECT ` + cloudRunColumns + `
	      FROM chat.cloud_task_runs
	      WHERE task_id = $1 AND tenant_id = $2 AND user_id = $3
	      ORDER BY fired_at DESC
	      LIMIT 200`
	rows, err := r.db.Query(ctx, q, taskID, tenantID, userID)
	if err != nil {
		return nil, fmt.Errorf("list cloud task runs: %w", err)
	}
	defer rows.Close()
	out := make([]model.CloudTaskRun, 0, 16)
	for rows.Next() {
		var run model.CloudTaskRun
		if err := scanCloudRun(rows, &run); err != nil {
			return nil, fmt.Errorf("list cloud task runs scan: %w", err)
		}
		out = append(out, run)
	}
	return out, rows.Err()
}
