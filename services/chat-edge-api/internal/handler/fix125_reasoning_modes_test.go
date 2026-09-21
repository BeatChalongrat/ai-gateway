package handler

// FIX125 M3 — the model list offers "off" only where it is true.
//
// The Gateway knows each model's reasoning strategy; its modes list is
// passed through untouched, and an empty list is never padded with "off".
// The static fallback (Gateway unreachable) has no strategy, so it offers
// "off" only for Anthropic, whose thinking block is simply omitted.

import (
	"encoding/json"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

func hasMode(modes []string, want string) bool {
	for _, m := range modes {
		if m == want {
			return true
		}
	}
	return false
}

func TestFIX125_GatewayModesPassThrough(t *testing.T) {
	h := newTestHandler()
	out, _ := h.buildFromGateway([]service.GatewayModelInfo{
		{ID: "qwen3.8-27b-fp8", Provider: "sranai", Reasoning: &service.GatewayReasoningCapability{
			Supported: true, Modes: []string{"off", "auto"}, DefaultMode: "auto"}},
		{ID: "o3-mini", Provider: "openai", Reasoning: &service.GatewayReasoningCapability{
			Supported: true, Modes: []string{"auto", "low", "medium", "high"}}},
		// The Gateway said "reasons" but sent no modes: nothing invented.
		{ID: "deepseek/deepseek-r1", Provider: "openrouter", Reasoning: &service.GatewayReasoningCapability{Supported: true}},
	})
	got := map[string][]string{}
	for _, m := range out {
		if m.Reasoning != nil {
			got[m.ID] = m.Reasoning.Modes
		}
	}
	if b, _ := json.Marshal(got["qwen3.8-27b-fp8"]); string(b) != `["off","auto"]` {
		t.Errorf("qwen modes = %s, want the Gateway's own list", b)
	}
	if hasMode(got["o3-mini"], "off") {
		t.Errorf("o3-mini: Gateway sent no off, chat-edge added it: %v", got["o3-mini"])
	}
	if len(got["deepseek/deepseek-r1"]) == 0 || hasMode(got["deepseek/deepseek-r1"], "off") {
		t.Errorf("empty Gateway modes must not be padded with off: %v", got["deepseek/deepseek-r1"])
	}
}

func TestFIX125_StaticFallbackOffOnlyWhereTrue(t *testing.T) {
	h := NewModelHandler(&config.Config{AvailableModelsJSON: `[
		{"id":"claude-sonnet-4-20250514","name":"Claude Sonnet 4","provider":"anthropic"},
		{"id":"o3-mini","name":"o3-mini","provider":"openai"},
		{"id":"deepseek-reasoner","name":"DeepSeek R","provider":"deepseek"},
		{"id":"gemini-2.5-pro","name":"Gemini 2.5 Pro","provider":"google"}
	]`}, nil)
	seen := 0
	for _, m := range h.configCatalog() {
		if m.Reasoning == nil {
			continue
		}
		seen++
		off := hasMode(m.Reasoning.Modes, "off")
		if want := m.ID == "claude-sonnet-4-20250514"; off != want {
			t.Errorf("%s: off offered=%v, want %v (modes %v)", m.ID, off, want, m.Reasoning.Modes)
		}
		if !hasMode(m.Reasoning.Modes, "auto") {
			t.Errorf("%s: auto missing from %v", m.ID, m.Reasoning.Modes)
		}
	}
	if seen != 4 {
		t.Fatalf("expected 4 reasoning models in the static catalogue, got %d", seen)
	}
}
