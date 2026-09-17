package service

// FIX118 Part C — import carries the ending with it.
//
// Before: every imported row was 'completed' by SQL literal, so a turn
// that failed or was stopped on the desktop became a success the moment
// it reached the server. After: an assistant item may say status +
// terminal_cause + error_code, and the row keeps them; a user row never
// carries an ending; values outside the closed sets are refused (422).

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestImport_CarriesStatusCauseAndCode(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	fr := repository.NewFileRepo(pool)
	svc := NewImportService(pool, cr, mr, fr, NewConversationService(cr, importTestModels{}))
	tenantID, userID := uuid.New(), uuid.New()
	conv, _, err := svc.ImportConversation(ctx, ImportConversationInput{TenantID: tenantID, UserID: userID, ClientID: "desk-" + uuid.NewString(), Title: "t", ModelID: "m"})
	if err != nil {
		t.Skipf("import conversation (schema?): %v", err)
	}
	at := time.Date(2026, 9, 14, 12, 0, 0, 0, time.UTC)
	batch := []ImportMessageInput{
		{ClientID: "u1", Role: "user", Content: "q1", CreatedAt: at, Status: "error", TerminalCause: "timeout", ErrorCode: "x"}, // a user row's ending is ignored
		{ClientID: "a1", Role: "assistant", Content: "fine", CreatedAt: at.Add(1 * time.Second)},                                // pre-FIX118 shape
		{ClientID: "u2", Role: "user", Content: "q2", CreatedAt: at.Add(2 * time.Second)},
		{ClientID: "a2", Role: "assistant", Content: "half an", CreatedAt: at.Add(3 * time.Second), Status: "error", TerminalCause: "eof_without_terminal", ErrorCode: "stream_interrupted"},
		{ClientID: "u3", Role: "user", Content: "q3", CreatedAt: at.Add(4 * time.Second)},
		{ClientID: "a3", Role: "assistant", Content: "stopped he", CreatedAt: at.Add(5 * time.Second), Status: "cancelled"},
		{ClientID: "u4", Role: "user", Content: "q4", CreatedAt: at.Add(6 * time.Second)},
		{ClientID: "a4", Role: "assistant", Content: "", CreatedAt: at.Add(7 * time.Second), Status: "error", ErrorCode: "provider_timeout"}, // cause derived from status
		{ClientID: "u5", Role: "user", Content: "q5", CreatedAt: at.Add(8 * time.Second)},
		// FIX119 — a new desktop says completed explicitly and carries the run id + why the model stopped
		{ClientID: "a5", Role: "assistant", Content: "cut off he", CreatedAt: at.Add(9 * time.Second), Status: "completed", RunID: "8f1d2c3b-4e5f-4a6b-9c7d-0e1f2a3b4c5d", StopReason: "max_tokens"},
	}
	imported, skipped, _, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, batch)
	if err != nil || imported != 10 || skipped != 0 {
		t.Fatalf("import: imported=%d skipped=%d err=%v", imported, skipped, err)
	}
	type row struct {
		status string
		cause  *string
		code   *string
	}
	get := func(clientID string) row {
		var r row
		if err := pool.QueryRow(ctx, "SELECT status, terminal_cause, error_code FROM chat.messages_masked WHERE conversation_id=$1 AND client_id=$2", conv.ID, clientID).Scan(&r.status, &r.cause, &r.code); err != nil {
			t.Fatalf("%s: %v", clientID, err)
		}
		return r
	}
	s := func(p *string) string {
		if p == nil {
			return "<nil>"
		}
		return *p
	}
	want := map[string][3]string{
		"u1": {"completed", "<nil>", "<nil>"},
		// FIX119 C3 — an item that says nothing about its ending (e48) keeps
		// terminal_cause NULL: FIX118 wrote `completed` here.
		"a1": {"completed", "<nil>", "<nil>"},
		"a2": {"error", "eof_without_terminal", "stream_interrupted"},
		"a3": {"cancelled", "stopped_by_user", "<nil>"},
		"a4": {"error", "error", "provider_timeout"},
		"a5": {"completed", "completed", "<nil>"},
	}
	for id, w := range want {
		r := get(id)
		if r.status != w[0] || s(r.cause) != w[1] || s(r.code) != w[2] {
			t.Fatalf("%s: got %s/%s/%s want %v", id, r.status, s(r.cause), s(r.code), w)
		}
	}
	// FIX119 — run_id and stop_reason reach the row; a user row carries neither.
	var runID, stop *string
	if err := pool.QueryRow(ctx, "SELECT run_id, stop_reason FROM chat.messages_masked WHERE conversation_id=$1 AND client_id='a5'", conv.ID).Scan(&runID, &stop); err != nil {
		t.Fatal(err)
	}
	if runID == nil || *runID != "8f1d2c3b-4e5f-4a6b-9c7d-0e1f2a3b4c5d" || stop == nil || *stop != "max_tokens" {
		t.Fatalf("a5 lost its run id / stop reason: %v %v", runID, stop)
	}
	// A replay of the same batch is still a no-op.
	if imported, skipped, _, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, batch); err != nil || imported != 0 || skipped != 10 {
		t.Fatalf("replay: imported=%d skipped=%d err=%v", imported, skipped, err)
	}

	for name, bad := range map[string]ImportMessageInput{
		"status outside the set":      {ClientID: "b1", Role: "assistant", Content: "x", CreatedAt: at, Status: "streaming"},
		"cause outside the set":       {ClientID: "b2", Role: "assistant", Content: "x", CreatedAt: at, Status: "error", TerminalCause: "failed"},
		"code that is not a code":     {ClientID: "b3", Role: "assistant", Content: "x", CreatedAt: at, Status: "error", ErrorCode: "stream error: read tcp: connection reset"},
		"stop reason outside the set": {ClientID: "b4", Role: "assistant", Content: "x", CreatedAt: at, Status: "completed", StopReason: "length"},
		"run id with a bad shape":     {ClientID: "b5", Role: "assistant", Content: "x", CreatedAt: at, Status: "completed", RunID: "run id with spaces"},
	} {
		if _, _, _, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, []ImportMessageInput{bad}); !errors.Is(err, ErrImportInvalid) {
			t.Fatalf("%s: want ErrImportInvalid, got %v", name, err)
		}
	}
}
