package repository_test

// FIX118 — the row says how the turn ended (migration 043).

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestMessageRepo_TerminalCauseRoundTrip(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	tenantID, userID := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Skipf("conv create (schema?): %v", err)
	}
	mk := func(role, status string) *model.Message {
		m, err := mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenantID, UserID: userID, Role: role, Status: status})
		if err != nil {
			t.Fatal(err)
		}
		return m
	}
	mk("user", "completed")
	failed := mk("assistant", "streaming")
	if err := mr.UpdateContent(ctx, failed.ID, "half", "error", nil, nil); err != nil {
		t.Fatal(err)
	}
	if err := mr.SetTerminal(ctx, failed.ID, "eof_without_terminal", "stream_interrupted"); err != nil {
		t.Fatalf("set terminal: %v", err)
	}
	ok := mk("assistant", "streaming")
	if err := mr.UpdateContent(ctx, ok.ID, "fine", "completed", nil, nil); err != nil {
		t.Fatal(err)
	}
	if err := mr.SetTerminal(ctx, ok.ID, "completed", ""); err != nil {
		t.Fatal(err)
	}
	orphan := mk("assistant", "streaming")
	if err := mr.MarkInterrupted(ctx, orphan.ID); err != nil {
		t.Fatal(err)
	}
	// MarkInterrupted is guarded: a settled row keeps its own ending.
	if err := mr.MarkInterrupted(ctx, ok.ID); err != nil {
		t.Fatal(err)
	}

	rows, _, err := mr.ListByConversation(ctx, tenantID, conv.ID, 10, uuid.Nil)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	byID := map[uuid.UUID]model.Message{}
	for _, r := range rows {
		byID[r.ID] = r
	}
	str := func(p *string) string {
		if p == nil {
			return "<nil>"
		}
		return *p
	}
	if r := byID[failed.ID]; r.Status != "error" || str(r.TerminalCause) != "eof_without_terminal" || str(r.ErrorCode) != "stream_interrupted" {
		t.Fatalf("failed row: status=%s cause=%s code=%s", r.Status, str(r.TerminalCause), str(r.ErrorCode))
	}
	if r := byID[ok.ID]; r.Status != "completed" || str(r.TerminalCause) != "completed" || r.ErrorCode != nil {
		t.Fatalf("completed row: status=%s cause=%s code=%s", r.Status, str(r.TerminalCause), str(r.ErrorCode))
	}
	if r := byID[orphan.ID]; r.Status != "error" || str(r.TerminalCause) != "interrupted" || str(r.ErrorCode) != "GENERATION_INTERRUPTED" {
		t.Fatalf("interrupted row: status=%s cause=%s code=%s", r.Status, str(r.TerminalCause), str(r.ErrorCode))
	}
	// A value outside the closed set is refused by the database itself.
	if err := mr.SetTerminal(ctx, ok.ID, "failed", ""); err == nil {
		t.Fatal("terminal_cause outside the closed set must be refused by the CHECK constraint")
	}
}

func TestMessageRepo_CreateImportedCarriesTheEnding(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	tenantID, userID := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Skipf("conv create (schema?): %v", err)
	}
	cause, code := "disconnected", "stream_interrupted"
	at := time.Date(2026, 9, 14, 12, 0, 0, 0, time.UTC)
	if _, _, err := mr.CreateImported(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenantID, UserID: userID, Role: "assistant",
		ContentMasked: "partial", Status: "error", TerminalCause: &cause, ErrorCode: &code}, "asst_1", at); err != nil {
		t.Fatalf("import errored row: %v", err)
	}
	// The pre-FIX118 shape (no status) is still a completed row.
	if _, _, err := mr.CreateImported(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenantID, UserID: userID, Role: "assistant",
		ContentMasked: "fine"}, "asst_2", at.Add(time.Second)); err != nil {
		t.Fatalf("import plain row: %v", err)
	}
	var status, gotCause, gotCode *string
	if err := pool.QueryRow(ctx, "SELECT status, terminal_cause, error_code FROM chat.messages_masked WHERE client_id='asst_1'").Scan(&status, &gotCause, &gotCode); err != nil {
		t.Fatal(err)
	}
	if *status != "error" || gotCause == nil || *gotCause != cause || gotCode == nil || *gotCode != code {
		t.Fatalf("imported errored row lost its ending: %v %v %v", *status, gotCause, gotCode)
	}
	if err := pool.QueryRow(ctx, "SELECT status, terminal_cause, error_code FROM chat.messages_masked WHERE client_id='asst_2'").Scan(&status, &gotCause, &gotCode); err != nil {
		t.Fatal(err)
	}
	if *status != "completed" || gotCause != nil || gotCode != nil {
		t.Fatalf("plain import must stay completed with no cause: %v %v %v", *status, gotCause, gotCode)
	}
}
