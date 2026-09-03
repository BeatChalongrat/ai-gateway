package service

// FIX13 §7 criterion 3 — LIVE end-to-end: a DUE cloud task, with NO client
// attached, is fired by the scheduler Tick, runs a REAL headless turn through
// the gateway, and writes a REAL audit row. This is the whole point of the
// round, proven against the running stack (gateway + mock provider + DB).
//
// Run:
//   CLOUD_PROBE=1 GW_URL=http://localhost:8080/v1 GW_KEY=... GW_SECRET=... \
//     go test ./internal/service/ -run TestCloudTaskLiveFire -count=1 -v
// Skips unless CLOUD_PROBE=1.

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// allowProbeAuth is the "user is active + funded" verdict, so the live run
// reaches STEP 2. (The refusal paths are covered deterministically by the
// unit tests; this probe exercises the allow path end-to-end.)
type allowProbeAuth struct{}

func (allowProbeAuth) Authorize(_ context.Context, _ uuid.UUID, _ uuid.UUID) (AuthzResult, error) {
	return AuthzResult{Allow: true, Reason: "ok"}, nil
}

func TestCloudTaskLiveFire(t *testing.T) {
	if os.Getenv("CLOUD_PROBE") != "1" {
		t.Skip("set CLOUD_PROBE=1 (plus GW_URL/GW_KEY/GW_SECRET) to run the live cloud-task fire probe")
	}
	dsn := os.Getenv("CHAT_EDGE_TEST_DSN")
	if dsn == "" {
		dsn = "postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable"
	}
	ctx := context.Background()
	// Own database per test, dropped when the test ends — no
	// cleanup code, so none can silently fail (internal/testdb).
	pool := testdb.Fresh(t)

	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	ctr := repository.NewCloudTaskRepo(pool)
	gw := NewGatewayClient(os.Getenv("GW_URL"), os.Getenv("GW_KEY"), "", 0, os.Getenv("GW_SECRET"))
	msgSvc := NewMessageService(cr, mr, gw, nil, allowAllModels{}, 10)

	tenantID, userID := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "auto"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}

	// A task that is already DUE (next_run_at in the past), enabled.
	task, err := ctr.Create(ctx, &model.CloudTask{
		TenantID:        tenantID,
		UserID:          userID,
		ConversationID:  conv.ID,
		Name:            "nightly summary (probe)",
		Prompt:          "Reply with the single word: fired",
		ScheduleKind:    "interval",
		IntervalSeconds: 3600,
		NextRunAt:       time.Now().UTC().Add(-time.Minute),
		Enabled:         true,
	})
	if err != nil {
		t.Fatalf("create cloud task: %v", err)
	}

	// The scheduler tick — the exact server-side call that fires when the
	// user's machine is closed/asleep. No client, no session, no stream.
	runner := NewCloudTaskRunner(ctr, allowProbeAuth{}, msgSvc)
	if err := runner.Tick(ctx, time.Now().UTC()); err != nil {
		t.Fatalf("Tick failed: %v", err)
	}

	// The audit row proves it ran, as whom, with what outcome.
	runs, err := ctr.ListRunsByTask(ctx, tenantID, userID, task.ID)
	if err != nil {
		t.Fatalf("list runs: %v", err)
	}
	if len(runs) != 1 {
		t.Fatalf("expected exactly one audit run, got %d", len(runs))
	}
	r := runs[0]
	if r.Outcome != model.CloudRunOK {
		t.Fatalf("expected outcome=ok, got %q (skip=%v err=%v)", r.Outcome, r.SkipReason, r.ErrorDetail)
	}
	if r.AssistantMessageID == nil {
		t.Fatalf("ok run must record the assistant message id (the turn produced no reply?)")
	}
	t.Logf("CLOUD TASK FIRED HEADLESS — task %s ran as user %s, audit outcome=%s, assistant msg=%s",
		task.ID, userID, r.Outcome, r.AssistantMessageID)
}
