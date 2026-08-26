package model

// Cloud scheduled tasks (migration 035) — FIX13.
//
// A CloudTask is an unattended, recurring run: a stored prompt fired on
// an interval against a conversation, with NO human present. The prompt
// is plain text handed to the turn runner exactly like a typed message —
// there is deliberately no command / exec / script field, and no code
// path executes a task's Prompt.
//
// THE ONE INVARIANT lives in the runner, not here: a run is authorized
// BEFORE the turn runner is ever called. CloudTaskRun is the durable
// audit of each fire proving that happened.
//
// The struct shapes mirror the column lists exactly so the repo's
// hand-rolled INSERT/SELECT statements stay in 1:1 sync with the DB.

import (
	"time"

	"github.com/google/uuid"
)

// Size / floor caps — mirrored from the migration's CHECK constraints so
// the repo + handler reject bad input before it reaches SQL.
const (
	CloudTaskNameMaxLen   = 120
	CloudTaskPromptMaxLen = 4000
	// A cloud task fires at most once per hour. Unattended runs must not
	// be able to hot-loop and drain a balance.
	CloudTaskMinIntervalSeconds = 3600
)

// Run-outcome enums (kept in sync with the cloud_task_runs CHECK).
const (
	CloudRunOK               = "ok"
	CloudRunRefusedRevoked   = "refused_revoked"
	CloudRunRefusedNoBalance = "refused_no_balance"
	CloudRunError            = "error"
)

// Paused reasons written when a task is auto-paused (enabled=false).
const (
	CloudPausedRevoked = "revoked"
	CloudPausedMaxRuns = "max_runs"
)

// CloudTask is one row from chat.cloud_scheduled_tasks.
type CloudTask struct {
	ID       uuid.UUID `json:"id"`
	TenantID uuid.UUID `json:"-"`
	UserID   uuid.UUID `json:"-"`

	ConversationID uuid.UUID `json:"conversation_id"`

	Name   string `json:"name"`
	Prompt string `json:"prompt"`

	ScheduleKind    string `json:"schedule_kind"`
	IntervalSeconds int    `json:"interval_seconds"`

	NextRunAt time.Time `json:"next_run_at"`
	Enabled   bool      `json:"enabled"`

	MaxRuns  *int `json:"max_runs,omitempty"`
	RunCount int  `json:"run_count"`

	LastRunAt    *time.Time `json:"last_run_at,omitempty"`
	PausedReason *string    `json:"paused_reason,omitempty"`

	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// CloudTaskRun is one row from chat.cloud_task_runs — the append-only
// audit of a single fire. A refused_* outcome means the turn runner was
// NEVER invoked for that fire.
type CloudTaskRun struct {
	ID             uuid.UUID  `json:"id"`
	TaskID         uuid.UUID  `json:"task_id"`
	TenantID       uuid.UUID  `json:"-"`
	UserID         uuid.UUID  `json:"-"`
	ConversationID *uuid.UUID `json:"conversation_id,omitempty"`

	FiredAt time.Time `json:"fired_at"`

	Outcome    string  `json:"outcome"`
	SkipReason *string `json:"skip_reason,omitempty"`

	AssistantMessageID *uuid.UUID `json:"assistant_message_id,omitempty"`
	PolicyAction       *string    `json:"policy_action,omitempty"`
	Tokens             *int       `json:"tokens,omitempty"`
	ErrorDetail        *string    `json:"error_detail,omitempty"`
}
