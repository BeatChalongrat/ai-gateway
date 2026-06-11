package repository_test

import (
	"context"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// These are INTEGRATION tests — they run against the real postgres-webchat
// schema (chat.folders / chat.conversations). They skip cleanly when the DB
// is unreachable so CI without a database stays green.
//
//	CHAT_EDGE_TEST_DSN overrides the connection string; the default targets
//	the host-published postgres-webchat (docker-compose.webchat.yml :5433).
//
// Apply migrations first: services/chat-edge-api/scripts/apply_migrations.sh

const defaultTestDSN = "postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable"

func testPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("CHAT_EDGE_TEST_DSN")
	if dsn == "" {
		dsn = defaultTestDSN
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Skipf("skipping folder integration tests: cannot create pool: %v", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		t.Skipf("skipping folder integration tests: DB unreachable (%v)", err)
	}
	// Verify the migration is applied so a missing table fails as a SKIP,
	// not a confusing scan error.
	var reg *string
	if err := pool.QueryRow(ctx, "SELECT to_regclass('chat.folders')::text").Scan(&reg); err != nil || reg == nil {
		pool.Close()
		t.Skip("skipping: chat.folders not present — run apply_migrations.sh")
	}
	return pool
}

// newConv inserts a throwaway conversation for the given owner and registers
// cleanup. Uses ConversationRepo.Create so the row is shaped exactly like a
// real one.
func newConv(t *testing.T, ctx context.Context, cr *repository.ConversationRepo, tenantID, userID uuid.UUID) *model.Conversation {
	t.Helper()
	c, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "gpt-4o-mini"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	return c
}

func TestFolderRepo(t *testing.T) {
	pool := testPool(t)
	defer pool.Close()
	ctx := context.Background()
	fr := repository.NewFolderRepo(pool)
	cr := repository.NewConversationRepo(pool)

	// Isolate every run behind a unique (tenant, user) so concurrent/repeat
	// runs never see each other's rows. Clean up at the end.
	tenantID := uuid.New()
	userID := uuid.New()
	otherUser := uuid.New()
	t.Cleanup(func() {
		// Delete conversations first (FK), then folders, for both users.
		_, _ = pool.Exec(ctx, "DELETE FROM chat.conversations WHERE tenant_id = $1", tenantID)
		_, _ = pool.Exec(ctx, "DELETE FROM chat.folders WHERE tenant_id = $1", tenantID)
	})

	t.Run("CreateFolder", func(t *testing.T) {
		f, err := fr.Create(ctx, tenantID, userID, "งาน")
		if err != nil {
			t.Fatalf("create: %v", err)
		}
		if f.ID == uuid.Nil || f.Name != "งาน" || f.Position != 1 {
			t.Fatalf("unexpected folder: %+v", f)
		}
		// Second folder gets the next position.
		f2, err := fr.Create(ctx, tenantID, userID, "เรียนรู้")
		if err != nil {
			t.Fatalf("create 2: %v", err)
		}
		if f2.Position != 2 {
			t.Fatalf("want position 2, got %d", f2.Position)
		}
	})

	t.Run("ListFoldersWithCount", func(t *testing.T) {
		folders, err := fr.ListByUser(ctx, tenantID, userID)
		if err != nil {
			t.Fatalf("list: %v", err)
		}
		if len(folders) < 2 {
			t.Fatalf("want >=2 folders, got %d", len(folders))
		}
		work := folders[0] // ordered by position → "งาน" first
		// File two conversations into it; count must reflect non-deleted.
		c1 := newConv(t, ctx, cr, tenantID, userID)
		c2 := newConv(t, ctx, cr, tenantID, userID)
		if err := cr.MoveToFolder(ctx, tenantID, c1.ID, &work.ID); err != nil {
			t.Fatalf("move c1: %v", err)
		}
		if err := cr.MoveToFolder(ctx, tenantID, c2.ID, &work.ID); err != nil {
			t.Fatalf("move c2: %v", err)
		}
		folders, _ = fr.ListByUser(ctx, tenantID, userID)
		if folders[0].ConversationCount != 2 {
			t.Fatalf("want count 2, got %d", folders[0].ConversationCount)
		}
		// Soft-deleting one must drop the count to 1.
		if err := cr.SoftDelete(ctx, tenantID, c2.ID); err != nil {
			t.Fatalf("soft delete: %v", err)
		}
		folders, _ = fr.ListByUser(ctx, tenantID, userID)
		if folders[0].ConversationCount != 1 {
			t.Fatalf("want count 1 after soft delete, got %d", folders[0].ConversationCount)
		}
	})

	t.Run("RenameFolder", func(t *testing.T) {
		f, _ := fr.Create(ctx, tenantID, userID, "before")
		got, err := fr.Update(ctx, tenantID, userID, f.ID, "after")
		if err != nil {
			t.Fatalf("rename: %v", err)
		}
		if got.Name != "after" {
			t.Fatalf("want 'after', got %q", got.Name)
		}
	})

	t.Run("DeleteFolder_ConversationsUnfiled", func(t *testing.T) {
		f, _ := fr.Create(ctx, tenantID, userID, "to-delete")
		c := newConv(t, ctx, cr, tenantID, userID)
		if err := cr.MoveToFolder(ctx, tenantID, c.ID, &f.ID); err != nil {
			t.Fatalf("file conv: %v", err)
		}
		if err := fr.Delete(ctx, tenantID, userID, f.ID); err != nil {
			t.Fatalf("delete folder: %v", err)
		}
		// The conversation must survive with folder_id back to NULL.
		got, err := cr.GetByID(ctx, tenantID, c.ID)
		if err != nil {
			t.Fatalf("get conv after folder delete: %v", err)
		}
		if got.FolderID != nil {
			t.Fatalf("want folder_id NULL after folder delete, got %v", *got.FolderID)
		}
	})

	t.Run("ReorderFolders", func(t *testing.T) {
		a, _ := fr.Create(ctx, tenantID, userID, "A")
		b, _ := fr.Create(ctx, tenantID, userID, "B")
		// Put B before A.
		if err := fr.Reorder(ctx, tenantID, userID, []uuid.UUID{b.ID, a.ID}); err != nil {
			t.Fatalf("reorder: %v", err)
		}
		gotB, _ := fr.GetByID(ctx, tenantID, userID, b.ID)
		gotA, _ := fr.GetByID(ctx, tenantID, userID, a.ID)
		if gotB.Position >= gotA.Position {
			t.Fatalf("want B before A, got B=%d A=%d", gotB.Position, gotA.Position)
		}
	})

	t.Run("MoveConversationInAndOut", func(t *testing.T) {
		f, _ := fr.Create(ctx, tenantID, userID, "move-target")
		c := newConv(t, ctx, cr, tenantID, userID)
		// In.
		if err := cr.MoveToFolder(ctx, tenantID, c.ID, &f.ID); err != nil {
			t.Fatalf("move in: %v", err)
		}
		got, _ := cr.GetByID(ctx, tenantID, c.ID)
		if got.FolderID == nil || *got.FolderID != f.ID {
			t.Fatalf("want filed into %v, got %v", f.ID, got.FolderID)
		}
		// Out (nil → unfiled).
		if err := cr.MoveToFolder(ctx, tenantID, c.ID, nil); err != nil {
			t.Fatalf("move out: %v", err)
		}
		got, _ = cr.GetByID(ctx, tenantID, c.ID)
		if got.FolderID != nil {
			t.Fatalf("want unfiled, got %v", *got.FolderID)
		}
	})

	t.Run("UserCannotAccessOtherUserFolders", func(t *testing.T) {
		mine, _ := fr.Create(ctx, tenantID, userID, "private")
		// Another user lists → must not see it.
		theirs, err := fr.ListByUser(ctx, tenantID, otherUser)
		if err != nil {
			t.Fatalf("list other: %v", err)
		}
		for _, f := range theirs {
			if f.ID == mine.ID {
				t.Fatalf("other user saw my folder %v", mine.ID)
			}
		}
		// Other user rename/delete → ErrNotFound (no-op).
		if _, err := fr.Update(ctx, tenantID, otherUser, mine.ID, "hacked"); err != repository.ErrNotFound {
			t.Fatalf("want ErrNotFound on cross-user rename, got %v", err)
		}
		if err := fr.Delete(ctx, tenantID, otherUser, mine.ID); err != repository.ErrNotFound {
			t.Fatalf("want ErrNotFound on cross-user delete, got %v", err)
		}
		// And mine still exists.
		if _, err := fr.GetByID(ctx, tenantID, userID, mine.ID); err != nil {
			t.Fatalf("my folder should still exist: %v", err)
		}
	})
}
