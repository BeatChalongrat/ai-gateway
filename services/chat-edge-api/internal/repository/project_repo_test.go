package repository_test

// Projects integration tests (migration 029). Same harness contract as
// folder_repo_test.go: real postgres-webchat schema, SKIPS cleanly when
// the DB is unreachable or the migration isn't applied.

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

func projectPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	pool := testPool(t)
	var reg *string
	if err := pool.QueryRow(context.Background(),
		"SELECT to_regclass('chat.projects')::text").Scan(&reg); err != nil || reg == nil {
		pool.Close()
		testdeps.Missing(t, "skipping: chat.projects not present — apply migration 029")
	}
	return pool
}

func TestProjectRepo_CRUDAndOwnership(t *testing.T) {
	pool := projectPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	pr := repository.NewProjectRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()

	p, err := pr.Create(ctx, tenantID, userID, "งานลูกค้า A", "ตอบสุภาพ ลงท้ายครับ/ค่ะ")
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	t.Cleanup(func() { _ = pr.Delete(context.Background(), tenantID, userID, p.ID) })

	got, err := pr.GetByID(ctx, tenantID, userID, p.ID)
	if err != nil || got.Name != "งานลูกค้า A" || got.Instructions != "ตอบสุภาพ ลงท้ายครับ/ค่ะ" {
		t.Fatalf("get: %+v err=%v", got, err)
	}

	// Update patches subsets independently.
	newName := "งานลูกค้า B"
	if got, err = pr.Update(ctx, tenantID, userID, p.ID, &newName, nil); err != nil ||
		got.Name != newName || got.Instructions != "ตอบสุภาพ ลงท้ายครับ/ค่ะ" {
		t.Fatalf("update name only: %+v err=%v", got, err)
	}

	// Ownership: another user in the SAME tenant sees nothing and can't
	// read, mutate, or delete.
	stranger := uuid.New()
	if list, err := pr.ListByUser(ctx, tenantID, stranger); err != nil || len(list) != 0 {
		t.Fatalf("stranger list leaked: %d err=%v", len(list), err)
	}
	if _, err := pr.GetByID(ctx, tenantID, stranger, p.ID); err == nil {
		t.Fatal("stranger get should fail")
	}
	if _, err := pr.Update(ctx, tenantID, stranger, p.ID, &newName, nil); err == nil {
		t.Fatal("stranger update should fail")
	}
	if err := pr.Delete(ctx, tenantID, stranger, p.ID); err == nil {
		t.Fatal("stranger delete should fail")
	}
	// Other tenant likewise.
	if _, err := pr.GetByID(ctx, uuid.New(), userID, p.ID); err == nil {
		t.Fatal("cross-tenant get should fail")
	}
}

func TestProjectRepo_ConversationMembershipAndContextLookup(t *testing.T) {
	pool := projectPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	pr := repository.NewProjectRepo(pool)
	cr := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	p, err := pr.Create(ctx, tenantID, userID, "โปรเจกต์ทดสอบ", "คำสั่งโปรเจกต์")
	if err != nil {
		t.Fatalf("create project: %v", err)
	}
	conv := newConv(t, ctx, cr, tenantID, userID)
	t.Cleanup(func() {
		_ = pr.MoveConversationToProject(context.Background(), tenantID, conv.ID, nil)
		_ = pr.Delete(context.Background(), tenantID, userID, p.ID)
	})

	if err := pr.MoveConversationToProject(ctx, tenantID, conv.ID, &p.ID); err != nil {
		t.Fatalf("assign: %v", err)
	}
	// Membership surfaces on the conversation row.
	reread, err := cr.GetByID(ctx, tenantID, conv.ID)
	if err != nil || reread.ProjectID == nil || *reread.ProjectID != p.ID {
		t.Fatalf("conversation project_id not set: %+v err=%v", reread.ProjectID, err)
	}
	// Prompt-compile lookup resolves the inherited context in one hop.
	inherited, err := pr.GetForConversation(ctx, tenantID, conv.ID)
	if err != nil || inherited.Instructions != "คำสั่งโปรเจกต์" {
		t.Fatalf("GetForConversation: %+v err=%v", inherited, err)
	}
	// Cross-tenant lookup never resolves.
	if _, err := pr.GetForConversation(ctx, uuid.New(), conv.ID); err == nil {
		t.Fatal("cross-tenant GetForConversation should fail")
	}

	// Un-assign → lookup returns not-found again.
	if err := pr.MoveConversationToProject(ctx, tenantID, conv.ID, nil); err != nil {
		t.Fatalf("unassign: %v", err)
	}
	if _, err := pr.GetForConversation(ctx, tenantID, conv.ID); err == nil {
		t.Fatal("GetForConversation after unassign should fail")
	}
}

func TestProjectRepo_DeleteUnassignsConversations(t *testing.T) {
	pool := projectPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	pr := repository.NewProjectRepo(pool)
	cr := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	p, _ := pr.Create(ctx, tenantID, userID, "ลบแล้วแชทต้องอยู่", "")
	conv := newConv(t, ctx, cr, tenantID, userID)
	_ = pr.MoveConversationToProject(ctx, tenantID, conv.ID, &p.ID)

	if err := pr.Delete(ctx, tenantID, userID, p.ID); err != nil {
		t.Fatalf("delete project: %v", err)
	}
	reread, err := cr.GetByID(ctx, tenantID, conv.ID)
	if err != nil {
		t.Fatalf("conversation lost with project: %v", err)
	}
	if reread.ProjectID != nil {
		t.Fatalf("project_id should be NULL after project delete, got %v", reread.ProjectID)
	}
}

func TestProjectRepo_SetKnowledgeBase_RejectsForeignKB(t *testing.T) {
	pool := projectPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	pr := repository.NewProjectRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	p, _ := pr.Create(ctx, tenantID, userID, "KB scope", "")
	t.Cleanup(func() { _ = pr.Delete(context.Background(), tenantID, userID, p.ID) })

	// A random (non-existent / other-tenant) KB id must be refused.
	foreign := uuid.New()
	if _, err := pr.SetKnowledgeBase(ctx, tenantID, userID, p.ID, &foreign); err == nil {
		t.Fatal("attaching a foreign KB should fail")
	}
	// Detach (nil) always succeeds on an owned project.
	if got, err := pr.SetKnowledgeBase(ctx, tenantID, userID, p.ID, nil); err != nil || got.KnowledgeBaseID != nil {
		t.Fatalf("detach: %+v err=%v", got, err)
	}
}
