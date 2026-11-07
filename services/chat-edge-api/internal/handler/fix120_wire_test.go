package handler

// FIX120 — the requested model of a rescued turn rides the list wire and
// the terminal event, and is absent when there was no swap.

import (
	"encoding/json"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func TestFallbackFrom_OnTheListWireOnlyWhenSet(t *testing.T) {
	from := "gpt-5.6-luna"
	served := "qwen3.8-27b-fp8"
	m := &model.Message{Role: "assistant", ContentMasked: "a", Status: "completed", ModelID: &served, FallbackFrom: &from}
	raw, err := json.Marshal(messageToWire(m))
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got["fallback_from"] != "gpt-5.6-luna" || got["model_id"] != "qwen3.8-27b-fp8" {
		t.Fatalf("wire: %v", got)
	}

	empty := ""
	for _, v := range []*string{nil, &empty} {
		raw, _ := json.Marshal(messageToWire(&model.Message{Role: "assistant", ContentMasked: "a", Status: "completed", ModelID: &served, FallbackFrom: v}))
		var got map[string]any
		_ = json.Unmarshal(raw, &got)
		if _, present := got["fallback_from"]; present {
			t.Fatalf("fallback_from must be omitted when %v: %v", v, got)
		}
	}
}

func TestCompleteWithFallback_AddsTheFieldOnlyWhenSet(t *testing.T) {
	data := map[string]any{"message_id": "x"}
	completeWithModel(data, "qwen3.8-27b-fp8")
	completeWithFallback(data, "")
	if _, ok := data["fallback_from"]; ok {
		t.Fatalf("no swap → no field: %v", data)
	}
	completeWithFallback(data, "gpt-5.6-luna")
	if data["fallback_from"] != "gpt-5.6-luna" || data["model_id"] != "qwen3.8-27b-fp8" {
		t.Fatalf("complete: %v", data)
	}
}
