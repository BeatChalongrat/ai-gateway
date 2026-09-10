package handler

import (
	"context"
	"testing"
	"time"
)

// FIX101 Part A — the catalogue side of the ceiling.
//
// The compiler now budgets against model_configs.max_context_tokens,
// which an operator edits in the admin console. Two things have to be
// true for that to mean anything: the number must be the one the Gateway
// actually sent (never a static guess wearing its clothes), and an
// absent number must read as "unknown", not as a default.

func TestFIX101_AdvertisedWindowIsOnlyEverWhatTheGatewaySent(t *testing.T) {
	h := &ModelHandler{
		// A populated cache with no gateway client: catalog() serves it
		// as-is, exactly as the FIX64/FIX65 flag tests do.
		cached: []modelResponse{
			// gpt-4o carries a STATIC ContextWindow of 128000 from
			// modelMetadata even when the Gateway sends nothing. If
			// AdvertisedContextWindow read the cached rows it would hand
			// the compiler that guess as though it were data — which is
			// the entire class of defect this round is about.
			{ID: "gpt-4o", ContextWindow: 128000},
			{ID: "Typhoon-S-ThaiLLM-8B-Instruct", ContextWindow: 32768},
		},
		gwContext: map[string]int{
			// ...and this is what the Gateway ACTUALLY sent: only Typhoon,
			// at the value an operator corrected it to.
			"Typhoon-S-ThaiLLM-8B-Instruct": 16384,
		},
	}
	h.cachedAt = time.Now()

	if got := h.AdvertisedContextWindow(context.Background(), "Typhoon-S-ThaiLLM-8B-Instruct"); got != 16384 {
		t.Errorf("Typhoon = %d, want the operator's 16384", got)
	}
	if got := h.AdvertisedContextWindow(context.Background(), "gpt-4o"); got != 0 {
		t.Errorf("gpt-4o = %d, want 0 — the Gateway sent no window, and the static 128000 in the cached row is a guess, not data", got)
	}
	if got := h.AdvertisedContextWindow(context.Background(), "a-model-nobody-has-heard-of"); got != 0 {
		t.Errorf("unknown id = %d, want 0", got)
	}
}

func TestFIX101_AutoSentinelIsNotAModel(t *testing.T) {
	h := &ModelHandler{
		cached:    []modelResponse{{ID: "auto", ContextWindow: 200000}},
		gwContext: map[string]int{"auto": 200000},
	}
	h.cachedAt = time.Now()
	if got := h.AdvertisedContextWindow(context.Background(), "auto"); got != 0 {
		t.Fatalf("auto = %d, want 0: the router picks the answering model per turn, after the budget is decided, so budgeting against any one model's window is budgeting against a model that is not answering", got)
	}
	if got := h.AdvertisedContextWindow(context.Background(), ""); got != 0 {
		t.Fatalf("empty id = %d, want 0", got)
	}
}
