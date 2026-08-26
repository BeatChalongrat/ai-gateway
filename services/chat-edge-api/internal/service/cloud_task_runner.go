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
	"fmt"
	"log/slog"
	"os"
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
	// ClaimDue (FIX16) atomically selects AND leases due tasks — the
	// single-flight claim. staleBefore = now - leaseTTL; a task locked more
	// recently than that is skipped (its run is still in flight or its
	// holder has not yet timed out).
	ClaimDue(ctx context.Context, now, staleBefore time.Time, instanceID string, limit int) ([]model.CloudTask, error)
	RecordRun(ctx context.Context, run *model.CloudTaskRun) error
	SetPaused(ctx context.Context, tenantID, userID, id uuid.UUID, reason string) error
	AdvanceAfterRun(ctx context.Context, tenantID, userID, taskID uuid.UUID, nextRunAt time.Time, incRun bool) error
}

// Lease + per-run bounds (FIX16). leaseTTL must exceed runDeadline so a
// legitimately slow run is never stolen; a crashed holder is reclaimed
// leaseTTL after it died.
const (
	defaultRunDeadline = 5 * time.Minute
	defaultLeaseTTL    = 15 * time.Minute
)

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
	// instanceID stamps the lease (locked_by) so a run's holder is
	// identifiable; leaseTTL bounds a crashed holder's lease; runDeadline
	// caps a single run's wall-clock.
	instanceID  string
	leaseTTL    time.Duration
	runDeadline time.Duration
}

func NewCloudTaskRunner(store CloudTaskStore, authorizer RunAuthorizer, turnRunner TurnRunner) *CloudTaskRunner {
	return &CloudTaskRunner{
		store:       store,
		authorizer:  authorizer,
		turnRunner:  turnRunner,
		now:         func() time.Time { return time.Now().UTC() },
		dueLimit:    100,
		instanceID:  defaultInstanceID(),
		leaseTTL:    defaultLeaseTTL,
		runDeadline: defaultRunDeadline,
	}
}

// defaultInstanceID identifies this scheduler process in the lease's
// locked_by column (hostname#pid, falling back to a random id). Diagnostic
// only — the claim's correctness rests on the atomic UPDATE, not this value.
func defaultInstanceID() string {
	host, err := os.Hostname()
	if err != nil || host == "" {
		return "chat-edge-" + uuid.NewString()[:8]
	}
	return fmt.Sprintf("%s#%d", host, os.Getpid())
}

// SetClock overrides the runner's clock (tests only).
func (rr *CloudTaskRunner) SetClock(now func() time.Time) { rr.now = now }

// SetLeaseParams overrides the lease TTL + per-run deadline (tests only).
func (rr *CloudTaskRunner) SetLeaseParams(leaseTTL, runDeadline time.Duration) {
	rr.leaseTTL, rr.runDeadline = leaseTTL, runDeadline
}

// SetInstanceID overrides the lease holder id (tests only).
func (rr *CloudTaskRunner) SetInstanceID(id string) { rr.instanceID = id }

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
		// Both refusals AUTO-PAUSE the task with a visible reason, so the
		// user learns WHY it stopped (FIX16 §3) rather than the task
		// silently retrying at 3 a.m. forever. revoked -> terminal; a
		// no_balance task is paused too and resumes on the user's manual
		// re-enable after a top-up (SetEnabled clears paused_reason). No
		// balance is edited — this is a pre-run refusal, per the standing rule.
		pauseReason := model.CloudPausedNoBalance
		if authz.Reason == model.CloudPausedRevoked {
			pauseReason = model.CloudPausedRevoked
		}
		if e := rr.store.SetPaused(ctx, task.TenantID, task.UserID, task.ID, pauseReason); e != nil {
			slog.Warn("cloud task pause-on-refuse failed", "task_id", task.ID.String(), "reason", pauseReason)
		}
		slog.Info("cloud task run refused", "task_id", task.ID.String(), "reason", authz.Reason)
		return
	}

	// -------- STEP 2: RUN THE TURN (reached ONLY on Allow) --------
	//
	// FIX16 §1.4 — the run is handed the explicit CLOUD tool allowlist, so
	// SendNonStream offers only the read-only/self-contained tools and its
	// executor refuses (and flags) any call outside it. ImageMode stays off.
	// A per-run wall-clock deadline (§3) caps a hung run so one task cannot
	// hold its lease — or spend — indefinitely.
	runCtx, cancel := context.WithTimeout(ctx, rr.runDeadline)
	defer cancel()
	res, err := rr.turnRunner.SendNonStream(runCtx, SendInput{
		TenantID:           task.TenantID,
		UserID:             task.UserID,
		ConversationID:     task.ConversationID,
		Content:            task.Prompt,
		ImageMode:          false,
		CloudToolAllowlist: model.CloudAllowedToolNames(),
	})
	if err != nil {
		rr.recordRun(ctx, task, &model.CloudTaskRun{
			Outcome:     model.CloudRunError,
			ErrorDetail: strptr(clamp300("turn: " + err.Error())),
		})
		// Still advance so a persistently-failing turn doesn't fire every
		// scan; the audit row records the error. advance also clears the lease.
		rr.advance(ctx, task, now)
		slog.Warn("cloud task turn failed", "task_id", task.ID.String())
		return
	}

	// Success — audit with the assistant message id, token total, the DLP
	// verdict the gateway reported (FIX16 §1.5), and a tool_withheld note if
	// the model reached for a tool the cloud subset does not grant (§2).
	run := &model.CloudTaskRun{Outcome: model.CloudRunOK}
	if res != nil {
		run.PolicyAction = strptr(res.PolicyAction)
		if res.WithheldToolRequested {
			run.SkipReason = strptr(model.SkipToolWithheld)
		}
		if res.AssistantMessage != nil {
			id := res.AssistantMessage.ID
			run.AssistantMessageID = &id
			if t := tokenTotal(res.AssistantMessage); t != nil {
				run.Tokens = t
			}
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

// Tick claims and drains all currently-due tasks. FIX16: the claim is a
// single atomic ClaimDue (lease), so two ticks — in this process or another
// instance — never fire the same task twice. This is the scheduler entry
// point; StartScheduler (main.go) calls it on a timer with a non-overlap
// guard, but the lease is what makes it safe across instances/restarts.
func (rr *CloudTaskRunner) Tick(ctx context.Context, now time.Time) error {
	staleBefore := now.Add(-rr.leaseTTL)
	due, err := rr.store.ClaimDue(ctx, now, staleBefore, rr.instanceID, rr.dueLimit)
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
