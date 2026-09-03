package service

// Transcript retention sweep tests.
//
//   - dry-run (the default) counts, writes a retention_dry_run audit
//     entry, and deletes NOTHING;
//   - a live pass erases the expired conversation and the user-
//     deleted one past its purge window, each with an atomic audit
//     row;
//   - a disabled tenant is never scanned;
//   - the midnight-UTC schedule math lands exactly on 00:00.
//
// Integration tests against the real chat schema (synthetic tenants
// only; skip when the DB is unreachable).

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

const retentionTestDSN = "postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable"

func retentionTestPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("CHAT_EDGE_TEST_DSN")
	if dsn == "" {
		dsn = retentionTestDSN
	}
	pool, err := pgxpool.New(context.Background(), dsn)
	if err != nil {
		t.Skipf("skip: cannot create pool: %v", err)
	}
	if err := pool.Ping(context.Background()); err != nil {
		pool.Close()
		t.Skipf("skip: DB unreachable (%v)", err)
	}
	var reg *string
	if err := pool.QueryRow(context.Background(),
		"SELECT to_regclass('chat.tenant_retention_settings')::text").Scan(&reg); err != nil || reg == nil {
		pool.Close()
		t.Skip("skip: migration 040 not applied")
	}
	return pool
}

type fakeEraseStore struct{ deleted []string }

func (f *fakeEraseStore) Delete(_ context.Context, key string) error {
	f.deleted = append(f.deleted, key)
	return nil
}

func seedRetentionTenant(
	t *testing.T, ctx context.Context, pool *pgxpool.Pool,
	enabled, dryRun bool,
) (tenant, user, expiredConv, freshConv, deletedConv uuid.UUID) {
	t.Helper()
	tenant, user = uuid.New(), uuid.New()
	days := model.TranscriptRetentionMinDays
	settings := repository.NewTenantRetentionSettingsRepo(pool)
	if err := settings.Upsert(ctx, &model.TenantRetentionSettings{
		TenantID:                     tenant,
		TranscriptRetentionEnabled:   enabled,
		TranscriptRetentionDays:      &days,
		DeletedConversationPurgeDays: 30,
		DryRun:                       dryRun,
	}); err != nil {
		t.Fatalf("seed settings: %v", err)
	}

	mk := func(updatedDaysAgo int, deleted bool, deletedDaysAgo int) uuid.UUID {
		id := uuid.New()
		if _, err := pool.Exec(ctx, `
			INSERT INTO chat.conversations
				(id, tenant_id, user_id, title, model_id, is_deleted, deleted_at, updated_at)
			VALUES ($1, $2, $3, 'ret-probe', 'gpt-4o-mini', $4,
			        CASE WHEN $4 THEN NOW() - make_interval(days => $5) END,
			        NOW() - make_interval(days => $6))`,
			id, tenant, user, deleted, deletedDaysAgo, updatedDaysAgo,
		); err != nil {
			t.Fatalf("mk conv: %v", err)
		}
		if _, err := pool.Exec(ctx, `
			INSERT INTO chat.messages_masked
				(conversation_id, tenant_id, user_id, role, content_masked)
			VALUES ($1, $2, $3, 'user', 'probe')`,
			id, tenant, user,
		); err != nil {
			t.Fatalf("mk msg: %v", err)
		}
		return id
	}
	expiredConv = mk(60, false, 0) // > 30-day window
	freshConv = mk(1, false, 0)
	deletedConv = mk(60, true, 45) // user-deleted 45 days ago (> 30)

	t.Cleanup(func() {
		for _, tbl := range []string{
			"messages_masked", "conversations",
			"tenant_retention_settings", "deletion_audit",
		} {
			_, _ = pool.Exec(ctx,
				"DELETE FROM chat."+tbl+" WHERE tenant_id = $1", tenant)
		}
	})
	return
}

func newSweeper(pool *pgxpool.Pool) (*TranscriptRetentionService, *fakeEraseStore) {
	store := &fakeEraseStore{}
	return NewTranscriptRetentionService(
		repository.NewTenantRetentionSettingsRepo(pool),
		repository.NewConversationEraseRepo(pool),
		repository.NewFileRepo(pool),
		repository.NewDeletionAuditRepo(pool),
		store,
	), store
}

func convCount(t *testing.T, ctx context.Context, pool *pgxpool.Pool, tenant uuid.UUID) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx,
		"SELECT count(*) FROM chat.conversations WHERE tenant_id = $1", tenant,
	).Scan(&n); err != nil {
		t.Fatalf("count: %v", err)
	}
	return n
}

func TestSweep_DryRunCountsAndDeletesNothing(t *testing.T) {
	pool := retentionTestPool(t)
	defer pool.Close()
	ctx := context.Background()

	tenant, _, _, _, _ := seedRetentionTenant(t, ctx, pool, true, true)
	sweeper, _ := newSweeper(pool)

	stats := sweeper.EnforceOnce(ctx)
	if stats.Erased != 0 {
		t.Fatalf("dry run erased %d conversations — must be 0", stats.Erased)
	}
	if got := convCount(t, ctx, pool, tenant); got != 3 {
		t.Fatalf("dry run changed row count: %d, want 3", got)
	}

	audit := repository.NewDeletionAuditRepo(pool)
	entries, err := audit.ListByTenant(ctx, tenant, model.AuditActionRetentionDryRun, 10)
	if err != nil || len(entries) == 0 {
		t.Fatalf("dry-run audit entry missing (err=%v n=%d)", err, len(entries))
	}
	c := entries[0].Counts
	if c["conversations_retention_expired"] != 1 ||
		c["conversations_user_deleted_due"] != 1 {
		t.Errorf("dry-run counts wrong: %+v", c)
	}
}

func TestSweep_LivePassErasesBothRulesWithAudit(t *testing.T) {
	pool := retentionTestPool(t)
	defer pool.Close()
	ctx := context.Background()

	tenant, _, expiredConv, freshConv, deletedConv :=
		seedRetentionTenant(t, ctx, pool, true, false)
	sweeper, _ := newSweeper(pool)

	stats := sweeper.EnforceOnce(ctx)
	if stats.Erased != 2 {
		t.Fatalf("live pass erased %d, want 2 (expired + user-deleted)", stats.Erased)
	}
	if got := convCount(t, ctx, pool, tenant); got != 1 {
		t.Fatalf("rows after live pass = %d, want 1 (the fresh one)", got)
	}
	var freshLeft bool
	_ = pool.QueryRow(ctx,
		"SELECT EXISTS(SELECT 1 FROM chat.conversations WHERE id = $1)",
		freshConv).Scan(&freshLeft)
	if !freshLeft {
		t.Fatal("the fresh conversation must survive")
	}

	audit := repository.NewDeletionAuditRepo(pool)
	entries, err := audit.ListByTenant(
		ctx, tenant, model.AuditActionConversationHardDeleted, 10)
	if err != nil || len(entries) != 2 {
		t.Fatalf("hard-delete audit entries = %d (err=%v), want 2", len(entries), err)
	}
	rules := map[string]uuid.UUID{}
	for _, e := range entries {
		if e.ResourceID != nil {
			rules[e.Rule] = *e.ResourceID
		}
	}
	if rules[model.AuditRuleRetentionExpired] != expiredConv {
		t.Errorf("retention_expired audit points at %v, want %s",
			rules[model.AuditRuleRetentionExpired], expiredConv)
	}
	if rules[model.AuditRuleUserRequest] != deletedConv {
		t.Errorf("user_request audit points at %v, want %s",
			rules[model.AuditRuleUserRequest], deletedConv)
	}
}

func TestSweep_DisabledTenantIsUntouched(t *testing.T) {
	pool := retentionTestPool(t)
	defer pool.Close()
	ctx := context.Background()

	tenant, _, _, _, _ := seedRetentionTenant(t, ctx, pool, false, false)
	sweeper, _ := newSweeper(pool)
	_ = sweeper.EnforceOnce(ctx)
	if got := convCount(t, ctx, pool, tenant); got != 3 {
		t.Fatalf("disabled tenant lost rows: %d, want 3", got)
	}
}

func TestSecondsUntilNextMidnightUTC(t *testing.T) {
	at := time.Date(2026, 9, 3, 21, 30, 0, 0, time.UTC)
	d := secondsUntilNextMidnightUTC(at)
	if d != 2*time.Hour+30*time.Minute {
		t.Fatalf("wait from 21:30 = %v, want 2h30m", d)
	}
	// Crossing into the next day lands exactly on 00:00 UTC.
	if next := at.Add(d); next.Hour() != 0 || next.Minute() != 0 {
		t.Fatalf("landing time = %v, want midnight", next)
	}
}
