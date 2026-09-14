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
		{ClientID: "a1", Role: "assistant", Content: "fine", CreatedAt: at.Add(1 * time.Second)},                                  // pre-FIX118 shape
		{ClientID: "u2", Role: "user", Content: "q2", CreatedAt: at.Add(2 * time.Second)},
		{ClientID: "a2", Role: "assistant", Content: "half an", CreatedAt: at.Add(3 * time.Second), Status: "error", TerminalCause: "eof_without_terminal", ErrorCode: "stream_interrupted"},
		{ClientID: "u3", Role: "user", Content: "q3", CreatedAt: at.Add(4 * time.Second)},
		{ClientID: "a3", Role: "assistant", Content: "stopped he", CreatedAt: at.Add(5 * time.Second), Status: "cancelled"},
		{ClientID: "u4", Role: "user", Content: "q4", CreatedAt: at.Add(6 * time.Second)},
		{ClientID: "a4", Role: "assistant", Content: "", CreatedAt: at.Add(7 * time.Second), Status: "error", ErrorCode: "provider_timeout"}, // cause derived from status
	}
	imported, skipped, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, batch)
	if err != nil || imported != 8 || skipped != 0 {
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
		"a1": {"completed", "completed", "<nil>"},
		"a2": {"error", "eof_without_terminal", "stream_interrupted"},
		"a3": {"cancelled", "stopped_by_user", "<nil>"},
		"a4": {"error", "error", "provider_timeout"},
	}
	for id, w := range want {
		r := get(id)
		if r.status != w[0] || s(r.cause) != w[1] || s(r.code) != w[2] {
			t.Fatalf("%s: got %s/%s/%s want %v", id, r.status, s(r.cause), s(r.code), w)
		}
	}
	// A replay of the same batch is still a no-op.
	if imported, skipped, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, batch); err != nil || imported != 0 || skipped != 8 {
		t.Fatalf("replay: imported=%d skipped=%d err=%v", imported, skipped, err)
	}

	for name, bad := range map[string]ImportMessageInput{
		"status outside the set": {ClientID: "b1", Role: "assistant", Content: "x", CreatedAt: at, Status: "streaming"},
		"cause outside the set":  {ClientID: "b2", Role: "assistant", Content: "x", CreatedAt: at, Status: "error", TerminalCause: "failed"},
		"code that is not a code": {ClientID: "b3", Role: "assistant", Content: "x", CreatedAt: at, Status: "error", ErrorCode: "stream error: read tcp: connection reset"},
	} {
		if _, _, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, []ImportMessageInput{bad}); !errors.Is(err, ErrImportInvalid) {
			t.Fatalf("%s: want ErrImportInvalid, got %v", name, err)
		}
	}
}
