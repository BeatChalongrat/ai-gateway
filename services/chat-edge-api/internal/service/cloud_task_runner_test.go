package service

// FIX13 — the cloud task runner tests. These PROVE THE ONE INVARIANT:
//
//   a task run MUST NOT call the turn runner unless authorize() returned Allow.
//
// The refusal tests assert the spy TurnRunner was called ZERO times, and the
// allow test asserts it was called exactly once with the task's identity +
// prompt. No DB is touched — the store, authorizer, and turn runner are all
// in-memory fakes wired through the runner's interfaces.

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// ---- fakes ----------------------------------------------------------------

type fakeStore struct {
	runs           []*model.CloudTaskRun
	paused         []pausedCall
	advances       []advanceCall
	dueTasks       []model.CloudTask
	recordErr      error
	claims         int
	lastInstanceID string
}

type pausedCall struct {
	id     uuid.UUID
	reason string
}
type advanceCall struct {
	id     uuid.UUID
	next   time.Time
	incRun bool
	tenant uuid.UUID
	userID uuid.UUID
}

func (f *fakeStore) ListDue(_ context.Context, _ time.Time, _ int) ([]model.CloudTask, error) {
	return f.dueTasks, nil
}

// claimCalls records each ClaimDue invocation so the lease path is testable.
func (f *fakeStore) ClaimDue(_ context.Context, _ , _ time.Time, instanceID string, _ int) ([]model.CloudTask, error) {
	f.claims++
	f.lastInstanceID = instanceID
	return f.dueTasks, nil
}
func (f *fakeStore) RecordRun(_ context.Context, run *model.CloudTaskRun) error {
	f.runs = append(f.runs, run)
	return f.recordErr
}
func (f *fakeStore) SetPaused(_ context.Context, tenantID, userID, id uuid.UUID, reason string) error {
	f.paused = append(f.paused, pausedCall{id: id, reason: reason})
	return nil
}
func (f *fakeStore) AdvanceAfterRun(_ context.Context, tenantID, userID, taskID uuid.UUID, next time.Time, incRun bool) error {
	f.advances = append(f.advances, advanceCall{id: taskID, next: next, incRun: incRun, tenant: tenantID, userID: userID})
	return nil
}

// stubAuthorizer returns a fixed verdict.
type stubAuthorizer struct {
	result AuthzResult
	err    error
	calls  int
}

func (s *stubAuthorizer) Authorize(_ context.Context, _ uuid.UUID, _ uuid.UUID) (AuthzResult, error) {
	s.calls++
	return s.result, s.err
}

// spyTurnRunner records EVERY call. The whole point of the refusal test is
// that Calls stays 0.
type spyTurnRunner struct {
	Calls  int
	Inputs []SendInput
	result *SendResult
	err    error
}

func (s *spyTurnRunner) SendNonStream(_ context.Context, in SendInput) (*SendResult, error) {
	s.Calls++
	s.Inputs = append(s.Inputs, in)
	if s.err != nil {
		return nil, s.err
	}
	if s.result != nil {
		return s.result, nil
	}
	return &SendResult{AssistantMessage: &model.Message{ID: uuid.New()}}, nil
}

func sampleTask() model.CloudTask {
	return model.CloudTask{
		ID:              uuid.New(),
		TenantID:        uuid.New(),
		UserID:          uuid.New(),
		ConversationID:  uuid.New(),
		Name:            "daily digest",
		Prompt:          "summarize my unread threads",
		ScheduleKind:    "interval",
		IntervalSeconds: 3600,
		NextRunAt:       time.Now().Add(-time.Minute),
		Enabled:         true,
	}
}

// ---- THE REFUSAL TESTS ----------------------------------------------------

func TestRunDueTask_RefusedRevoked_NeverCallsTurnRunner(t *testing.T) {
	store := &fakeStore{}
	authz := &stubAuthorizer{result: AuthzResult{Allow: false, Reason: "revoked"}}
	spy := &spyTurnRunner{}
	runner := NewCloudTaskRunner(store, authz, spy)

	task := sampleTask()
	runner.RunDueTask(context.Background(), task)

	// THE INVARIANT: the turn runner was NEVER invoked.
	if spy.Calls != 0 {
		t.Fatalf("INVARIANT VIOLATED: TurnRunner called %d times on a revoked refusal (must be 0)", spy.Calls)
	}
	if authz.calls != 1 {
		t.Fatalf("authorize must be called exactly once, got %d", authz.calls)
	}
	// Audited as refused_revoked with reason=revoked.
	if len(store.runs) != 1 || store.runs[0].Outcome != model.CloudRunRefusedRevoked {
		t.Fatalf("expected one refused_revoked audit row, got %+v", store.runs)
	}
	if store.runs[0].SkipReason == nil || *store.runs[0].SkipReason != "revoked" {
		t.Fatalf("skip_reason must be 'revoked', got %+v", store.runs[0].SkipReason)
	}
	// The task was paused (stops being scanned) and NOT advanced.
	if len(store.paused) != 1 || store.paused[0].reason != model.CloudPausedRevoked {
		t.Fatalf("revoked task must be paused with reason=revoked, got %+v", store.paused)
	}
	if len(store.advances) != 0 {
		t.Fatalf("revoked task must NOT be advanced, got %+v", store.advances)
	}
}

func TestRunDueTask_RefusedNoBalance_NeverCallsTurnRunner(t *testing.T) {
	store := &fakeStore{}
	authz := &stubAuthorizer{result: AuthzResult{Allow: false, Reason: "no_balance"}}
	spy := &spyTurnRunner{}
	runner := NewCloudTaskRunner(store, authz, spy)

	runner.RunDueTask(context.Background(), sampleTask())

	if spy.Calls != 0 {
		t.Fatalf("INVARIANT VIOLATED: TurnRunner called %d times on a no_balance refusal (must be 0)", spy.Calls)
	}
	if len(store.runs) != 1 || store.runs[0].Outcome != model.CloudRunRefusedNoBalance {
		t.Fatalf("expected one refused_no_balance audit row, got %+v", store.runs)
	}
	// FIX16 §3 — a no_balance task is auto-PAUSED with a visible reason so the
	// user learns why it stopped (not silently retried forever). Not advanced.
	if len(store.paused) != 1 || store.paused[0].reason != model.CloudPausedNoBalance {
		t.Fatalf("no_balance task must be paused with reason=no_balance, got %+v", store.paused)
	}
	if len(store.advances) != 0 {
		t.Fatalf("paused no_balance task must NOT be advanced, got %+v", store.advances)
	}
}

func TestRunDueTask_AuthorizeError_FailsClosed_NeverCallsTurnRunner(t *testing.T) {
	store := &fakeStore{}
	authz := &stubAuthorizer{err: errors.New("backend down")}
	spy := &spyTurnRunner{}
	runner := NewCloudTaskRunner(store, authz, spy)

	runner.RunDueTask(context.Background(), sampleTask())

	if spy.Calls != 0 {
		t.Fatalf("INVARIANT VIOLATED: authorizer error must fail closed; TurnRunner called %d times", spy.Calls)
	}
	if len(store.runs) != 1 || store.runs[0].Outcome != model.CloudRunError {
		t.Fatalf("authorize error must audit outcome=error, got %+v", store.runs)
	}
}

// ---- THE ALLOW PATH -------------------------------------------------------

func TestRunDueTask_Allow_RunsOnceAndAdvances(t *testing.T) {
	store := &fakeStore{}
	authz := &stubAuthorizer{result: AuthzResult{Allow: true, Reason: "ok"}}
	inTok, outTok := 10, 20
	asst := &model.Message{ID: uuid.New(), TokenUsageInput: &inTok, TokenUsageOutput: &outTok}
	spy := &spyTurnRunner{result: &SendResult{AssistantMessage: asst}}
	runner := NewCloudTaskRunner(store, authz, spy)

	task := sampleTask()
	runner.RunDueTask(context.Background(), task)

	// Authorized THEN run — exactly once.
	if authz.calls != 1 {
		t.Fatalf("authorize must run exactly once, got %d", authz.calls)
	}
	if spy.Calls != 1 {
		t.Fatalf("allow path must call TurnRunner exactly once, got %d", spy.Calls)
	}
	// The turn ran with the task's identity + prompt, no image mode.
	in := spy.Inputs[0]
	if in.TenantID != task.TenantID || in.UserID != task.UserID || in.ConversationID != task.ConversationID {
		t.Fatalf("turn ran with wrong identity: %+v vs task %+v", in, task)
	}
	if in.Content != task.Prompt {
		t.Fatalf("turn ran with wrong prompt: %q", in.Content)
	}
	if in.ImageMode {
		t.Fatalf("cloud task turn must not use image mode")
	}
	// Audited ok, with the assistant message id + token total.
	if len(store.runs) != 1 || store.runs[0].Outcome != model.CloudRunOK {
		t.Fatalf("expected one ok audit row, got %+v", store.runs)
	}
	if store.runs[0].AssistantMessageID == nil || *store.runs[0].AssistantMessageID != asst.ID {
		t.Fatalf("ok run must record the assistant message id")
	}
	if store.runs[0].Tokens == nil || *store.runs[0].Tokens != 30 {
		t.Fatalf("ok run must record token total 30, got %+v", store.runs[0].Tokens)
	}
	// Advanced with incRun=true.
	if len(store.advances) != 1 || !store.advances[0].incRun {
		t.Fatalf("ok run must advance with incRun=true, got %+v", store.advances)
	}
}

func TestRunDueTask_Allow_MaxRunsReached_Pauses(t *testing.T) {
	store := &fakeStore{}
	authz := &stubAuthorizer{result: AuthzResult{Allow: true, Reason: "ok"}}
	spy := &spyTurnRunner{}
	runner := NewCloudTaskRunner(store, authz, spy)

	max := 3
	task := sampleTask()
	task.MaxRuns = &max
	task.RunCount = 2 // this run makes it 3 → reached.

	runner.RunDueTask(context.Background(), task)

	if spy.Calls != 1 {
		t.Fatalf("allow path must run once, got %d", spy.Calls)
	}
	if len(store.paused) != 1 || store.paused[0].reason != model.CloudPausedMaxRuns {
		t.Fatalf("reaching max_runs must pause with reason=max_runs, got %+v", store.paused)
	}
}

// ---- FIX16: cloud tool subset + DLP verdict in the audit ------------------

func TestRunDueTask_Allow_RunsWithCloudToolSubset(t *testing.T) {
	store := &fakeStore{}
	authz := &stubAuthorizer{result: AuthzResult{Allow: true, Reason: "ok"}}
	spy := &spyTurnRunner{result: &SendResult{AssistantMessage: &model.Message{ID: uuid.New()}}}
	runner := NewCloudTaskRunner(store, authz, spy)

	runner.RunDueTask(context.Background(), sampleTask())

	// §1.4 — the unattended turn is handed the explicit cloud allowlist, NOT
	// the full tool set. It must be non-nil (restricted) and contain only
	// read-only tools; it must NOT contain a spend/write tool.
	if len(spy.Inputs) != 1 {
		t.Fatalf("expected one turn, got %d", len(spy.Inputs))
	}
	allow := spy.Inputs[0].CloudToolAllowlist
	if allow == nil {
		t.Fatal("cloud run MUST pass a non-nil CloudToolAllowlist (restricted)")
	}
	set := map[string]bool{}
	for _, n := range allow {
		set[n] = true
	}
	if !set["web_search"] || !set["run_python"] {
		t.Fatalf("allowlist must contain the read-only tools, got %v", allow)
	}
	for _, banned := range []string{"generate_image", "generate_video", "save_memory", "update_canvas_artifact"} {
		if set[banned] {
			t.Fatalf("allowlist must NOT contain the withheld tool %q", banned)
		}
	}
}

func TestRunDueTask_Allow_RecordsPolicyActionAndWithheldNote(t *testing.T) {
	store := &fakeStore{}
	authz := &stubAuthorizer{result: AuthzResult{Allow: true, Reason: "ok"}}
	// The turn reports a DLP verdict AND that the model reached for a withheld tool.
	spy := &spyTurnRunner{result: &SendResult{
		AssistantMessage:      &model.Message{ID: uuid.New()},
		PolicyAction:          "mask",
		WithheldToolRequested: true,
	}}
	runner := NewCloudTaskRunner(store, authz, spy)

	runner.RunDueTask(context.Background(), sampleTask())

	if len(store.runs) != 1 {
		t.Fatalf("expected one audit row, got %d", len(store.runs))
	}
	run := store.runs[0]
	// §1.5 — the DLP verdict is recorded (was always "" before FIX16).
	if run.PolicyAction == nil || *run.PolicyAction != "mask" {
		t.Fatalf("run must record policy_action=mask, got %+v", run.PolicyAction)
	}
	// §2 — the wall is visible: a tool_withheld note on the run.
	if run.SkipReason == nil || *run.SkipReason != model.SkipToolWithheld {
		t.Fatalf("run must note tool_withheld when the model asked for a withheld tool, got %+v", run.SkipReason)
	}
}

// ---- Tick fans out to RunDueTask ------------------------------------------

func TestTick_RefusedTasksNeverReachTurnRunner(t *testing.T) {
	t1, t2 := sampleTask(), sampleTask()
	store := &fakeStore{dueTasks: []model.CloudTask{t1, t2}}
	authz := &stubAuthorizer{result: AuthzResult{Allow: false, Reason: "revoked"}}
	spy := &spyTurnRunner{}
	runner := NewCloudTaskRunner(store, authz, spy)

	if err := runner.Tick(context.Background(), time.Now()); err != nil {
		t.Fatalf("tick: %v", err)
	}
	if spy.Calls != 0 {
		t.Fatalf("INVARIANT VIOLATED: %d refused tasks reached the TurnRunner via Tick", spy.Calls)
	}
	if len(store.runs) != 2 {
		t.Fatalf("both due tasks must be audited, got %d", len(store.runs))
	}
}
