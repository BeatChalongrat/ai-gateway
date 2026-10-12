package service

// Transcript retention sweep tests.
//
//   - dry-run (the default) counts, writes a retention_dry_run audit
//     entry, and deletes NOTHING;
//   - a live pass erases the expired conversation and the user-
//     deleted one past its purge window, each with an atomic audit
//     row;
//   - a disabled tenant is never scanned;
//   - the schedule: a start runs the pass it finds owed, then one for
//     every 00:00 UTC that goes by, once each, also after days asleep;
//   - a pass is as of its hour: run late it erases what was due at that
//     hour and nothing that fell due since, and a tenant whose settings
//     changed after that hour waits for the next pass.
//
// Integration tests against the real chat schema (synthetic tenants
// only; skip when the DB is unreachable).

import (
	"bytes"
	"context"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func retentionTestPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	// FIX119 Part C1 — a private clone of the test template, never the
	// live database (internal/testdb refuses it and fails without a DB).
	return testdb.Fresh(t)
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
	// Cleanup registered BEFORE any DB write: a Fatalf mid-seed must
	// never strand an enabled settings row in the shared DB (the
	// nightly sweep scans ListEnabled globally).
	t.Cleanup(func() {
		for _, tbl := range []string{
			"messages_masked", "conversations",
			"tenant_retention_settings", "deletion_audit",
		} {
			_, _ = pool.Exec(ctx,
				"DELETE FROM chat."+tbl+" WHERE tenant_id = $1", tenant)
		}
	})
	days := model.TranscriptRetentionMinDays
	settings := repository.NewTenantRetentionSettingsRepo(pool)
	if err := settings.Upsert(ctx, &model.TenantRetentionSettings{
		TenantID:                     tenant,
		TranscriptRetentionEnabled:   enabled,
		TranscriptRetentionDays:      &days,
		DeletedConversationPurgeDays: 30,
		DryRun:                       dryRun,
		// FIX132 — the deleted-data purge has its own switches; these
		// tests drive both rules together, so "disabled" means both off.
		DeletedPurgeEnabled: enabled,
		DeletedPurgeDryRun:  dryRun,
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
	t.Cleanup(pool.Close) // registered FIRST -> runs LAST, after row cleanup
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
	t.Cleanup(pool.Close) // registered FIRST -> runs LAST, after row cleanup
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
	t.Cleanup(pool.Close) // registered FIRST -> runs LAST, after row cleanup
	ctx := context.Background()

	tenant, _, _, _, _ := seedRetentionTenant(t, ctx, pool, false, false)
	sweeper, _ := newSweeper(pool)
	_ = sweeper.EnforceOnce(ctx)
	if got := convCount(t, ctx, pool, tenant); got != 3 {
		t.Fatalf("disabled tenant lost rows: %d, want 3", got)
	}
}

// The hour a pass is for: the latest 00:00 UTC at or before the clock.
// (Replaces the test of secondsUntilNextMidnightUTC: the schedule no
// longer sleeps until the next hour, it looks at the clock.)
func TestLastScheduledPass(t *testing.T) {
	sep3 := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	for _, c := range []struct {
		what string
		at   time.Time
		want time.Time
	}{
		{"late in the day", time.Date(2026, 9, 3, 21, 30, 0, 0, time.UTC), sep3},
		{"exactly on the hour", sep3, sep3},
		{"one second before the hour", sep3.Add(-time.Second), sep3.AddDate(0, 0, -1)},
		// 06:30 on the 4th in Bangkok is 23:30 UTC on the 3rd: the hour is UTC's, not the machine's
		{"a clock in another zone", time.Date(2026, 9, 4, 6, 30, 0, 0, time.FixedZone("ICT", 7*3600)), sep3},
	} {
		if got := lastScheduledPass(c.at); !got.Equal(c.want) {
			t.Errorf("%s: the pass at %s is for %s, want %s", c.what, c.at, got, c.want)
		}
	}
}

// The schedule on a clock the test moves. A start runs the pass it finds
// owed — the job used to wait for the next 00:00 UTC and so skipped the
// day whenever the machine was off or asleep at that minute — then one
// pass for every hour that goes by, once, and one (not two) after two
// days asleep.
func TestSchedule_AStartMakesGoodThePassItFindsOwedThenOneForEveryHour(t *testing.T) {
	var mu sync.Mutex
	clock := time.Date(2026, 10, 12, 3, 18, 0, 0, time.UTC) // the start of 12 Oct 2026: 10:18 in Bangkok, 3 h 18 min after the hour
	now := func() time.Time { mu.Lock(); defer mu.Unlock(); return clock }
	set := func(to time.Time) { mu.Lock(); clock = to; mu.Unlock() }

	ticks := make(chan time.Time)
	ran := make(chan time.Time, 8)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		runRetentionSchedule(ctx, now, ticks, func(due time.Time) { ran <- due })
	}()
	tick := func() {
		t.Helper()
		select {
		case ticks <- time.Time{}:
		case <-time.After(2 * time.Second):
			t.Fatal("the schedule is not looking at the clock")
		}
	}
	want := func(what string, due time.Time) {
		t.Helper()
		select {
		case got := <-ran:
			if !got.Equal(due) {
				t.Fatalf("%s: ran the pass for %s, want %s", what, got, due)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("%s: no pass was run (want the one for %s)", what, due)
		}
	}
	// two looks at the clock: the second is taken only after the first has been acted on
	none := func(what string) {
		t.Helper()
		tick()
		tick()
		select {
		case got := <-ran:
			t.Fatalf("%s: ran a pass for %s — want none", what, got)
		default:
		}
	}
	day := func(d int) time.Time { return time.Date(2026, 10, d, 0, 0, 0, 0, time.UTC) }

	want("at the start", day(12))
	none("later the same day")
	set(day(12).Add(23*time.Hour + 59*time.Minute + 30*time.Second))
	none("half a minute before the next hour")
	set(day(13).Add(20 * time.Second))
	tick()
	want("the next hour gone by", day(13))
	none("the same hour again")
	set(day(15).Add(2 * time.Hour)) // asleep through the 14th and the hour of the 15th
	tick()
	want("waking after two days", day(15))
	none("the day slept through is not run as a second pass")

	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("the schedule did not stop with its context")
	}
}

// settingsLastChanged puts a tenant's settings row at an earlier time: a
// seeded row says "changed just now", and a pass for an earlier hour
// leaves such a tenant to the next pass.
func settingsLastChanged(t *testing.T, ctx context.Context, pool *pgxpool.Pool, tenant uuid.UUID, at time.Time) {
	t.Helper()
	if _, err := pool.Exec(ctx,
		"UPDATE chat.tenant_retention_settings SET updated_at = $2 WHERE tenant_id = $1", tenant, at,
	); err != nil {
		t.Fatalf("settings change time: %v", err)
	}
}

// A pass made good late is still the pass of its hour. Six hours after
// the hour, a chat deleted 30 days and one hour before the hour is
// erased; one deleted 30 days less three hours before it — past its
// window by the clock, not yet at the hour — is not, and neither is a
// chat that went quiet as recently. They go at the next pass, as they
// would have had this one run on time.
func TestSweep_APassMadeGoodLateErasesOnlyWhatWasDueAtItsHour(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()

	tenant, user, _, freshConv, _ := seedRetentionTenant(t, ctx, pool, true, false)
	due := time.Now().UTC().Add(-6 * time.Hour)
	settingsLastChanged(t, ctx, pool, tenant, due.AddDate(0, 0, -1))

	mk := func(deletedAt *time.Time, updatedAt time.Time) uuid.UUID {
		id := uuid.New()
		if _, err := pool.Exec(ctx, `
			INSERT INTO chat.conversations
				(id, tenant_id, user_id, title, model_id, is_deleted, deleted_at, updated_at)
			VALUES ($1, $2, $3, 'ret-probe', 'gpt-4o-mini', $4, $5, $6)`,
			id, tenant, user, deletedAt != nil, deletedAt, updatedAt,
		); err != nil {
			t.Fatalf("mk conv: %v", err)
		}
		return id
	}
	window := due.AddDate(0, 0, -30)
	dueAtTheHour, notYet := window.Add(-time.Hour), window.Add(3*time.Hour)
	deletedDue := mk(&dueAtTheHour, dueAtTheHour)
	deletedSince := mk(&notYet, notYet)
	quietSince := mk(nil, notYet)

	sweeper, _ := newSweeper(pool)
	stats := sweeper.EnforceAsOf(ctx, due)
	// the seed's two (quiet 60 days, deleted 45 days ago) and the one due at the hour
	if stats.Erased != 3 {
		t.Fatalf("the late pass erased %d, want 3 — what was due at its hour: %+v", stats.Erased, stats)
	}
	left := map[uuid.UUID]bool{}
	rows, err := pool.Query(ctx, "SELECT id FROM chat.conversations WHERE tenant_id = $1", tenant)
	if err != nil {
		t.Fatalf("rows: %v", err)
	}
	for rows.Next() {
		var id uuid.UUID
		if err := rows.Scan(&id); err != nil {
			t.Fatalf("scan: %v", err)
		}
		left[id] = true
	}
	rows.Close()
	if left[deletedDue] {
		t.Error("the chat deleted 30 days and an hour before the pass's hour is still there")
	}
	if !left[deletedSince] {
		t.Error("a chat that fell due AFTER the pass's hour was erased by a pass made good late")
	}
	if !left[quietSince] {
		t.Error("a chat that went quiet long enough only AFTER the pass's hour was erased by a pass made good late")
	}
	if !left[freshConv] || len(left) != 3 {
		t.Errorf("left after the late pass: %d rows, want the fresh one and the two not yet due", len(left))
	}

	// and they are not kept: the next pass, at its own hour, takes them
	if next := sweeper.EnforceOnce(ctx); next.Erased != 2 {
		t.Fatalf("the next pass erased %d, want the 2 that fell due since: %+v", next.Erased, next)
	}
}

// "Nothing happens at 14:00": a change to a tenant's settings applies
// from the next pass. A pass being made good for an hour BEFORE the
// change must not be what applies it — the tenant waits, and says so.
func TestSweep_SettingsChangedAfterThePassHourWaitForTheNextPass(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()

	tenant, _, _, _, _ := seedRetentionTenant(t, ctx, pool, true, false) // the settings row is written now
	sweeper, _ := newSweeper(pool)

	stats := sweeper.EnforceAsOf(ctx, time.Now().UTC().Add(-6*time.Hour))
	if stats.Erased != 0 || stats.TenantsWaiting != 1 {
		t.Fatalf("a pass for an hour before the settings changed: erased %d, waiting %d — want 0 and 1", stats.Erased, stats.TenantsWaiting)
	}
	if got := convCount(t, ctx, pool, tenant); got != 3 {
		t.Fatalf("rows = %d, want all 3 kept", got)
	}
	if next := sweeper.EnforceOnce(ctx); next.Erased != 2 || next.TenantsWaiting != 0 {
		t.Fatalf("the next pass: erased %d, waiting %d — want 2 and 0", next.Erased, next.TenantsWaiting)
	}
}

// A pass that finds nothing due still says it ran. The line used to be written only when a pass saw a
// conversation, so the smoke's check for it in the last 26 hours was red on 11 Oct 2026 with nothing
// overdue — and nothing told "ran, nothing due" from "did not run".
func TestSweep_APassWithNothingDueStillSaysItRan(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()
	var said bytes.Buffer
	old := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(&said, nil)))
	t.Cleanup(func() { slog.SetDefault(old) })

	seedRetentionTenant(t, ctx, pool, false, false) // a tenant with the rule off: nothing is due
	sweeper, _ := newSweeper(pool)
	stats := sweeper.EnforceOnce(ctx)
	if stats.ConversationsSeen != 0 || stats.Erased != 0 {
		t.Fatalf("nothing was due: %+v", stats)
	}
	if !strings.Contains(said.String(), `"msg":"transcript retention: pass complete"`) || !strings.Contains(said.String(), `"seen":0`) {
		t.Fatalf("a pass with nothing due left no line saying it ran:\n%s", said.String())
	}
	// … and for which hour, so today's pass can be told from yesterday's
	if !strings.Contains(said.String(), `"due":"`+time.Now().UTC().Format("2006-01-02")) {
		t.Fatalf("the line does not say which hour the pass was for:\n%s", said.String())
	}
}
