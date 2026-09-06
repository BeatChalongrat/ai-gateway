package handler

import (
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// FIX74 — the wire: a parked turn ends on its own terminal, after the card.
func TestDrainStreamEvents_QuestionThenParked(t *testing.T) {
	rec := httptest.NewRecorder()
	done := make(chan struct{})
	events := make(chan service.StreamUpdate, 4)
	events <- service.StreamUpdate{Delta: "Let me ask. "}
	events <- service.StreamUpdate{Question: &service.QuestionUpdate{PromptID: "p-1", MessageID: "m-1", ToolCallID: "call_1", Questions: []service.AskQuestion{{Question: "Which?", Header: "Which", Options: []service.AskOption{{Label: "A"}, {Label: "B"}}}}}}
	events <- service.StreamUpdate{Parked: true, FinishReason: "parked"}
	close(events)

	res := drainStreamEvents(rec, rec, done, events, func() {})
	if !res.parked || res.parkedPromptID != "p-1" {
		t.Fatalf("drain must report the park: %+v", res)
	}
	if res.finishReason != "parked" {
		t.Fatalf("finishReason = %q", res.finishReason)
	}
	body := rec.Body.String()
	if !strings.Contains(body, "event: question") || !strings.Contains(body, `"promptId":"p-1"`) || !strings.Contains(body, `"header":"Which"`) {
		t.Fatalf("the question frame must carry the card:\n%s", body)
	}
	if res.content != "Let me ask. " {
		t.Fatalf("the text before the park is the row's prefix: %q", res.content)
	}
	if !isTerminalEvent("message_paused") {
		t.Fatal("message_paused must count as a terminal, or the run writer appends an error after it")
	}
}
