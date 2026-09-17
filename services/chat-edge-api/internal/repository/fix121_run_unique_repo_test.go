package repository_test

// FIX121 — migration 047's arbiter: one native user row per (conversation,
// run id). The service checks before it inserts; this index is what holds
// when two copies of a request insert in the same instant.

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestMessageRepo_OneUserRowPerRunID(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	tenantID, userID := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Fatalf("conv create: %v", err)
	}
	other, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Fatalf("conv create: %v", err)
	}
	run := "fix121-run-0001"
	row := func(convID uuid.UUID, role string, runID *string) *model.Message {
		return &model.Message{ConversationID: convID, TenantID: tenantID, UserID: userID, Role: role, Status: "completed", ContentMasked: "x", RunID: runID}
	}

	// Eight copies of one request at once: exactly one user row wins.
	var wg sync.WaitGroup
	var mu sync.Mutex
	won, lost := 0, 0
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := mr.Create(ctx, row(conv.ID, "user", &run))
			mu.Lock()
			defer mu.Unlock()
			switch {
			case err == nil:
				won++
			case errors.Is(err, repository.ErrDuplicateRun):
				lost++
			default:
				t.Errorf("unexpected error: %v", err)
			}
		}()
	}
	wg.Wait()
	if won != 1 || lost != 7 {
		t.Fatalf("want exactly one insert to win, got won=%d lost=%d", won, lost)
	}

	// What the index does NOT constrain — everything that worked before.
	if _, err := mr.Create(ctx, row(conv.ID, "assistant", &run)); err != nil {
		t.Fatalf("the answer row shares the run id: %v", err)
	}
	if _, err := mr.Create(ctx, row(conv.ID, "assistant", &run)); err != nil {
		t.Fatalf("assistant rows are not arbitrated: %v", err)
	}
	if _, err := mr.Create(ctx, row(other.ID, "user", &run)); err != nil {
		t.Fatalf("the same id in another conversation is another request: %v", err)
	}
	for i := 0; i < 2; i++ {
		if _, err := mr.Create(ctx, row(conv.ID, "user", nil)); err != nil {
			t.Fatalf("user rows without a run id (every send with a minted id) are unconstrained: %v", err)
		}
	}
	// An imported desktop row is idempotent on client_id, not on run_id.
	for _, cid := range []string{"u_a", "u_b"} {
		if _, _, err := mr.CreateImported(ctx, row(conv.ID, "user", &run), cid, time.Now()); err != nil {
			t.Fatalf("imported user rows stay outside the index: %v", err)
		}
	}

	got, err := mr.FindByRun(ctx, tenantID, conv.ID, run, "user")
	if err != nil || got.Role != "user" || got.ClientID != nil {
		t.Fatalf("FindByRun(user) = %+v, %v — must be the native row", got, err)
	}
	if _, err := mr.FindByRun(ctx, tenantID, conv.ID, "never-seen-0001", "user"); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("an unknown run id is ErrNotFound, got %v", err)
	}
	if _, err := mr.FindByRun(ctx, uuid.New(), conv.ID, run, "user"); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("another tenant must not find the row, got %v", err)
	}
}
