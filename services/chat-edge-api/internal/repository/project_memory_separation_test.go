package repository_test

// FIX58 — project-scoped memory, proven at the QUERY.
//
// The claim this file exists to defend is not "the answer did not
// mention the other project". A model can be quiet about a fact it was
// given. The claim is stronger: a chat in project B never FETCHES
// project A's rows, and a general chat fetches neither — so there is no
// moment at which one project's content is inside another's request,
// one forgotten filter away from the prompt.
//
// That is a statement about SQL, so it is tested against the real
// schema. Skips cleanly when the DB is unreachable or migration 041 is
// not applied, like the other repository tests here.

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

func memoryPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	pool := testPool(t)
	var reg *string
	if err := pool.QueryRow(context.Background(),
		"SELECT to_regclass('chat.user_memories')::text").Scan(&reg); err != nil || reg == nil {
		pool.Close()
		testdeps.Missing(t, "skipping: chat.user_memories not present — run apply_migrations.sh")
	}
	return pool
}

// seedMemory inserts one active memory in the given scope and returns its id.
func seedMemory(
	t *testing.T, mr *repository.MemoryRepo, tenantID, userID uuid.UUID,
	scopeType string, scopeID *uuid.UUID, content string,
) uuid.UUID {
	t.Helper()
	m, err := mr.Create(context.Background(), &model.Memory{
		TenantID:   tenantID,
		UserID:     userID,
		ScopeType:  scopeType,
		ScopeID:    scopeID,
		MemoryType: model.MemoryTypeExplicitFact,
		Content:    content,
		SourceType: model.MemorySourceUserExplicit,
		Status:     model.MemoryStatusActive,
	})
	if err != nil {
		t.Fatalf("seed %s memory: %v", scopeType, err)
	}
	return m.ID
}

func contentsOf(ms []model.Memory) map[string]bool {
	out := map[string]bool{}
	for i := range ms {
		out[ms[i].Content] = true
	}
	return out
}

func TestGetActiveForInjection_ProjectsAreSeparatedBothWays(t *testing.T) {
	pool := memoryPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()
	mr := repository.NewMemoryRepo(pool)

	// A fresh user per run: the separation claim is about scope, and a
	// shared user would let another test's rows decide the outcome.
	tenantID, userID := uuid.New(), uuid.New()
	projectA, projectB := uuid.New(), uuid.New()

	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.user_memories WHERE user_id = $1", userID)
	})

	seedMemory(t, mr, tenantID, userID, model.MemoryScopeGlobalUser, nil, "general: the user prefers metric units")
	seedMemory(t, mr, tenantID, userID, model.MemoryScopeProject, &projectA, "project A: the client is Acme, contact Somchai")
	seedMemory(t, mr, tenantID, userID, model.MemoryScopeProject, &projectB, "project B: the client is Globex, contact Pim")

	t.Run("a project chat sees only its own project", func(t *testing.T) {
		got, err := mr.GetActiveForInjection(ctx, userID, tenantID, &projectA)
		if err != nil {
			t.Fatalf("inject for project A: %v", err)
		}
		c := contentsOf(got)
		if !c["project A: the client is Acme, contact Somchai"] {
			t.Errorf("project A's own memory was not returned; got %d rows: %v", len(got), c)
		}
		// The leak this whole round exists to prevent.
		if c["project B: the client is Globex, contact Pim"] {
			t.Error("LEAK: project B's memory was fetched for a project A chat")
		}
		// General chat memory does not automatically apply within a
		// project either — the separation runs both ways.
		if c["general: the user prefers metric units"] {
			t.Error("the global memory leaked into a project chat")
		}
		if len(got) != 1 {
			t.Errorf("expected exactly 1 row for project A, got %d: %v", len(got), c)
		}
	})

	t.Run("a general chat sees no project's memories", func(t *testing.T) {
		got, err := mr.GetActiveForInjection(ctx, userID, tenantID, nil)
		if err != nil {
			t.Fatalf("inject for general chat: %v", err)
		}
		c := contentsOf(got)
		if !c["general: the user prefers metric units"] {
			t.Errorf("the global memory was not returned; got %d rows: %v", len(got), c)
		}
		if c["project A: the client is Acme, contact Somchai"] ||
			c["project B: the client is Globex, contact Pim"] {
			t.Error("LEAK: a project memory was fetched for a general chat")
		}
		if len(got) != 1 {
			t.Errorf("expected exactly 1 row for a general chat, got %d: %v", len(got), c)
		}
	})

	t.Run("an unrelated project sees nothing at all", func(t *testing.T) {
		empty := uuid.New()
		got, err := mr.GetActiveForInjection(ctx, userID, tenantID, &empty)
		if err != nil {
			t.Fatalf("inject for an empty project: %v", err)
		}
		if len(got) != 0 {
			t.Errorf("expected 0 rows for a project with no memories, got %d: %v",
				len(got), contentsOf(got))
		}
	})
}

// Deleting a project must not leave rows that nothing can read and
// nothing can delete — the shape four rounds of retention work kept
// running into. The erase also scrubs the content, so the audit trail
// can record that a deletion happened without keeping what was deleted.
func TestSoftDeleteByProject_LeavesNoUnreachableRows(t *testing.T) {
	pool := memoryPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()
	mr := repository.NewMemoryRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	projectA, projectB := uuid.New(), uuid.New()
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.user_memories WHERE user_id = $1", userID)
	})

	seedMemory(t, mr, tenantID, userID, model.MemoryScopeProject, &projectA, "A: secret contact detail")
	seedMemory(t, mr, tenantID, userID, model.MemoryScopeProject, &projectB, "B: untouched")
	globalID := seedMemory(t, mr, tenantID, userID, model.MemoryScopeGlobalUser, nil, "global: untouched")

	ids, err := mr.SoftDeleteByProject(ctx, projectA, userID, tenantID)
	if err != nil {
		t.Fatalf("soft delete by project: %v", err)
	}
	if len(ids) != 1 {
		t.Fatalf("expected 1 erased id, got %d", len(ids))
	}

	// The erased row keeps no content.
	var content string
	var status string
	if err := pool.QueryRow(ctx,
		"SELECT content, status FROM chat.user_memories WHERE id = $1", ids[0],
	).Scan(&content, &status); err != nil {
		t.Fatalf("read erased row: %v", err)
	}
	if status != model.MemoryStatusDeleted {
		t.Errorf("status = %q, want %q", status, model.MemoryStatusDeleted)
	}
	if content != "[deleted]" {
		t.Errorf("content survived the erase: %q", content)
	}

	// The sibling project and the global set are untouched — a delete
	// that reached past its own project would be its own leak.
	stillB, err := mr.GetActiveForInjection(ctx, userID, tenantID, &projectB)
	if err != nil {
		t.Fatalf("inject for project B: %v", err)
	}
	if len(stillB) != 1 || stillB[0].Content != "B: untouched" {
		t.Errorf("project B was affected by project A's delete: %v", contentsOf(stillB))
	}
	stillGlobal, err := mr.GetActiveForInjection(ctx, userID, tenantID, nil)
	if err != nil {
		t.Fatalf("inject for general chat: %v", err)
	}
	if len(stillGlobal) != 1 || stillGlobal[0].ID != globalID {
		t.Errorf("the global memory was affected by a project delete: %v", contentsOf(stillGlobal))
	}

	// Idempotent: a second delete finds nothing left to erase, so a
	// retry cannot double-log or resurrect anything.
	again, err := mr.SoftDeleteByProject(ctx, projectA, userID, tenantID)
	if err != nil {
		t.Fatalf("second soft delete: %v", err)
	}
	if len(again) != 0 {
		t.Errorf("second delete erased %d rows, want 0", len(again))
	}
}
