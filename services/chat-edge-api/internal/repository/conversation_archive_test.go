package repository_test

// Phase 10 Task 1 — archive (migration 032).
//
// The contract archive must honour, and the reason it is a separate flag
// from is_deleted:
//   * archived chats leave the SIDEBAR LIST but stay fully readable
//     (GetByID still resolves them) — archive is filing, not deleting;
//   * the archive view lists exactly the archived ones, through the same
//     paginated query;
//   * un-archiving brings the chat back where it was;
//   * a DELETED chat can never be un-archived back into the sidebar —
//     delete stays the stronger state;
//   * the flag is tenant-scoped, so a foreign tenant cannot flip it.

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func archivePool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	pool := testPool(t)
	var col *string
	if err := pool.QueryRow(context.Background(), `
		SELECT column_name FROM information_schema.columns
		 WHERE table_schema='chat' AND table_name='conversations'
		   AND column_name='is_archived'`).Scan(&col); err != nil || col == nil {
		pool.Close()
		t.Skip("skipping: conversations.is_archived not present — apply migration 032")
	}
	return pool
}

// archiveConv inserts a throwaway conversation and hard-cleans it up.
// Named distinctly from folder_repo_test.go's newConv (same package).
func archiveConv(t *testing.T, cr *repository.ConversationRepo, tenantID, userID uuid.UUID, title string) *model.Conversation {
	t.Helper()
	c, err := cr.Create(context.Background(), &model.Conversation{
		TenantID: tenantID, UserID: userID, Title: title, ModelID: "gpt-4o-mini",
	})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	t.Cleanup(func() {
		_ = cr.SoftDelete(context.Background(), tenantID, c.ID)
	})
	return c
}

func idsOf(convs []model.Conversation) map[uuid.UUID]bool {
	out := make(map[uuid.UUID]bool, len(convs))
	for i := range convs {
		out[convs[i].ID] = true
	}
	return out
}

func TestArchive_HidesFromTheSidebarButKeepsTheChat(t *testing.T) {
	pool := archivePool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	kept := archiveConv(t, cr, tenantID, userID, "still here")
	filed := archiveConv(t, cr, tenantID, userID, "filed away")

	// Both start in the sidebar.
	list, _, err := cr.ListByUser(ctx, tenantID, userID, 50, uuid.Nil, false)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if got := idsOf(list); !got[kept.ID] || !got[filed.ID] {
		t.Fatalf("both conversations should start in the sidebar, got %d rows", len(list))
	}

	if err := cr.UpdateArchived(ctx, tenantID, filed.ID, true); err != nil {
		t.Fatalf("archive: %v", err)
	}

	// Gone from the sidebar…
	list, _, err = cr.ListByUser(ctx, tenantID, userID, 50, uuid.Nil, false)
	if err != nil {
		t.Fatalf("list after archive: %v", err)
	}
	got := idsOf(list)
	if got[filed.ID] {
		t.Error("an archived conversation must not appear in the sidebar list")
	}
	if !got[kept.ID] {
		t.Error("archiving one chat must not hide the others")
	}

	// …present in the archive view…
	archivedList, _, err := cr.ListByUser(ctx, tenantID, userID, 50, uuid.Nil, true)
	if err != nil {
		t.Fatalf("list archived: %v", err)
	}
	gotArchived := idsOf(archivedList)
	if !gotArchived[filed.ID] {
		t.Error("the archive view must contain the archived conversation")
	}
	if gotArchived[kept.ID] {
		t.Error("the archive view must NOT contain unarchived conversations")
	}

	// …and still fully readable. This is the whole point: archive is not
	// delete, so the thread opens normally.
	row, err := cr.GetByID(ctx, tenantID, filed.ID)
	if err != nil {
		t.Fatalf("an archived conversation must still be openable: %v", err)
	}
	if !row.IsArchived {
		t.Error("GetByID must surface is_archived so the UI can show the state")
	}

	// Un-archive restores it.
	if err := cr.UpdateArchived(ctx, tenantID, filed.ID, false); err != nil {
		t.Fatalf("unarchive: %v", err)
	}
	list, _, err = cr.ListByUser(ctx, tenantID, userID, 50, uuid.Nil, false)
	if err != nil {
		t.Fatalf("list after unarchive: %v", err)
	}
	if !idsOf(list)[filed.ID] {
		t.Error("un-archiving must bring the conversation back to the sidebar")
	}
}

func TestArchive_IsTenantScopedAndLosesToDelete(t *testing.T) {
	pool := archivePool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	c := archiveConv(t, cr, tenantID, userID, "scoped")

	// A foreign tenant cannot flip the flag.
	if err := cr.UpdateArchived(ctx, uuid.New(), c.ID, true); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("cross-tenant archive should be not-found, got %v", err)
	}
	row, err := cr.GetByID(ctx, tenantID, c.ID)
	if err != nil || row.IsArchived {
		t.Fatalf("the conversation must be untouched, got archived=%v err=%v", row, err)
	}

	// Delete wins: an archived-then-deleted chat cannot be un-archived
	// back into the sidebar.
	if err := cr.UpdateArchived(ctx, tenantID, c.ID, true); err != nil {
		t.Fatalf("archive: %v", err)
	}
	if err := cr.SoftDelete(ctx, tenantID, c.ID); err != nil {
		t.Fatalf("delete: %v", err)
	}
	if err := cr.UpdateArchived(ctx, tenantID, c.ID, false); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("un-archiving a deleted conversation must fail, got %v", err)
	}
	for _, archived := range []bool{false, true} {
		list, _, err := cr.ListByUser(ctx, tenantID, userID, 50, uuid.Nil, archived)
		if err != nil {
			t.Fatalf("list(archived=%v): %v", archived, err)
		}
		if idsOf(list)[c.ID] {
			t.Errorf("a deleted conversation must not appear in list(archived=%v)", archived)
		}
	}
}

func TestArchive_NewConversationsStartUnarchived(t *testing.T) {
	pool := archivePool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	c := archiveConv(t, cr, tenantID, userID, "fresh")

	if c.IsArchived {
		t.Error("a new conversation must not be archived")
	}
	list, _, err := cr.ListByUser(ctx, tenantID, userID, 50, uuid.Nil, false)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if !idsOf(list)[c.ID] {
		t.Error("a new conversation must appear in the sidebar")
	}
}
