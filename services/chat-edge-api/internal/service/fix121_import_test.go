package service

// FIX121 C4 + C5 — the import mirrors a turn that already happened.
//
// Before: a message above 128 KiB (about 43,000 Thai characters) failed the
// WHOLE batch with 422 before the transaction opened, so a long desktop turn
// — the question and the answer — never reached the server while the person
// looked at a normal reply. After: there is no content ceiling (the request
// ceiling is the handler's, handler/fix121_import_http_test.go), a bad item
// is rejected BY NAME while its neighbours are stored, and only a request
// that has nothing storable is refused as a whole.

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func fix121ImportSvc(t *testing.T) (*ImportService, *repository.MessageRepo, uuid.UUID, uuid.UUID, uuid.UUID) {
	t.Helper()
	pool := testdb.Fresh(t)
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	fr := repository.NewFileRepo(pool)
	svc := NewImportService(pool, cr, mr, fr, NewConversationService(cr, importTestModels{}))
	tenantID, userID := uuid.New(), uuid.New()
	conv, _, err := svc.ImportConversation(context.Background(), ImportConversationInput{
		TenantID: tenantID, UserID: userID, ClientID: "desk-" + uuid.NewString(), Title: "t", ModelID: "m",
	})
	if err != nil {
		t.Fatalf("import conversation: %v", err)
	}
	return svc, mr, tenantID, userID, conv.ID
}

func TestFix121Import_ALongThaiTurnIsKeptWhole(t *testing.T) {
	svc, mr, tenantID, userID, convID := fix121ImportSvc(t)
	ctx := context.Background()
	// 50,000 Thai characters = 150,000 bytes: over the old 131,072 ceiling.
	long := strings.Repeat("ก", 50_000)
	if len(long) <= 128*1024 {
		t.Fatalf("fixture must exceed the old ceiling, is %d bytes", len(long))
	}
	at := time.Date(2026, 9, 17, 9, 0, 0, 0, time.UTC)
	imported, skipped, rejected, err := svc.ImportMessages(ctx, tenantID, userID, convID, []ImportMessageInput{
		{ClientID: "u_long", Role: "user", Content: long, CreatedAt: at},
		{ClientID: "a_long", Role: "assistant", Content: "สรุปให้แล้วครับ", CreatedAt: at.Add(time.Second), Status: "completed"},
	})
	if err != nil || imported != 2 || skipped != 0 || len(rejected) != 0 {
		t.Fatalf("imported=%d skipped=%d rejected=%v err=%v", imported, skipped, rejected, err)
	}
	rows, _, err := mr.ListByConversation(ctx, tenantID, convID, 10, uuid.Nil)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	kept := false
	for _, m := range rows {
		if m.Role == "user" && m.ContentMasked == long {
			kept = true
		}
	}
	if !kept {
		t.Fatalf("the long message did not come back byte for byte")
	}
}

func TestFix121Import_OneBadItemIsNamedAndItsNeighboursAreStored(t *testing.T) {
	svc, _, tenantID, userID, convID := fix121ImportSvc(t)
	ctx := context.Background()
	at := time.Date(2026, 9, 17, 9, 0, 0, 0, time.UTC)
	imported, skipped, rejected, err := svc.ImportMessages(ctx, tenantID, userID, convID, []ImportMessageInput{
		{ClientID: "u1", Role: "user", Content: "what the person said", CreatedAt: at},
		{ClientID: "a1", Role: "assistant", Content: "x", CreatedAt: at.Add(time.Second), Status: "exploded"},
		{ClientID: "u2", Role: "user", Content: "next", CreatedAt: at.Add(2 * time.Second)},
		{ClientID: "a2", Role: "assistant", Content: "y", CreatedAt: at.Add(3 * time.Second), Invalid: "file_ids must be UUIDs"},
	})
	if err != nil {
		t.Fatalf("a batch with storable items must not be refused: %v", err)
	}
	if imported != 2 || skipped != 0 {
		t.Fatalf("imported=%d skipped=%d, want 2 / 0", imported, skipped)
	}
	if len(rejected) != 2 ||
		rejected[0].Index != 1 || rejected[0].ClientID != "a1" || !strings.Contains(rejected[0].Reason, "status") ||
		rejected[1].Index != 3 || rejected[1].ClientID != "a2" || rejected[1].Reason != "file_ids must be UUIDs" {
		t.Fatalf("rejected = %+v", rejected)
	}
	// message_count follows what was stored, not what was sent
	var count int
	_ = svc.pool.QueryRow(ctx, "SELECT message_count FROM chat.conversations WHERE id = $1", convID).Scan(&count)
	if count != 2 {
		t.Fatalf("message_count = %d, want 2", count)
	}
	// replaying the batch is still idempotent, and still names the same two
	imported, skipped, rejected, err = svc.ImportMessages(ctx, tenantID, userID, convID, []ImportMessageInput{
		{ClientID: "u1", Role: "user", Content: "what the person said", CreatedAt: at},
		{ClientID: "a1", Role: "assistant", Content: "x", CreatedAt: at.Add(time.Second), Status: "exploded"},
	})
	if err != nil || imported != 0 || skipped != 1 || len(rejected) != 1 {
		t.Fatalf("replay: imported=%d skipped=%d rejected=%v err=%v", imported, skipped, rejected, err)
	}
}

func TestFix121Import_NothingStorableIsStillRefusedAsAWhole(t *testing.T) {
	svc, _, tenantID, userID, convID := fix121ImportSvc(t)
	at := time.Date(2026, 9, 17, 9, 0, 0, 0, time.UTC)
	_, _, _, err := svc.ImportMessages(context.Background(), tenantID, userID, convID, []ImportMessageInput{
		{ClientID: "t1", Role: "tool", Content: "r", CreatedAt: at},
		{ClientID: "", Role: "user", Content: "r", CreatedAt: at},
	})
	if !errors.Is(err, ErrImportInvalid) || !strings.Contains(err.Error(), "message 0") {
		t.Fatalf("want ErrImportInvalid naming the first item, got %v", err)
	}
}

func TestFix121Import_AttemptsRideAFailedTurnOnlyAndAreBounded(t *testing.T) {
	svc, mr, tenantID, userID, convID := fix121ImportSvc(t)
	ctx := context.Background()
	at := time.Date(2026, 9, 17, 9, 0, 0, 0, time.UTC)
	many := make([]any, 0, 40)
	for i := 0; i < 40; i++ {
		many = append(many, map[string]any{"provider": "p", "model": "m", "error_code": "provider_timeout", "error_message": strings.Repeat("e", 2000)})
	}
	imported, _, rejected, err := svc.ImportMessages(ctx, tenantID, userID, convID, []ImportMessageInput{
		{ClientID: "u1", Role: "user", Content: "q", CreatedAt: at, Attempts: many},
		{ClientID: "a1", Role: "assistant", Content: "", CreatedAt: at.Add(time.Second), Status: "error", ErrorCode: "all_models_failed", Attempts: many},
		{ClientID: "u2", Role: "user", Content: "q", CreatedAt: at.Add(2 * time.Second)},
		{ClientID: "a2", Role: "assistant", Content: "fine", CreatedAt: at.Add(3 * time.Second), Status: "completed", Attempts: many},
	})
	if err != nil || imported != 4 || len(rejected) != 0 {
		t.Fatalf("imported=%d rejected=%v err=%v", imported, rejected, err)
	}
	rows, _, err := mr.ListByConversation(ctx, tenantID, convID, 10, uuid.Nil)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	for _, m := range rows {
		client := ""
		if m.ClientID != nil {
			client = *m.ClientID
		}
		if client != "a1" {
			if len(m.Attempts) != 0 {
				t.Fatalf("row %s must carry no attempts, has %s", client, m.Attempts)
			}
			continue
		}
		var got []map[string]any
		if err := json.Unmarshal(m.Attempts, &got); err != nil {
			t.Fatalf("a1 attempts: %v (%s)", err, m.Attempts)
		}
		if len(got) != maxStoredAttempts {
			t.Fatalf("stored %d attempts, want the bound %d", len(got), maxStoredAttempts)
		}
		if n := len([]rune(got[0]["error_message"].(string))); n != maxAttemptText {
			t.Fatalf("error_message kept %d runes, want %d", n, maxAttemptText)
		}
	}
}

func TestFix121_NormalizeAttempts(t *testing.T) {
	for name, v := range map[string]any{
		"nil": nil, "not a list": "x", "empty": []any{}, "no names": []any{map[string]any{"error_code": "x"}},
		"wrong entries": []any{"a", 1.0},
	} {
		if got := NormalizeAttempts(v); got != nil {
			t.Fatalf("%s: want nothing stored, got %s", name, got)
		}
	}
	raw := NormalizeAttempts([]any{
		map[string]any{"provider": " sranai ", "model": "qwen", "error_code": "provider_timeout", "latency_ms": 30000.0, "secret": "nope", "status": 7},
	})
	var got []map[string]any
	if err := json.Unmarshal(raw, &got); err != nil || len(got) != 1 {
		t.Fatalf("unmarshal: %v %s", err, raw)
	}
	want := map[string]any{"provider": "sranai", "model": "qwen", "error_code": "provider_timeout", "latency_ms": 30000.0}
	if len(got[0]) != len(want) {
		t.Fatalf("kept %v, want exactly %v", got[0], want)
	}
	for k, v := range want {
		if got[0][k] != v {
			t.Fatalf("key %s = %v, want %v", k, got[0][k], v)
		}
	}
}
