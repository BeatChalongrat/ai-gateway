package repository_test

// Conversation erase cascade tests (retention & deletion program).
//
// Two layers:
//
//   - COVERAGE — information_schema is the referee: every chat.*
//     table carrying a conversation_id column must be either in the
//     cascade's kind list or in the documented keep-list. A new
//     content table added without a cascade entry fails this test —
//     the erasure can never silently become incomplete.
//   - BEHAVIOR — a synthetic tenant gets one conversation with a row
//     in every content-bearing satellite; the footprint counts them
//     without deleting; the hard delete removes ALL of them in one
//     transaction and commits the deletion-audit row atomically with
//     the erasure; a second tenant's identical rows are untouched.
//
// Integration tests against the real chat schema (same testPool
// contract as folder_repo_test.go); everything created here is a
// throwaway synthetic tenant — no real data is touched.

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

// eraseKindToTable — cascade kind name → actual table name.
var eraseKindToTable = map[string]string{
	"feedback":                 "feedback",
	"canvas_artifacts":         "canvas_artifacts",
	"response_context_sources": "response_context_sources",
	"conversation_states":      "conversation_states",
	"shared_conversations":     "shared_conversations",
	"cloud_scheduled_tasks":    "cloud_scheduled_tasks",
	"conversation_files":       "conversation_files",
	"pending_questions":        "pending_questions", // FIX74
	"messages":                 "messages_masked",
	"conversations":            "conversations",
}

func TestEraseCoverage_EveryConversationTableIsAccountedFor(t *testing.T) {
	pool := testPool(t)
	t.Cleanup(pool.Close) // registered FIRST -> runs LAST, after row cleanup
	ctx := context.Background()

	rows, err := pool.Query(ctx, `
		SELECT table_name FROM information_schema.columns
		WHERE table_schema = 'chat' AND column_name = 'conversation_id'
		ORDER BY table_name`)
	if err != nil {
		t.Fatalf("information_schema query: %v", err)
	}
	defer rows.Close()

	covered := map[string]bool{}
	for _, k := range repository.EraseKinds() {
		tbl, ok := eraseKindToTable[k]
		if !ok {
			t.Fatalf("cascade kind %q has no table mapping in this test", k)
		}
		covered[tbl] = true
	}
	// conversations itself is in the cascade via its own id column.
	covered["conversations"] = true
	for _, tbl := range repository.KeptConversationTables {
		covered[tbl] = true
	}

	for rows.Next() {
		var tbl string
		if err := rows.Scan(&tbl); err != nil {
			t.Fatalf("scan: %v", err)
		}
		if !covered[tbl] {
			t.Errorf(
				"chat.%s has a conversation_id column but is neither in the "+
					"erase cascade nor in KeptConversationTables — add it to "+
					"one of the two lists (and to this test's mapping)", tbl)
		}
	}
}

// seedConversationFootprint creates one conversation for (tenant,
// user) with a row in every content-bearing satellite table. Returns
// the conversation id.
func seedConversationFootprint(
	t *testing.T, ctx context.Context, pool *pgxpool.Pool,
	tenantID, userID uuid.UUID,
) uuid.UUID {
	t.Helper()
	convID := uuid.New()
	msgID := uuid.New()
	fileID := uuid.New()

	stmts := []struct {
		name string
		sql  string
		args []any
	}{
		{"conversation", `
			INSERT INTO chat.conversations (id, tenant_id, user_id, title, model_id)
			VALUES ($1, $2, $3, 'erase-probe', 'gpt-4o-mini')`,
			[]any{convID, tenantID, userID}},
		{"message", `
			INSERT INTO chat.messages_masked
				(id, conversation_id, tenant_id, user_id, role, content_masked)
			VALUES ($1, $2, $3, $4, 'user', 'probe content')`,
			[]any{msgID, convID, tenantID, userID}},
		{"feedback", `
			INSERT INTO chat.feedback (message_id, tenant_id, user_id, rating)
			VALUES ($1, $2, $3, 'thumbs_up')`,
			[]any{msgID, tenantID, userID}},
		{"canvas", `
			INSERT INTO chat.canvas_artifacts
				(tenant_id, user_id, conversation_id, artifact_key, type, content)
			VALUES ($1, $2, $3, 'k1', 'html', 'probe artifact')`,
			[]any{tenantID, userID, convID}},
		{"state", `
			INSERT INTO chat.conversation_states (conversation_id, tenant_id, user_id)
			VALUES ($1, $2, $3)`,
			[]any{convID, tenantID, userID}},
		{"source", `
			INSERT INTO chat.response_context_sources
				(response_message_id, tenant_id, user_id, conversation_id,
				 source_type, display_label)
			VALUES ($1, $2, $3, $4, 'memory', 'probe label')`,
			[]any{msgID, tenantID, userID, convID}},
		{"share", `
			INSERT INTO chat.shared_conversations (conversation_id, tenant_id, share_token)
			VALUES ($1, $2, $3)`,
			[]any{convID, tenantID, uuid.NewString()[:30]}},
		{"cloud_task", `
			INSERT INTO chat.cloud_scheduled_tasks
				(tenant_id, user_id, conversation_id, name, prompt,
				 interval_seconds, next_run_at, enabled)
			VALUES ($1, $2, $3, 'probe', 'probe prompt', 3600, NOW() + interval '1 day', false)`,
			[]any{tenantID, userID, convID}},
		{"file", `
			INSERT INTO chat.conversation_files
				(id, conversation_id, tenant_id, user_id, filename, original_filename,
				 content_type, file_size_bytes, storage_path)
			VALUES ($1, $2, $3, $4, 'p.txt', 'p.txt', 'text/plain', 5, $5)`,
			[]any{fileID, convID, tenantID, userID,
				fmt.Sprintf("erase-probe/%s.txt", fileID)}},
		// FIX74 — a parked turn (its request snapshot holds the conversation's text).
		{"pending_question", `
			INSERT INTO chat.pending_questions
				(tenant_id, user_id, conversation_id, message_id, tool_call_id,
				 questions_json, state_json, expires_at)
			SELECT $1, $2, $3, m.id, 'call_probe', '[]'::jsonb, '{}'::jsonb, NOW() + interval '1 day'
			FROM chat.messages_masked m WHERE m.conversation_id = $3 LIMIT 1`,
			[]any{tenantID, userID, convID}},
	}
	for _, s := range stmts {
		if _, err := pool.Exec(ctx, s.sql, s.args...); err != nil {
			t.Fatalf("seed %s: %v", s.name, err)
		}
	}
	return convID
}

func countAcrossFootprint(
	t *testing.T, ctx context.Context, pool *pgxpool.Pool,
	tenantID uuid.UUID,
) int {
	t.Helper()
	total := 0
	for _, tbl := range []string{
		"conversations", "messages_masked", "feedback", "canvas_artifacts",
		"conversation_states", "response_context_sources",
		"shared_conversations", "cloud_scheduled_tasks", "conversation_files",
	} {
		var n int
		q := "SELECT count(*) FROM chat." + tbl + " WHERE tenant_id = $1"
		if err := pool.QueryRow(ctx, q, tenantID).Scan(&n); err != nil {
			t.Fatalf("count %s: %v", tbl, err)
		}
		total += n
	}
	return total
}

func TestHardDeleteConversation_CascadesAndAuditsAtomically(t *testing.T) {
	pool := testPool(t)
	t.Cleanup(pool.Close) // registered FIRST -> runs LAST, after row cleanup
	ctx := context.Background()
	if reg := regclass(t, ctx, pool, "chat.deletion_audit"); !reg {
		testdeps.Missing(t, "skipping: chat.deletion_audit not present — run apply_migrations.sh")
	}

	tenantA, userA := uuid.New(), uuid.New()
	tenantB, userB := uuid.New(), uuid.New()
	// Cleanup registered BEFORE seeding: a mid-seed Fatalf must not
	// strand synthetic rows in the shared DB.
	t.Cleanup(func() {
		// Tenant B's synthetic rows (and any A leftovers on failure).
		for _, tid := range []uuid.UUID{tenantA, tenantB} {
			for _, tbl := range []string{
				"feedback", "canvas_artifacts", "response_context_sources",
				"conversation_states", "shared_conversations",
				"cloud_scheduled_tasks", "conversation_files",
				"messages_masked", "conversations", "deletion_audit",
			} {
				_, _ = pool.Exec(ctx,
					"DELETE FROM chat."+tbl+" WHERE tenant_id = $1", tid)
			}
		}
	})
	convA := seedConversationFootprint(t, ctx, pool, tenantA, userA)
	_ = seedConversationFootprint(t, ctx, pool, tenantB, userB)

	erase := repository.NewConversationEraseRepo(pool)

	// Footprint counts without deleting.
	fp, err := erase.CountConversationFootprint(ctx, tenantA, convA)
	if err != nil {
		t.Fatalf("footprint: %v", err)
	}
	for _, kind := range repository.EraseKinds() {
		if fp[kind] != 1 {
			t.Errorf("footprint[%s] = %d, want 1 (seed puts one row everywhere)",
				kind, fp[kind])
		}
	}
	if got := countAcrossFootprint(t, ctx, pool, tenantA); got != 9 {
		t.Fatalf("footprint counting must not delete — tenant A rows = %d, want 9", got)
	}

	// Hard delete: everything gone, audit row committed with counts.
	counts, err := erase.HardDeleteConversation(
		ctx, tenantA, convA, &userA, "test:erase", model.AuditRuleRetentionExpired)
	if err != nil {
		t.Fatalf("hard delete: %v", err)
	}
	for _, kind := range repository.EraseKinds() {
		if counts[kind] != 1 {
			t.Errorf("deleted[%s] = %d, want 1", kind, counts[kind])
		}
	}
	if got := countAcrossFootprint(t, ctx, pool, tenantA); got != 0 {
		t.Errorf("tenant A rows after erase = %d, want 0", got)
	}
	// The other tenant's identical footprint is untouched.
	if got := countAcrossFootprint(t, ctx, pool, tenantB); got != 9 {
		t.Errorf("tenant B rows after erasing tenant A = %d, want 9", got)
	}

	audit := repository.NewDeletionAuditRepo(pool)
	entries, err := audit.ListByTenant(
		ctx, tenantA, model.AuditActionConversationHardDeleted, 10)
	if err != nil {
		t.Fatalf("list audit: %v", err)
	}
	if len(entries) != 1 {
		t.Fatalf("audit entries = %d, want exactly 1", len(entries))
	}
	e := entries[0]
	if e.Rule != model.AuditRuleRetentionExpired {
		t.Errorf("audit rule = %q, want retention_expired", e.Rule)
	}
	if e.ResourceID == nil || *e.ResourceID != convA {
		t.Errorf("audit resource_id = %v, want %s", e.ResourceID, convA)
	}
	if e.Counts["messages"] != 1 || e.Counts["conversations"] != 1 {
		t.Errorf("audit counts missing kinds: %+v", e.Counts)
	}
	// Counts only — the audit row must not carry content. (Shape
	// guarantee: Counts is {kind: int}; nothing stores text.)
}

func TestRetentionSettings_FloorAndDefaults(t *testing.T) {
	pool := testPool(t)
	t.Cleanup(pool.Close) // registered FIRST -> runs LAST, after row cleanup
	ctx := context.Background()
	if reg := regclass(t, ctx, pool, "chat.tenant_retention_settings"); !reg {
		testdeps.Missing(t, "skipping: chat.tenant_retention_settings not present")
	}

	repo := repository.NewTenantRetentionSettingsRepo(pool)
	tenant := uuid.New()
	t.Cleanup(func() {
		_, _ = pool.Exec(ctx,
			"DELETE FROM chat.tenant_retention_settings WHERE tenant_id = $1", tenant)
	})

	// Defaults: off + dry-run when no row exists.
	s, err := repo.Get(ctx, tenant)
	if err != nil {
		t.Fatalf("get defaults: %v", err)
	}
	if s.TranscriptRetentionEnabled || !s.DryRun {
		t.Fatalf("defaults must be enabled=false dry_run=true, got %+v", s)
	}

	// Below-floor days are refused at the repo layer.
	days := 29
	err = repo.Upsert(ctx, &model.TenantRetentionSettings{
		TenantID: tenant, TranscriptRetentionDays: &days,
		DeletedConversationPurgeDays: 30, DryRun: true,
	})
	if !errors.Is(err, repository.ErrRetentionBelowFloor) {
		t.Fatalf("29 days must hit the floor error, got %v", err)
	}

	// The DB CHECK is the second net: bypass the repo and go straight
	// at the table.
	_, err = pool.Exec(ctx, `
		INSERT INTO chat.tenant_retention_settings
			(tenant_id, transcript_retention_days) VALUES ($1, 29)`, tenant)
	if err == nil {
		t.Fatal("DB CHECK must refuse transcript_retention_days=29")
	}

	// The floor value itself is accepted.
	days = model.TranscriptRetentionMinDays
	if err := repo.Upsert(ctx, &model.TenantRetentionSettings{
		TenantID: tenant, TranscriptRetentionEnabled: true,
		TranscriptRetentionDays:      &days,
		DeletedConversationPurgeDays: 30, DryRun: true,
	}); err != nil {
		t.Fatalf("30 days must be accepted: %v", err)
	}
}

func TestEraseCandidateWindows(t *testing.T) {
	pool := testPool(t)
	t.Cleanup(pool.Close) // registered FIRST -> runs LAST, after row cleanup
	ctx := context.Background()

	tenant, user := uuid.New(), uuid.New()
	erase := repository.NewConversationEraseRepo(pool)
	t.Cleanup(func() {
		_, _ = pool.Exec(ctx,
			"DELETE FROM chat.conversations WHERE tenant_id = $1", tenant)
	})

	mk := func(updatedDaysAgo int, deleted bool, deletedDaysAgo int) uuid.UUID {
		id := uuid.New()
		if _, err := pool.Exec(ctx, `
			INSERT INTO chat.conversations
				(id, tenant_id, user_id, title, model_id, is_deleted, deleted_at, updated_at)
			VALUES ($1, $2, $3, 'w', 'gpt-4o-mini', $4,
			        CASE WHEN $4 THEN NOW() - make_interval(days => $5) END,
			        NOW() - make_interval(days => $6))`,
			id, tenant, user, deleted, deletedDaysAgo, updatedDaysAgo,
		); err != nil {
			t.Fatalf("mk conversation: %v", err)
		}
		return id
	}

	oldActive := mk(100, false, 0)   // expired under a 90-day window
	freshActive := mk(5, false, 0)   // safe
	oldDeleted := mk(100, true, 40)  // past a 30-day purge window
	freshDeleted := mk(100, true, 5) // deleted recently — still inside window

	expired, err := erase.ListExpiredConversations(
		ctx, tenant, time.Now().UTC().AddDate(0, 0, -90), 100)
	if err != nil {
		t.Fatalf("expired: %v", err)
	}
	ids := map[uuid.UUID]bool{}
	for _, c := range expired {
		ids[c.ID] = true
	}
	if !ids[oldActive] || ids[freshActive] || ids[oldDeleted] || ids[freshDeleted] {
		t.Errorf("expired window wrong: %v", ids)
	}

	purgeable, err := erase.ListDeletedPastPurgeWindow(
		ctx, tenant, time.Now().UTC().AddDate(0, 0, -30), 100)
	if err != nil {
		t.Fatalf("purgeable: %v", err)
	}
	ids = map[uuid.UUID]bool{}
	for _, c := range purgeable {
		ids[c.ID] = true
	}
	if !ids[oldDeleted] || ids[freshDeleted] || ids[oldActive] {
		t.Errorf("purge window wrong: %v", ids)
	}
}

func regclass(t *testing.T, ctx context.Context, pool *pgxpool.Pool, rel string) bool {
	t.Helper()
	var reg *string
	if err := pool.QueryRow(ctx,
		"SELECT to_regclass($1)::text", rel).Scan(&reg); err != nil {
		return false
	}
	return reg != nil
}

// Tenant classification (retention reporting): a tenant is only ever
// called test data on positive evidence, and anything unproven counts
// as real. These pins guard the direction of that asymmetry — getting
// it wrong the other way would hide real conversations from a total
// someone uses to decide whether deletion is safe.
func TestClassifyTenant_OnlyCallsItTestOnPositiveEvidence(t *testing.T) {
	pool := testPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()
	erase := repository.NewConversationEraseRepo(pool)

	// A derived (v5) id is provisioning, never a test helper's.
	v5 := uuid.NewSHA1(uuid.NameSpaceDNS, []byte("a-real-workspace"))
	got, err := erase.ClassifyTenant(ctx, v5)
	if err != nil {
		t.Fatalf("classify v5: %v", err)
	}
	if got != repository.TenantProvisioned {
		t.Errorf("v5 id classified %q, want provisioned", got)
	}

	// A random id with nothing behind it is the throwaway shape.
	empty := uuid.New()
	if got, err = erase.ClassifyTenant(ctx, empty); err != nil {
		t.Fatalf("classify empty: %v", err)
	}
	if got != repository.TenantLikelyTest {
		t.Errorf("empty v4 tenant classified %q, want likely_test", got)
	}

	// A random id that does NOT match the shape must be reported as
	// real: three conversations is past the throwaway threshold.
	busy, user := uuid.New(), uuid.New()
	t.Cleanup(func() {
		_, _ = pool.Exec(ctx,
			"DELETE FROM chat.conversations WHERE tenant_id = $1", busy)
	})
	for i := 0; i < 3; i++ {
		if _, err := pool.Exec(ctx, `
			INSERT INTO chat.conversations (id, tenant_id, user_id, title, model_id)
			VALUES ($1, $2, $3, 'c', 'gpt-4o-mini')`,
			uuid.New(), busy, user,
		); err != nil {
			t.Fatalf("seed busy tenant: %v", err)
		}
	}
	if got, err = erase.ClassifyTenant(ctx, busy); err != nil {
		t.Fatalf("classify busy: %v", err)
	}
	if got != repository.TenantUnproven {
		t.Errorf("busy v4 tenant classified %q, want unproven (counted as real)", got)
	}
}
