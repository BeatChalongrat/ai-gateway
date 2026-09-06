package repository_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// FIX74 — the parked-turn store. One pending question per conversation is
// a property of the data; the claim is first-wins; close is idempotent.
func TestPendingQuestionRepo_OnePerConversation_ClaimFirstWins(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	pq := repository.NewPendingQuestionRepo(pool)
	tenantID, userID := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Skipf("conv create (schema?): %v", err)
	}
	asst, err := mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenantID, UserID: userID, Role: "assistant", Status: "streaming"})
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	row := &model.PendingQuestion{TenantID: tenantID, UserID: userID, ConversationID: conv.ID, MessageID: asst.ID, ToolCallID: "call_1",
		Questions: []byte(`[{"question":"Which?","header":"Which","multiSelect":false,"options":[{"label":"A","description":""},{"label":"B","description":""}]}]`),
		State: []byte(`{"iteration":0}`), ExpiresAt: now.Add(24 * time.Hour)}
	created, err := pq.Create(ctx, row)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	if created.Status != model.PendingQuestionPending || created.ID == uuid.Nil {
		t.Fatalf("created row: %+v", created)
	}
	// One card at a time: a second pending row in the same conversation is refused by the index.
	dup := *row
	dup.ID = uuid.Nil
	if _, err := pq.Create(ctx, &dup); err == nil {
		t.Fatal("a second pending question in the same conversation must be refused")
	}
	got, err := pq.PendingForConversation(ctx, tenantID, conv.ID)
	if err != nil || got.ID != created.ID {
		t.Fatalf("pending lookup: %v %+v", err, got)
	}
	// First answer wins.
	if _, err := pq.Claim(ctx, tenantID, created.ID, model.PendingQuestionAnswered, now); err != nil {
		t.Fatalf("first claim: %v", err)
	}
	if _, err := pq.Claim(ctx, tenantID, created.ID, model.PendingQuestionSkipped, now); !errors.Is(err, repository.ErrQuestionNotPending) {
		t.Fatalf("second claim must lose: %v", err)
	}
	if _, err := pq.PendingForConversation(ctx, tenantID, conv.ID); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("nothing pending after the claim: %v", err)
	}
	// Close is idempotent on a settled row; a new pending row can now be created and closed by conversation.
	if closed, err := pq.Close(ctx, created.ID, model.PendingQuestionCancelled, now); err != nil || closed {
		t.Fatalf("close on a settled row must be a no-op: %v %v", closed, err)
	}
	second, err := pq.Create(ctx, &model.PendingQuestion{TenantID: tenantID, UserID: userID, ConversationID: conv.ID, MessageID: asst.ID, ToolCallID: "call_2",
		Questions: row.Questions, State: row.State, ExpiresAt: now.Add(24 * time.Hour)})
	if err != nil {
		t.Fatalf("second pending after settle: %v", err)
	}
	sup, err := pq.CloseForConversation(ctx, tenantID, conv.ID, model.PendingQuestionSuperseded, now)
	if err != nil || sup.ID != second.ID || sup.Status != model.PendingQuestionSuperseded {
		t.Fatalf("close for conversation: %v %+v", err, sup)
	}
	// An expired-but-pending row is not claimable.
	third, err := pq.Create(ctx, &model.PendingQuestion{TenantID: tenantID, UserID: userID, ConversationID: conv.ID, MessageID: asst.ID, ToolCallID: "call_3",
		Questions: row.Questions, State: row.State, ExpiresAt: now.Add(-time.Minute)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pq.Claim(ctx, tenantID, third.ID, model.PendingQuestionAnswered, now); !errors.Is(err, repository.ErrQuestionNotPending) {
		t.Fatalf("an expired question must not be claimable: %v", err)
	}
	if third.IsPending(now) {
		t.Fatal("IsPending must be false past expires_at")
	}
}
