package service

// FIX132 F12 — a message hit from GET /search names the message, so the
// page can open the chat at it (it only had the conversation id, and opened
// every hit at the bottom).

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

func TestFIX132_SearchMessageHitCarriesTheMessageID(t *testing.T) {
	pool := dlPool(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	tenantID, userID := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m", Title: "t"})
	if err != nil {
		testdeps.Missing(t, "conv create (schema?): %v", err)
	}
	_, _ = mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenantID, UserID: userID, Role: "user", ContentMasked: "hello there", Status: "completed"})
	hit, err := mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenantID, UserID: userID, Role: "assistant", ContentMasked: "the zzfix132needle answer", Status: "completed"})
	if err != nil {
		t.Fatal(err)
	}
	res, err := NewSearchService(mr, cr, repository.NewMemoryRepo(pool)).SearchPastContext(ctx, userID, tenantID, "zzfix132needle", ScopeAll, 20)
	if err != nil {
		t.Fatal(err)
	}
	var got *PastContextResult
	for i := range res {
		if res[i].Type == "message" {
			got = &res[i]
		}
	}
	if got == nil {
		testdeps.Missing(t, "no message hit (tsvector trigger absent in this DB?)")
	}
	if got.MessageID != hit.ID.String() || got.ConversationID != conv.ID.String() {
		t.Fatalf("hit = %+v, want message %s", *got, hit.ID)
	}
	for i := range res {
		if res[i].Type != "message" && res[i].MessageID != "" {
			t.Fatalf("only message hits name a message: %+v", res[i])
		}
	}
}
