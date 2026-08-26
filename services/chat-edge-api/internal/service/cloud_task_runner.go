// Cloud task runner (FIX13) — the GATED, AUDITED executor for unattended
// scheduled runs.
//
// ===========================================================================
// THE ONE INVARIANT THAT OUTRANKS EVERYTHING
//
//	A task run MUST NOT call the turn runner unless the authorization gate
//	returned Allow.
//
// RunDueTask enforces this STRUCTURALLY: STEP 1 authorizes; only on Allow
// does STEP 2 call turnRunner.SendNonStream. A revoked user, or one with no
// balance, is refused (audited + possibly paused) and RETURNS before any
// turn/gateway call is made. There is a unit test (cloud_task_runner_test.go)
// that proves a deny ⇒ the TurnRunner is invoked ZERO times.
// ===========================================================================
//
// Every fire — allowed, refused, or errored — writes exactly one
// cloud_task_runs audit row via RecordRun. error_detail is bounded and
// carries no secret/token value.
package service

import (
	"context"
	"log/slog"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// AuthzResult is the authorizer's verdict. Reason ∈ "ok"|"revoked"|"no_balance".
type AuthzResult struct {
	Allow  bool
	Reason string
}

// RunAuthorizer decides whether an unattended run may proceed for a given
// (tenant, user). The real implementation calls the backend's
// /internal/scheduled-run/authorize endpoint (the internal-secret-gated
// source of truth for portal_users.status + credit balance). Kept as an
// interface so the invariant can be tested with a stub.
type RunAuthorizer interface {
	Authorize(ctx context.Context, tenantID, userID uuid.UUID) (AuthzResult, error)
}

// TurnRunner runs one headless assistant turn. *MessageService satisfies
// this via SendNonStream, so the real turn path is reused verbatim.
type TurnRunner interface {
	SendNonStream(ctx context.Context, in SendInput) (*SendResult, error)
}

// CloudTaskStore is the subset of CloudTaskRepo the runner needs. Narrowed
// to an interface so the invariant test can supply a fake with no DB.
type CloudTaskStore interface {
	ListDue(ctx context.Context, now time.Time, limit int) ([]model.CloudTask, error)
	RecordRun(ctx context.Context, run *model.CloudTaskRun) error
	SetPaused(ctx context.Context, tenantID, userID, id uuid.UUID, reason string) error
	AdvanceAfterRun(ctx context.Context, tenantID, userID, taskID uuid.UUID, nextRunAt time.Time, incRun bool) error
}

// CloudTaskRunner ties the three collaborators together. It owns NO state
// beyond them plus a clock (injectable for tests).
type CloudTaskRunner struct {
	store      CloudTaskStore
	authorizer RunAuthorizer
	turnRunner TurnRunner

	// now is injectable so tests can pin time; defaults to time.Now.
	now func() time.Time
	// dueLimit caps how many tasks one Tick drains.
	dueLimit int
}

func NewCloudTaskRunner(store CloudTaskStore, authorizer RunAuthorizer, turnRunner TurnRunner) *CloudTaskRunner {
	return &CloudTaskRunner{
		store:      store,
		authorizer: authorizer,
		turnRunner: turnRunner,
		now:        func() time.Time { return time.Now().UTC() },
		dueLimit:   100,
	}
}

// SetClock overrides the runner's clock (tests only).
func (rr *CloudTaskRunner) SetClock(now func() time.Time) { rr.now = now }

// clamp300 bounds error text so an unexpected error string can't blow the
// error_detail column (VARCHAR(300)) or smuggle a large payload into audit.
func clamp300(s string) string {
	if len(s) > 300 {
		return s[:300]
	}
	return s
}

func strptr(s string) *string { return &s }

// RunDueTask fires ONE due task under the invariant.
//
//	STEP 1 — authorize FIRST.
//	STEP 2 — only on Allow, run the turn.
//
// It never returns an error to the caller: a single task's failure must not
// abort the whole Tick. Everything is captured in the audit row.
func (rr *CloudTaskRunner) RunDueTask(ctx context.Context, task model.CloudTask) {
	now := rr.now()

	// -------- STEP 1: AUTHORIZE (before ANY turn/gateway call) --------
	authz, err := rr.authorizer.Authorize(ctx, task.TenantID, task.UserID)
	if err != nil {
		// Fail CLOSED: an authorizer error is NOT an Allow. The turn
		// runner is not called. Record + advance so we don't hot-retry a
		// failing authorizer every scan.
		rr.recordRun(ctx, task, &model.CloudTaskRun{
			Outcome:     model.CloudRunError,
			SkipReason:  strptr("authorize_error"),
			ErrorDetail: strptr(clamp300("authorize: " + err.Error())),
		})
		rr.advance(ctx, task, now)
		slog.Warn("cloud task authorize failed; run refused (fail-closed)",
			"task_id", task.ID.String())
		return
	}

	if !authz.Allow {
		// REFUSED. The TurnRunner is deliberately NOT called on this path.
		outcome := model.CloudRunRefusedNoBalance
		if authz.Reason == model.CloudPausedRevoked {
			outcome = model.CloudRunRefusedRevoked
		}
		rr.recordRun(ctx, task, &model.CloudTaskRun{
			Outcome:    outcome,
			SkipReason: strptr(authz.Reason),
		})
		// A revoked/departed user's task is paused so it stops being
		// scanned; a no-balance task stays enabled to retry after top-up,
		// but we still advance next_run_at so it doesn't fire every scan.
		if authz.Reason == model.CloudPausedRevoked {
			if e := rr.store.SetPaused(ctx, task.TenantID, task.UserID, task.ID, model.CloudPausedRevoked); e != nil {
				slog.Warn("cloud task pause-on-revoke failed", "task_id", task.ID.String())
			}
		} else {
			rr.advance(ctx, task, now)
		}
		slog.Info("cloud task run refused", "task_id", task.ID.String(), "reason", authz.Reason)
		return
	}

	// -------- STEP 2: RUN THE TURN (reached ONLY on Allow) --------
	//
	// TODO(FIX13 cloud-tool subset): SendInput has no tool-subset selector
	// today. An unattended run should run with the CLOUD tool subset only
	// (no desktop/local tools, no image mode). ImageMode is pinned false
	// here; wiring an explicit tool allowlist into SendNonStream is invasive
	// and deferred — see the report. Not destabilizing SendNonStream this
	// round.
	res, err := rr.turnRunner.SendNonStream(ctx, SendInput{
		TenantID:       task.TenantID,
		UserID:         task.UserID,
		ConversationID: task.ConversationID,
		Content:        task.Prompt,
		ImageMode:      false,
	})
	if err != nil {
		rr.recordRun(ctx, task, &model.CloudTaskRun{
			Outcome:     model.CloudRunError,
			ErrorDetail: strptr(clamp300("turn: " + err.Error())),
		})
		// Still advance so a persistently-failing turn doesn't fire every
		// scan; the audit row records the error.
		rr.advance(ctx, task, now)
		slog.Warn("cloud task turn failed", "task_id", task.ID.String())
		return
	}

	// Success — audit with the assistant message id + token total.
	run := &model.CloudTaskRun{
		Outcome:      model.CloudRunOK,
		PolicyAction: strptr(""), // SendResult exposes no policy action today.
	}
	if res != nil && res.AssistantMessage != nil {
		id := res.AssistantMessage.ID
		run.AssistantMessageID = &id
		if t := tokenTotal(res.AssistantMessage); t != nil {
			run.Tokens = t
		}
	}
	rr.recordRun(ctx, task, run)

	// Advance the schedule and enforce max_runs.
	rr.advance(ctx, task, now)
	if task.MaxRuns != nil && task.RunCount+1 >= *task.MaxRuns {
		if e := rr.store.SetPaused(ctx, task.TenantID, task.UserID, task.ID, model.CloudPausedMaxRuns); e != nil {
			slog.Warn("cloud task pause-on-max-runs failed", "task_id", task.ID.String())
		}
	}
	slog.Info("cloud task run ok", "task_id", task.ID.String())
}

// Tick drains all currently-due tasks. This is the unit-testable scheduler
// entry point.
//
// DEPLOYMENT NOTE: the safe core does NOT start an always-on goroutine. A
// scheduler goroutine that calls Tick(ctx, time.Now()) every N seconds (with
// a single-flight guard so two ticks never overlap) is the deployment step —
// wire it in main.go when the feature is turned on.
func (rr *CloudTaskRunner) Tick(ctx context.Context, now time.Time) error {
	due, err := rr.store.ListDue(ctx, now, rr.dueLimit)
	if err != nil {
		return err
	}
	for _, task := range due {
		rr.RunDueTask(ctx, task)
	}
	return nil
}

// advance moves next_run_at to now+interval and bumps run_count/last_run_at.
func (rr *CloudTaskRunner) advance(ctx context.Context, task model.CloudTask, now time.Time) {
	next := now.Add(time.Duration(task.IntervalSeconds) * time.Second)
	if e := rr.store.AdvanceAfterRun(ctx, task.TenantID, task.UserID, task.ID, next, true); e != nil {
		slog.Warn("cloud task advance failed", "task_id", task.ID.String())
	}
}

// recordRun fills the identity fields from the task, then appends the audit
// row. Best-effort: an audit write failure is logged, never fatal.
func (rr *CloudTaskRunner) recordRun(ctx context.Context, task model.CloudTask, run *model.CloudTaskRun) {
	run.TaskID = task.ID
	run.TenantID = task.TenantID
	run.UserID = task.UserID
	convID := task.ConversationID
	run.ConversationID = &convID
	if e := rr.store.RecordRun(ctx, run); e != nil {
		slog.Warn("cloud task audit write failed", "task_id", task.ID.String(), "outcome", run.Outcome)
	}
}

// tokenTotal sums a message's input+output token usage when present.
func tokenTotal(m *model.Message) *int {
	if m == nil {
		return nil
	}
	var total int
	var have bool
	if m.TokenUsageInput != nil {
		total += *m.TokenUsageInput
		have = true
	}
	if m.TokenUsageOutput != nil {
		total += *m.TokenUsageOutput
		have = true
	}
	if !have {
		return nil
	}
	return &total
}
