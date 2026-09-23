package handler

// FIX130 — the HTTP side of the memory card: the memory_used / memory_saved
// frames on the live stream and on a reattach, the suggestion path of
// POST /memories (the desktop host's remember tool), and the 409 for
// switching memory on in a temporary chat.

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestFIX130_StreamCarriesMemoryFrames(t *testing.T) {
	used := &service.MemoryUsedNotice{MessageID: "m", Count: 1, Items: []service.MemoryNoticeItem{{ID: "a", Label: "ชื่อเล่นว่าบีท"}}}
	saved := &service.MemorySavedNotice{MessageID: "m", Items: []service.MemoryNoticeItem{{ID: "b", Label: "แพ้กุ้ง", Status: "pending_confirmation"}}}

	rec := httptest.NewRecorder()
	events := make(chan service.StreamUpdate, 4)
	events <- service.StreamUpdate{MemoryUsed: used}
	events <- service.StreamUpdate{Delta: "สวัสดีครับคุณบีท"}
	events <- service.StreamUpdate{MemorySaved: saved}
	events <- service.StreamUpdate{Done: true, FinishReason: "stop"}
	close(events)
	drainStreamEvents(rec, rec, make(chan struct{}), events, func() {})
	body := rec.Body.String()
	iUsed, iDelta := strings.Index(body, "event: memory_used"), strings.Index(body, "event: content_delta")
	if iUsed < 0 || iDelta < 0 || iUsed > iDelta {
		t.Fatalf("memory_used must be written before the answer:\n%s", body)
	}
	if !strings.Contains(body, "event: memory_saved") || !strings.Contains(body, `"status":"pending_confirmation"`) {
		t.Fatalf("memory_saved missing:\n%s", body)
	}

	// A reattach replays both from the snapshot.
	rec = httptest.NewRecorder()
	(&MessageHandler{}).writeResumeSnapshot(rec, rec, service.StreamSnapshot{MemoryUsed: used, MemorySaved: saved, Content: "x"})
	if b := rec.Body.String(); !strings.Contains(b, "event: memory_used") || !strings.Contains(b, "event: memory_saved") {
		t.Fatalf("resume snapshot lost the memory frames:\n%s", b)
	}
}

func TestFIX130_PostMemoryCanFileASuggestion(t *testing.T) {
	pool := testdb.Fresh(t)
	repo := repository.NewMemoryRepo(pool)
	h := NewMemoryHandler(service.NewMemoryService(repo))
	user, tenant, conv := uuid.New(), uuid.New(), uuid.New()
	post := func(body map[string]any) (*httptest.ResponseRecorder, map[string]any) {
		raw, _ := json.Marshal(body)
		r := httptest.NewRequest(http.MethodPost, "/api/v1/memories", bytes.NewReader(raw))
		r = r.WithContext(middleware.WithCaller(r.Context(), middleware.Caller{UserID: user, TenantUUID: tenant, TenantID: tenant.String()}))
		rec := httptest.NewRecorder()
		h.Create(rec, r)
		var out map[string]any
		_ = json.Unmarshal(rec.Body.Bytes(), &out)
		return rec, out
	}

	rec, out := post(map[string]any{
		"content": "แพ้กุ้ง", "scope_type": "global_user", "memory_type": "explicit_fact",
		"source_type": "assistant_inferred", "source_conversation_id": conv.String(), "evidence_excerpt": "ผมแพ้กุ้งนะ",
	})
	if rec.Code != http.StatusCreated || out["status"] != model.MemoryStatusPendingConfirmation ||
		out["source_type"] != model.MemorySourceAssistantInferred || out["source_conversation_id"] != conv.String() || out["evidence_excerpt"] != "ผมแพ้กุ้งนะ" {
		t.Fatalf("suggestion: %d %v", rec.Code, out)
	}
	// Without the lower tier it is the user's own explicit memory, as before
	// — and it can still name the chat it was saved from (the desktop's
	// remember tool), so the Memory page links back instead of "เพิ่มเอง".
	rec, out = post(map[string]any{"content": "ชื่อเล่นว่าบีท", "scope_type": "global_user", "memory_type": "explicit_fact",
		"source_conversation_id": conv.String()})
	if rec.Code != http.StatusCreated || out["status"] != model.MemoryStatusActive || out["source_type"] != model.MemorySourceUserExplicit ||
		out["source_conversation_id"] != conv.String() {
		t.Fatalf("explicit: %d %v", rec.Code, out)
	}
	// No caller can RAISE the tier through the field.
	rec, out = post(map[string]any{"content": "x1", "scope_type": "global_user", "memory_type": "explicit_fact", "source_type": "conversation_summary"})
	if rec.Code != http.StatusCreated || out["source_type"] != model.MemorySourceUserExplicit {
		t.Fatalf("unknown source_type: %d %v", rec.Code, out)
	}
	rec, _ = post(map[string]any{"content": "x2", "scope_type": "global_user", "memory_type": "explicit_fact",
		"source_type": "assistant_inferred", "source_conversation_id": "not-a-uuid"})
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("a bad source_conversation_id must be a 400, got %d", rec.Code)
	}
	_ = context.Background()
}

func TestFIX130_TemporaryChatMemoryOnIsAConflict(t *testing.T) {
	rec := httptest.NewRecorder()
	(&ConversationHandler{}).writeUpdateError(rec, service.ErrConversationTemporaryMemory)
	if rec.Code != http.StatusConflict {
		t.Fatalf("status %d, want 409", rec.Code)
	}
}
