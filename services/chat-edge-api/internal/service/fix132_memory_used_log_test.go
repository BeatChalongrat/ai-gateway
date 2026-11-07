package service

// FIX132 A — the web page no longer badges an answer "ใช้ความจำ N รายการ",
// so the record of which saved memories went into a turn is the server's:
// the saved_memory rows in response_context_sources, the memory_used frame
// in the run ledger, and one log line per turn naming the memory ids. The
// line carries ids only — never a memory's text (labels are the user's own
// words, e.g. their name or an allergy).

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func captureSlogJSON(t *testing.T) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(&buf, &slog.HandlerOptions{Level: slog.LevelDebug})))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return &buf
}

func TestFIX132_MemoryUsedIsLoggedWithIdsOnly(t *testing.T) {
	buf := captureSlogJSON(t)
	msg := uuid.New()
	acc := NewSourcesAccumulator()
	acc.Add(
		ContextSource{SourceType: model.SourceTypeSavedMemory, SourceID: "11111111-1111-1111-1111-111111111111", DisplayLabel: "ชื่อเล่นว่าบีท"},
		ContextSource{SourceType: model.SourceTypeConversationState, SourceID: "c", DisplayLabel: "state"},
		ContextSource{SourceType: model.SourceTypeSavedMemory, SourceID: "22222222-2222-2222-2222-222222222222", DisplayLabel: "แพ้กุ้ง"},
	)
	ch := make(chan StreamUpdate, 1)
	emitMemoryUsed(ch, msg, acc)
	if u := <-ch; u.MemoryUsed == nil || u.MemoryUsed.Count != 2 {
		t.Fatalf("the frame must still be sent: %+v", u)
	}

	var line map[string]any
	for _, l := range strings.Split(strings.TrimSpace(buf.String()), "\n") {
		var m map[string]any
		if json.Unmarshal([]byte(l), &m) == nil && m["msg"] == "memory_used" {
			line = m
		}
	}
	if line == nil {
		t.Fatalf("want one `memory_used` log line, got:\n%s", buf.String())
	}
	if line["message_id"] != msg.String() {
		t.Fatalf("message_id = %v", line["message_id"])
	}
	if line["count"] != float64(2) {
		t.Fatalf("count = %v", line["count"])
	}
	ids, _ := line["memory_ids"].([]any)
	if len(ids) != 2 || ids[0] != "11111111-1111-1111-1111-111111111111" || ids[1] != "22222222-2222-2222-2222-222222222222" {
		t.Fatalf("memory_ids = %v", line["memory_ids"])
	}
	if strings.Contains(buf.String(), "บีท") || strings.Contains(buf.String(), "แพ้กุ้ง") {
		t.Fatalf("the log must not carry memory text:\n%s", buf.String())
	}
}

func TestFIX132_NoMemoryNoLogLine(t *testing.T) {
	buf := captureSlogJSON(t)
	ch := make(chan StreamUpdate, 1)
	emitMemoryUsed(ch, uuid.New(), NewSourcesAccumulator())
	if strings.Contains(buf.String(), `"msg":"memory_used"`) {
		t.Fatalf("a turn that read no memory logs nothing:\n%s", buf.String())
	}
}
