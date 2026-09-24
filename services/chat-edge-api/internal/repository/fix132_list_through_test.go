package repository_test

// FIX132 F12 — a search hit opens the chat AT the message. NIGHT3 J9: a
// Ctrl+K hit on message 16 of a 3,000-message chat opened it at message
// 2,999, and the top was ~59 "load older" clicks away. The page now asks
// for everything between what it already has and the hit in ONE request
// (`through`), or down to the very first message (`through=first`).

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

func TestFIX132_ListThroughReachesTheHitInOneRequest(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	tenantID, userID := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		testdeps.Missing(t, "conv create (schema?): %v", err)
	}
	other, _ := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	var ids []uuid.UUID
	for i := 0; i < 30; i++ {
		role := "user"
		if i%2 == 1 {
			role = "assistant"
		}
		m, err := mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenantID, UserID: userID, Role: role, Status: "completed", ContentMasked: "x"})
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, m.ID)
	}
	foreign, _ := mr.Create(ctx, &model.Message{ConversationID: other.ID, TenantID: tenantID, UserID: userID, Role: "user", Status: "completed", ContentMasked: "y"})

	// The page opened the chat: the newest 5 (ids[25..29]).
	latest, more, err := mr.ListByConversation(ctx, tenantID, conv.ID, 5, uuid.Nil)
	if err != nil || len(latest) != 5 || latest[0].ID != ids[25] || !more {
		t.Fatalf("latest page: n=%d more=%v err=%v", len(latest), more, err)
	}
	span := func(rows []model.Message) (uuid.UUID, uuid.UUID, int) {
		if len(rows) == 0 {
			return uuid.Nil, uuid.Nil, 0
		}
		return rows[0].ID, rows[len(rows)-1].ID, len(rows)
	}

	// Through message 3: rows 3..24, oldest first; older ones (0..2) remain.
	rows, more, err := mr.ListByConversationThrough(ctx, tenantID, conv.ID, 1000, ids[25], ids[3])
	if err != nil {
		t.Fatal(err)
	}
	if first, last, n := span(rows); first != ids[3] || last != ids[24] || n != 22 || !more {
		t.Fatalf("through ids[3]: first=%v last=%v n=%d more=%v", first == ids[3], last == ids[24], n, more)
	}

	// Through the very first message: nothing older is left.
	rows, more, err = mr.ListByConversationThrough(ctx, tenantID, conv.ID, 1000, ids[25], ids[0])
	if first, _, n := span(rows); err != nil || first != ids[0] || n != 25 || more {
		t.Fatalf("through ids[0]: n=%d more=%v err=%v", n, more, err)
	}

	// through=first (uuid.Nil): down to the start of the chat.
	rows, more, err = mr.ListByConversationThrough(ctx, tenantID, conv.ID, 1000, ids[25], uuid.Nil)
	if first, last, n := span(rows); err != nil || first != ids[0] || last != ids[24] || n != 25 || more {
		t.Fatalf("through first: n=%d more=%v err=%v", n, more, err)
	}

	// A gap larger than the limit: the newest `limit` rows of the gap, and
	// more to come (the page asks again from the new oldest row).
	rows, more, err = mr.ListByConversationThrough(ctx, tenantID, conv.ID, 10, ids[25], ids[3])
	if first, last, n := span(rows); err != nil || first != ids[15] || last != ids[24] || n != 10 || !more {
		t.Fatalf("limited gap: n=%d more=%v err=%v", n, more, err)
	}

	// A message of ANOTHER conversation is no bound at all: an ordinary page.
	rows, more, err = mr.ListByConversationThrough(ctx, tenantID, conv.ID, 4, ids[25], foreign.ID)
	if first, last, n := span(rows); err != nil || first != ids[21] || last != ids[24] || n != 4 || !more {
		t.Fatalf("foreign through: n=%d more=%v err=%v", n, more, err)
	}

	// Another tenant cannot use it to read this chat.
	rows, _, err = mr.ListByConversationThrough(ctx, uuid.New(), conv.ID, 1000, uuid.Nil, ids[3])
	if err != nil || len(rows) != 0 {
		t.Fatalf("other tenant: n=%d err=%v", len(rows), err)
	}
}
