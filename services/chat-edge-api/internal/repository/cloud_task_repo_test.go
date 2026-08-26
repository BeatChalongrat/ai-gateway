package repository_test

// FIX13 — cloud task repo tests.
//
//   * validation — Create rejects a sub-hour interval, an oversize / empty
//     prompt, and a non-positive max_runs WITHOUT touching the DB (pure
//     unit, nil pool);
//   * cross-user scoping — user B cannot Get / SetEnabled / Delete user A's
//     task, nor read user A's run audit;
//   * ListDue returns only enabled + due tasks; RecordRun + AdvanceAfterRun
//     round-trip.
//
// The scoping tests are INTEGRATION tests against the real chat schema; they
// skip cleanly when the DB is unreachable. Migration 035 is idempotent, so
// the helper applies it first.

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func intptr(i int) *int { return &i }

func TestCloudTaskRepo_Create_Validation(t *testing.T) {
	// No DB: validateCloudTaskCaps runs before any query, so a nil pool is
	// never dereferenced.
	r := repository.NewCloudTaskRepo(nil)
	base := func() *model.CloudTask {
		return &model.CloudTask{
			TenantID:        uuid.New(),
			UserID:          uuid.New(),
			ConversationID:  uuid.New(),
			Name:            "ok",
			Prompt:          "do the thing",
			IntervalSeconds: model.CloudTaskMinIntervalSeconds,
			NextRunAt:       time.Now(),
		}
	}

	// Sub-hour interval (the 1-hour floor).
	bad := base()
	bad.IntervalSeconds = model.CloudTaskMinIntervalSeconds - 1
	if _, err := r.Create(context.Background(), bad); !errors.Is(err, repository.ErrInvalidCloudTask) {
		t.Fatalf("expected ErrInvalidCloudTask for sub-hour interval, got %v", err)
	}

	// Oversize prompt.
	bad = base()
	bad.Prompt = strings.Repeat("x", model.CloudTaskPromptMaxLen+1)
	if _, err := r.Create(context.Background(), bad); !errors.Is(err, repository.ErrInvalidCloudTask) {
		t.Fatalf("expected ErrInvalidCloudTask for oversize prompt, got %v", err)
	}

	// Empty prompt.
	bad = base()
	bad.Prompt = "   "
	if _, err := r.Create(context.Background(), bad); !errors.Is(err, repository.ErrInvalidCloudTask) {
		t.Fatalf("expected ErrInvalidCloudTask for empty prompt, got %v", err)
	}

	// Empty / oversize name.
	bad = base()
	bad.Name = strings.Repeat("n", model.CloudTaskNameMaxLen+1)
	if _, err := r.Create(context.Background(), bad); !errors.Is(err, repository.ErrInvalidCloudTask) {
		t.Fatalf("expected ErrInvalidCloudTask for oversize name, got %v", err)
	}

	// Non-positive max_runs.
	bad = base()
	bad.MaxRuns = intptr(0)
	if _, err := r.Create(context.Background(), bad); !errors.Is(err, repository.ErrInvalidCloudTask) {
		t.Fatalf("expected ErrInvalidCloudTask for max_runs=0, got %v", err)
	}
}

func cloudTaskPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("CHAT_EDGE_TEST_DSN")
	if dsn == "" {
		dsn = defaultTestDSN
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Skipf("skip: cannot create pool: %v", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		t.Skipf("skip: DB unreachable (%v)", err)
	}
	// Migration 035 is idempotent — apply so a fresh test DB has the tables.
	if b, err := os.ReadFile("../../migrations/035_create_cloud_tasks.sql"); err == nil {
		_, _ = pool.Exec(ctx, string(b))
	}
	var reg *string
	if err := pool.QueryRow(ctx, "SELECT to_regclass('chat.cloud_scheduled_tasks')::text").Scan(&reg); err != nil || reg == nil {
		pool.Close()
		t.Skip("skip: chat.cloud_scheduled_tasks not present — run apply_migrations.sh")
	}
	return pool
}

func TestCloudTaskRepo_ScopingAndDueScan(t *testing.T) {
	pool := cloudTaskPool(t)
	defer pool.Close()
	ctx := context.Background()
	r := repository.NewCloudTaskRepo(pool)

	tenantID := uuid.New()
	userA, userB := uuid.New(), uuid.New()
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.cloud_task_runs WHERE tenant_id = $1", tenantID)
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.cloud_scheduled_tasks WHERE tenant_id = $1", tenantID)
	})

	// A creates a DUE task (next_run_at in the past) and a NOT-due one.
	due, err := r.Create(ctx, &model.CloudTask{
		TenantID: tenantID, UserID: userA, ConversationID: uuid.New(),
		Name: "due one", Prompt: "run me", IntervalSeconds: 3600,
		NextRunAt: time.Now().Add(-time.Hour), Enabled: true,
	})
	if err != nil {
		t.Fatalf("create due: %v", err)
	}
	if _, err := r.Create(ctx, &model.CloudTask{
		TenantID: tenantID, UserID: userA, ConversationID: uuid.New(),
		Name: "future one", Prompt: "later", IntervalSeconds: 3600,
		NextRunAt: time.Now().Add(time.Hour), Enabled: true,
	}); err != nil {
		t.Fatalf("create future: %v", err)
	}

	// ListDue must include the due task (scoped to our tenant to avoid
	// cross-test noise).
	dueList, err := r.ListDue(ctx, time.Now(), 100)
	if err != nil {
		t.Fatalf("list due: %v", err)
	}
	found := false
	for _, task := range dueList {
		if task.ID == due.ID {
			found = true
		}
		if task.ID != due.ID && task.TenantID == tenantID && task.Name == "future one" {
			t.Fatalf("ListDue returned a not-yet-due task")
		}
	}
	if !found {
		t.Fatalf("ListDue must return the due task")
	}

	// Cross-user scoping: B cannot Get / SetEnabled / Delete A's task.
	if _, err := r.GetByID(ctx, tenantID, userB, due.ID); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("user B must not read user A's task, got %v", err)
	}
	if err := r.SetEnabled(ctx, tenantID, userB, due.ID, false); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("user B must not toggle user A's task, got %v", err)
	}
	if err := r.Delete(ctx, tenantID, userB, due.ID); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("user B must not delete user A's task, got %v", err)
	}

	// RecordRun + audit scoping: an audit row for A's task is invisible to B.
	convID := due.ConversationID
	if err := r.RecordRun(ctx, &model.CloudTaskRun{
		TaskID: due.ID, TenantID: tenantID, UserID: userA, ConversationID: &convID,
		Outcome: model.CloudRunOK,
	}); err != nil {
		t.Fatalf("record run: %v", err)
	}
	aRuns, err := r.ListRunsByTask(ctx, tenantID, userA, due.ID)
	if err != nil || len(aRuns) != 1 {
		t.Fatalf("owner must see 1 audit row, got %d err=%v", len(aRuns), err)
	}
	bRuns, err := r.ListRunsByTask(ctx, tenantID, userB, due.ID)
	if err != nil || len(bRuns) != 0 {
		t.Fatalf("user B must see 0 audit rows for A's task, got %d err=%v", len(bRuns), err)
	}

	// AdvanceAfterRun bumps run_count + moves next_run_at forward.
	next := time.Now().Add(2 * time.Hour)
	if err := r.AdvanceAfterRun(ctx, tenantID, userA, due.ID, next, true); err != nil {
		t.Fatalf("advance: %v", err)
	}
	after, err := r.GetByID(ctx, tenantID, userA, due.ID)
	if err != nil {
		t.Fatalf("get after advance: %v", err)
	}
	if after.RunCount != 1 {
		t.Fatalf("advance must bump run_count to 1, got %d", after.RunCount)
	}

	// SetPaused disables + records the reason (owner-scoped).
	if err := r.SetPaused(ctx, tenantID, userA, due.ID, model.CloudPausedRevoked); err != nil {
		t.Fatalf("set paused: %v", err)
	}
	paused, err := r.GetByID(ctx, tenantID, userA, due.ID)
	if err != nil {
		t.Fatalf("get after pause: %v", err)
	}
	if paused.Enabled || paused.PausedReason == nil || *paused.PausedReason != model.CloudPausedRevoked {
		t.Fatalf("SetPaused must disable + set reason=revoked, got enabled=%v reason=%+v", paused.Enabled, paused.PausedReason)
	}
}
