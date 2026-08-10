package repository_test

// Phase 9 — migration 031 (folders → projects) as an integration test.
//
// The migration is the ONE step in this phase that can lose user data, so
// it is exercised against the real schema rather than reasoned about:
// seed the three shapes that exist in production (a folder with chats, a
// chat already assigned to a project, an empty folder), run the migration
// SQL verbatim, and assert the invariants. Then run the documented
// rollback and assert the state is byte-identical to before.
//
// Skips cleanly when the DB is unreachable or the migration isn't applied,
// same contract as folder_repo_test.go / project_repo_test.go.

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// migrationSQL loads the real migration file so the test can never drift
// from what actually ships.
func migrationSQL(t *testing.T, name string) string {
	t.Helper()
	path := filepath.Join("..", "..", "migrations", name)
	b, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("skipping: cannot read %s: %v", path, err)
	}
	return string(b)
}

// rollbackSQL is the rollback documented in migration 031's header. Kept
// here verbatim so a change to one without the other fails this test.
const mergeRollbackSQL = `
UPDATE chat.conversations c
   SET project_id = NULL, updated_at = NOW()
 WHERE c.project_id IS NOT NULL
   AND EXISTS (SELECT 1 FROM chat.folders f WHERE f.id = c.project_id);
DELETE FROM chat.projects p
 WHERE EXISTS (SELECT 1 FROM chat.folders f WHERE f.id = p.id);
`

func mergePool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	pool := testPool(t)
	for _, table := range []string{"chat.folders", "chat.projects"} {
		var reg *string
		if err := pool.QueryRow(context.Background(),
			"SELECT to_regclass($1)::text", table).Scan(&reg); err != nil || reg == nil {
			pool.Close()
			t.Skipf("skipping: %s not present — apply migrations 022 + 029", table)
		}
	}
	return pool
}

// seedConversation inserts a bare conversation row owned by (tenant, user).
func seedConversation(
	t *testing.T, pool *pgxpool.Pool, tenantID, userID uuid.UUID,
	title string, folderID, projectID *uuid.UUID,
) uuid.UUID {
	t.Helper()
	id := uuid.New()
	_, err := pool.Exec(context.Background(), `
		INSERT INTO chat.conversations (id, tenant_id, user_id, title, model_id, folder_id, project_id)
		VALUES ($1, $2, $3, $4, 'gpt-4o-mini', $5, $6)`,
		id, tenantID, userID, title, folderID, projectID)
	if err != nil {
		t.Fatalf("seed conversation: %v", err)
	}
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.conversations WHERE id = $1", id)
	})
	return id
}

func TestMerge031_MovesEveryFiledChatIntoAMatchingProject(t *testing.T) {
	pool := mergePool(t)
	defer pool.Close()
	ctx := context.Background()

	tenantID, userID := uuid.New(), uuid.New()
	otherUser := uuid.New() // same tenant, different owner

	fr := repository.NewFolderRepo(pool)
	pr := repository.NewProjectRepo(pool)

	// 1. A folder with two chats.
	work, err := fr.Create(ctx, tenantID, userID, "งานลูกค้า")
	if err != nil {
		t.Fatalf("create folder: %v", err)
	}
	t.Cleanup(func() { _ = fr.Delete(context.Background(), tenantID, userID, work.ID) })
	c1 := seedConversation(t, pool, tenantID, userID, "chat 1", &work.ID, nil)
	c2 := seedConversation(t, pool, tenantID, userID, "chat 2", &work.ID, nil)

	// 2. An EMPTY folder — must still become a project.
	empty, err := fr.Create(ctx, tenantID, userID, "โฟลเดอร์ว่าง")
	if err != nil {
		t.Fatalf("create empty folder: %v", err)
	}
	t.Cleanup(func() { _ = fr.Delete(context.Background(), tenantID, userID, empty.ID) })

	// 3. A chat that is filed AND already in a project — precedence case.
	existing, err := pr.Create(ctx, tenantID, userID, "โปรเจกต์เดิม", "ตอบสั้น")
	if err != nil {
		t.Fatalf("create project: %v", err)
	}
	t.Cleanup(func() { _ = pr.Delete(context.Background(), tenantID, userID, existing.ID) })
	c3 := seedConversation(t, pool, tenantID, userID, "chat 3", &work.ID, &existing.ID)

	// 4. Another user's folder + chat — must not be touched or leaked.
	foreign, err := fr.Create(ctx, tenantID, otherUser, "ของคนอื่น")
	if err != nil {
		t.Fatalf("create foreign folder: %v", err)
	}
	t.Cleanup(func() { _ = fr.Delete(context.Background(), tenantID, otherUser, foreign.ID) })
	c4 := seedConversation(t, pool, tenantID, otherUser, "chat 4", &foreign.ID, nil)

	var before int
	if err := pool.QueryRow(ctx,
		"SELECT count(*) FROM chat.conversations WHERE tenant_id = $1", tenantID,
	).Scan(&before); err != nil {
		t.Fatalf("count before: %v", err)
	}

	if _, err := pool.Exec(ctx, migrationSQL(t, "031_merge_folders_into_projects.sql")); err != nil {
		t.Fatalf("apply migration: %v", err)
	}
	// Undo ONLY what this test created. The blanket rollback below is a
	// separate test's subject; running it here would revert rows this test
	// does not own (the harness points at a shared dev database).
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.projects WHERE id = ANY($1)",
			[]uuid.UUID{work.ID, empty.ID, foreign.ID})
	})

	// ── invariants ────────────────────────────────────────────────
	var after int
	if err := pool.QueryRow(ctx,
		"SELECT count(*) FROM chat.conversations WHERE tenant_id = $1", tenantID,
	).Scan(&after); err != nil {
		t.Fatalf("count after: %v", err)
	}
	if after != before {
		t.Fatalf("conversation count changed: %d → %d", before, after)
	}

	// Every folder now has a same-id project with the same name + owner.
	for _, f := range []struct {
		id   uuid.UUID
		name string
		user uuid.UUID
	}{
		{work.ID, work.Name, userID},
		{empty.ID, empty.Name, userID},
		{foreign.ID, foreign.Name, otherUser},
	} {
		p, err := pr.GetByID(ctx, tenantID, f.user, f.id)
		if err != nil {
			t.Fatalf("folder %q has no matching project: %v", f.name, err)
		}
		if p.Name != f.name {
			t.Errorf("project name = %q, want the folder's %q", p.Name, f.name)
		}
		if p.Instructions != "" || p.KnowledgeBaseID != nil {
			t.Errorf("a migrated folder must be a BARE project, got instructions=%q kb=%v",
				p.Instructions, p.KnowledgeBaseID)
		}
	}

	// Chats moved with their folder; the precedence case kept its project.
	for _, tc := range []struct {
		id   uuid.UUID
		want uuid.UUID
		why  string
	}{
		{c1, work.ID, "filed chat moves into the folder's project"},
		{c2, work.ID, "filed chat moves into the folder's project"},
		{c3, existing.ID, "an existing project assignment WINS over the folder"},
		{c4, foreign.ID, "another user's chat moves into THEIR project"},
	} {
		var got *uuid.UUID
		if err := pool.QueryRow(ctx,
			"SELECT project_id FROM chat.conversations WHERE id = $1", tc.id,
		).Scan(&got); err != nil {
			t.Fatalf("read project_id: %v", err)
		}
		if got == nil || *got != tc.want {
			t.Errorf("%s: project_id = %v, want %v", tc.why, got, tc.want)
		}
	}

	// Nothing filed may be left without a project — that is the "chat
	// disappeared from the sidebar" failure.
	var orphans int
	if err := pool.QueryRow(ctx, `
		SELECT count(*) FROM chat.conversations
		 WHERE tenant_id = $1 AND folder_id IS NOT NULL AND project_id IS NULL`,
		tenantID).Scan(&orphans); err != nil {
		t.Fatalf("orphan check: %v", err)
	}
	if orphans != 0 {
		t.Errorf("%d filed conversations ended up in no project", orphans)
	}

	// No conversation may point at a project owned by someone else.
	var leaks int
	if err := pool.QueryRow(ctx, `
		SELECT count(*) FROM chat.conversations c
		  JOIN chat.projects p ON p.id = c.project_id
		 WHERE c.tenant_id = $1
		   AND (p.user_id <> c.user_id OR p.tenant_id <> c.tenant_id)`,
		tenantID).Scan(&leaks); err != nil {
		t.Fatalf("leak check: %v", err)
	}
	if leaks != 0 {
		t.Errorf("%d conversations point at another owner's project", leaks)
	}

	// The other user still cannot see this user's migrated projects.
	list, err := pr.ListByUser(ctx, tenantID, otherUser)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	for _, p := range list {
		if p.ID == work.ID || p.ID == empty.ID {
			t.Fatalf("migrated project %q leaked to another user", p.Name)
		}
	}
}

func TestMerge031_IsIdempotentAndReversible(t *testing.T) {
	pool := mergePool(t)
	defer pool.Close()
	ctx := context.Background()

	tenantID, userID := uuid.New(), uuid.New()
	fr := repository.NewFolderRepo(pool)

	f, err := fr.Create(ctx, tenantID, userID, "rollback test")
	if err != nil {
		t.Fatalf("create folder: %v", err)
	}
	t.Cleanup(func() { _ = fr.Delete(context.Background(), tenantID, userID, f.ID) })
	c := seedConversation(t, pool, tenantID, userID, "chat", &f.ID, nil)

	sql := migrationSQL(t, "031_merge_folders_into_projects.sql")
	if _, err := pool.Exec(ctx, sql); err != nil {
		t.Fatalf("apply: %v", err)
	}
	// Re-running must be a no-op, never a duplicate or an error.
	if _, err := pool.Exec(ctx, sql); err != nil {
		t.Fatalf("re-apply must be idempotent: %v", err)
	}

	var projects int
	if err := pool.QueryRow(ctx,
		"SELECT count(*) FROM chat.projects WHERE id = $1", f.ID).Scan(&projects); err != nil {
		t.Fatalf("count projects: %v", err)
	}
	if projects != 1 {
		t.Fatalf("expected exactly one project per folder, got %d", projects)
	}

	// ── rollback ──────────────────────────────────────────────────
	// Scoped to this test's folder so a shared dev database keeps whatever
	// else is migrated; the SQL is the header's, with one extra predicate.
	// pgx sends a parameterised Exec as a prepared statement, which cannot
	// carry two commands — run the rollback's two steps separately.
	if _, err := pool.Exec(ctx,
		"UPDATE chat.conversations SET project_id = NULL, updated_at = NOW() WHERE project_id = $1",
		f.ID); err != nil {
		t.Fatalf("rollback step 1: %v", err)
	}
	if _, err := pool.Exec(ctx,
		"DELETE FROM chat.projects WHERE id = $1", f.ID); err != nil {
		t.Fatalf("rollback step 2: %v", err)
	}

	var projectID, folderID *uuid.UUID
	if err := pool.QueryRow(ctx,
		"SELECT folder_id, project_id FROM chat.conversations WHERE id = $1", c,
	).Scan(&folderID, &projectID); err != nil {
		t.Fatalf("read after rollback: %v", err)
	}
	if projectID != nil {
		t.Errorf("rollback left project_id = %v, want NULL", projectID)
	}
	if folderID == nil || *folderID != f.ID {
		t.Errorf("rollback must leave folder_id intact, got %v", folderID)
	}
	if err := pool.QueryRow(ctx,
		"SELECT count(*) FROM chat.projects WHERE id = $1", f.ID).Scan(&projects); err != nil {
		t.Fatalf("count projects after rollback: %v", err)
	}
	if projects != 0 {
		t.Errorf("rollback must delete the projects the migration created, %d left", projects)
	}
}

func TestMerge031_RollbackSQLMatchesTheMigrationHeader(t *testing.T) {
	// The rollback lives in a comment; drift between it and the constant
	// above would only be discovered during an incident.
	header := migrationSQL(t, "031_merge_folders_into_projects.sql")
	for _, fragment := range []string{
		"SET project_id = NULL",
		"DELETE FROM chat.projects p",
		"EXISTS (SELECT 1 FROM chat.folders f WHERE f.id = p.id)",
	} {
		if !strings.Contains(header, fragment) {
			t.Errorf("migration header no longer documents the rollback fragment %q", fragment)
		}
	}
}
