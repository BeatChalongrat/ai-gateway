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
	CloudPausedRevoked   = "revoked"
	CloudPausedMaxRuns   = "max_runs"
	CloudPausedNoBalance = "no_balance"
)

// SkipToolWithheld is recorded on an otherwise-ok run when the model asked
// for a tool the cloud subset does not grant (FIX16 §1.4 / §2). The run still
// produces an answer; the note makes the wall visible in run history instead
// of the task silently producing a worse answer.
const SkipToolWithheld = "tool_withheld"

// ---------------------------------------------------------------------------
// FIX16 §1.4 — the CLOUD TOOL BOUNDARY, stated once and enforced.
//
// A cloud task runs with NO human present, so it may use only the read-only /
// self-contained tools: nothing that spends unpredictably, writes durable
// user data, installs, grants, or reaches a connector. "Happening to be safe
// is not the same as being kept safe" — so this is an explicit allowlist, and
// CloudToolClassification() below lists EVERY current tool as allowed or
// withheld. The exhaustiveness test (cloud_tool_boundary_test.go) fails if a
// tool exists that is in neither list, forcing the next tool added to make a
// decision here rather than silently inheriting cloud access.
// ---------------------------------------------------------------------------

// CloudAllowedTools is the set of tools an unattended cloud run may use.
var CloudAllowedTools = map[string]bool{
	"search_past_conversations": true, // read-only: the user's own history
	"web_search":                true, // already EXTERNAL_EGRESS-scanned
	"web_fetch":                 true, // already EXTERNAL_EGRESS-scanned
	"run_python":                true, // network-isolated sandbox
	"list_memories":             true, // memory READ
	"use_skill":                 true, // loads instructions; no side effect
}

// cloudWithheldTools lists every non-allowed tool WITH the reason, so the
// boundary is legible and the exhaustiveness test can prove the union covers
// the live registry.
var cloudWithheldTools = map[string]string{
	"generate_image":           "spends credits on a paid image generation",
	"generate_video":           "spends heavily; capability gated off besides",
	"save_memory":              "WRITES durable user memory with nobody watching",
	"update_canvas_artifact":   "WRITES a durable conversation artifact",
	"update_conversation_state": "WRITES conversation state",
}

// IsCloudToolAllowed reports whether an unattended cloud run may use the tool.
func IsCloudToolAllowed(name string) bool { return CloudAllowedTools[name] }

// CloudAllowedToolNames returns the allowlist as a slice (for SendInput).
func CloudAllowedToolNames() []string {
	out := make([]string, 0, len(CloudAllowedTools))
	for n := range CloudAllowedTools {
		out = append(out, n)
	}
	return out
}

// CloudToolClassification returns the allowed set and the withheld set (with
// reasons). The exhaustiveness test asserts every registered tool appears in
// exactly one — the mechanism that keeps the boundary honest as tools change.
func CloudToolClassification() (allowed map[string]bool, withheld map[string]string) {
	return CloudAllowedTools, cloudWithheldTools
}

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
