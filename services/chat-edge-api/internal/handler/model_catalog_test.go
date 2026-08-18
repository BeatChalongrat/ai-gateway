package handler

import (
	"context"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// Models-catalog steps 2-4: gateway display data wins over the static
// metadata for prices, fills context for unknown models, surfaces the
// DB-authoritative tools flag, and drives reasoning capability.

func newTestHandler() *ModelHandler {
	return NewModelHandler(&config.Config{}, nil)
}

func TestGatewayPricesWinOverStaticMap(t *testing.T) {
	h := newTestHandler()
	// gpt-4o-mini has a curated static metadata entry with its own
	// prices; the gateway-supplied (DB) prices must win.
	out, _ := h.buildFromGateway([]service.GatewayModelInfo{{
		ID: "gpt-4o-mini", Provider: "openai",
		InputPricePer1M: 0.42, OutputPricePer1M: 1.68,
	}})
	if len(out) != 1 {
		t.Fatalf("expected 1 model, got %d", len(out))
	}
	if out[0].InputPricePerM != 0.42 || out[0].OutputPricePerM != 1.68 {
		t.Fatalf("gateway prices did not win: in=%v out=%v",
			out[0].InputPricePerM, out[0].OutputPricePerM)
	}
}

func TestStaticPricesRemainWhenGatewayOmitsThem(t *testing.T) {
	h := newTestHandler()
	out, _ := h.buildFromGateway([]service.GatewayModelInfo{{
		ID: "gpt-4o-mini", Provider: "openai",
	}})
	meta := modelMetadata["gpt-4o-mini"]
	if out[0].InputPricePerM != meta.InputPricePerM {
		t.Fatalf("static fallback price lost: got %v want %v",
			out[0].InputPricePerM, meta.InputPricePerM)
	}
}

func TestGatewayContextWins(t *testing.T) {
	h := newTestHandler()
	out, _ := h.buildFromGateway([]service.GatewayModelInfo{
		// Unknown to the static map -> gateway context fills in.
		{ID: "gpt-6-preview", Provider: "openai", ContextWindow: 400000},
		// Curated entry too: model_configs is the context authority
		// since migration 076 corrected the rows that carried
		// max-OUTPUT values, so gateway data wins everywhere.
		{ID: "gpt-4o-mini", Provider: "openai", ContextWindow: 128000},
		// Gateway silent on context -> static fallback survives.
		{ID: "gpt-4o", Provider: "openai"},
	})
	if out[0].ContextWindow != 400000 {
		t.Fatalf("discovered model context = %d, want 400000", out[0].ContextWindow)
	}
	if out[1].ContextWindow != 128000 {
		t.Fatalf("curated model context = %d, want gateway 128000", out[1].ContextWindow)
	}
	if want := modelMetadata["gpt-4o"].ContextWindow; out[2].ContextWindow != want {
		t.Fatalf("static fallback context lost: got %d want %d", out[2].ContextWindow, want)
	}
}

func TestGatewayToolsFlagAppendsCapabilityString(t *testing.T) {
	h := newTestHandler()
	out, gwTools := h.buildFromGateway([]service.GatewayModelInfo{{
		ID: "gpt-6-preview", Provider: "openai",
		Capabilities: &service.GatewayModelCapabilities{Tools: true, Vision: true},
	}})
	if !containsCap(out[0].Capabilities, "function_calling") {
		t.Fatalf("function_calling not appended: %v", out[0].Capabilities)
	}
	if !containsCap(out[0].Capabilities, "vision") {
		t.Fatalf("vision not appended: %v", out[0].Capabilities)
	}
	if !gwTools["gpt-6-preview"] {
		t.Fatal("gwTools map missing the DB tools flag")
	}
}

func TestKnownWithoutToolsPrefersGatewayFlag(t *testing.T) {
	h := newTestHandler()
	// Simulate a completed gateway fetch: cached catalog + gwTools map.
	out, gwTools := h.buildFromGateway([]service.GatewayModelInfo{
		{ID: "no-tools-model", Provider: "openai",
			Capabilities: &service.GatewayModelCapabilities{Streaming: true}},
		{ID: "tools-model", Provider: "openai",
			Capabilities: &service.GatewayModelCapabilities{Tools: true}},
	})
	h.mu.Lock()
	h.cached = out
	h.gwTools = gwTools
	h.mu.Unlock()

	ctx := context.Background()
	if !h.KnownWithoutTools(ctx, "no-tools-model") {
		t.Fatal("DB tools=false model should be known-without-tools")
	}
	if h.KnownWithoutTools(ctx, "tools-model") {
		t.Fatal("DB tools=true model must keep its tools")
	}
	// Not reported by the gateway -> static metadata fallback (o1 has a
	// curated entry without function_calling).
	if !h.KnownWithoutTools(ctx, "o1") {
		t.Fatal("static fallback lost: o1 should be known-without-tools")
	}
	// Unknown everywhere -> capability unknown -> keep tools.
	if h.KnownWithoutTools(ctx, "totally-unknown") {
		t.Fatal("unknown id must not be stripped of tools")
	}
}

func TestReasoningCapableReadsCatalog(t *testing.T) {
	h := newTestHandler()
	out, gwTools := h.buildFromGateway([]service.GatewayModelInfo{
		{ID: "deepseek/deepseek-r1", Provider: "openrouter",
			Reasoning: &service.GatewayReasoningCapability{Supported: true}},
		{ID: "gpt-4o-mini", Provider: "openai"},
	})
	h.mu.Lock()
	h.cached = out
	h.gwTools = gwTools
	h.mu.Unlock()

	ctx := context.Background()
	if !h.ReasoningCapable(ctx, "deepseek/deepseek-r1") {
		t.Fatal("DB-onboarded reasoning model not recognised from the catalog")
	}
	if h.ReasoningCapable(ctx, "gpt-4o-mini") {
		t.Fatal("non-reasoning model wrongly reported capable")
	}
	if h.ReasoningCapable(ctx, "") {
		t.Fatal("empty id must be false")
	}
}
