package repository_test

import (
	"context"
	"sync"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// FIX133 F2 — creating a conversation with a client-chosen id converges on
// ONE row however many times the request is repeated, even concurrently:
// the first insert wins, every other call finds the same row again.
func TestConversationRepo_CreateWithClientID_Converges(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	tenantID, userID := uuid.New(), uuid.New()
	clientID := "agent-create-0001"

	var wg sync.WaitGroup
	var mu sync.Mutex
	inserted, replayed := 0, 0
	for i := 0; i < 6; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c := &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"}
			ok, err := cr.CreateWithClientID(ctx, c, clientID)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				t.Errorf("create: %v", err)
				return
			}
			if ok {
				inserted++
			} else {
				replayed++
			}
		}()
	}
	wg.Wait()
	if inserted != 1 || replayed != 5 {
		t.Fatalf("want one insert and five replays, got inserted=%d replayed=%d", inserted, replayed)
	}
	got, err := cr.GetByClientID(ctx, tenantID, userID, clientID)
	if err != nil {
		t.Fatalf("get by client id: %v", err)
	}
	if got == nil || got.ModelID != "m" {
		t.Fatalf("the row the first call made is what every retry sees: %+v", got)
	}
	// another user with the same client id is another conversation
	other := &model.Conversation{TenantID: tenantID, UserID: uuid.New(), ModelID: "m"}
	ok, err := cr.CreateWithClientID(ctx, other, clientID)
	if err != nil || !ok {
		t.Fatalf("another user's create must insert: ok=%v err=%v", ok, err)
	}
	if other.ID == got.ID {
		t.Fatalf("client ids are per user, not global")
	}
}
