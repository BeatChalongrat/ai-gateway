package service

import "testing"

// B1 — a Gateway ``error`` SSE frame must parse into a ProviderError so
// the pump can forward it to the client instead of swallowing it.
func TestParseStreamChunk_ProviderErrorFrame(t *testing.T) {
	raw := `{"error":{"code":"all_models_failed","message":"Unable to get a response right now","attempts":[{"model":"gpt-4o","error":"401"}]}}`
	ev, err := parseStreamChunk(raw, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ev.ProviderError == nil {
		t.Fatalf("expected ProviderError to be set, got %+v", ev)
	}
	if ev.ProviderError.Code != "all_models_failed" {
		t.Errorf("code = %q, want all_models_failed", ev.ProviderError.Code)
	}
	if len(ev.ProviderError.Attempts) != 1 {
		t.Errorf("attempts len = %d, want 1", len(ev.ProviderError.Attempts))
	}
}

// B2 — a fallback_info frame must parse into FallbackInfo.
func TestParseStreamChunk_FallbackInfoFrame(t *testing.T) {
	raw := `{"type":"fallback_info","fallback_info":{"requested_model":"gpt-4o","actual_model":"claude-sonnet-4"}}`
	ev, err := parseStreamChunk(raw, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ev.FallbackInfo == nil {
		t.Fatalf("expected FallbackInfo to be set, got %+v", ev)
	}
	if ev.FallbackInfo["actual_model"] != "claude-sonnet-4" {
		t.Errorf("actual_model = %v, want claude-sonnet-4", ev.FallbackInfo["actual_model"])
	}
}

// A reasoning_notice frame (the Gateway downgraded a reasoning request
// because the resolved model can't reason) must parse into
// ReasoningNotice so the pump relays it instead of dropping it — the
// user must not silently get less than they asked for. Shape per
// public_chat.py's downgrade emitter.
func TestParseStreamChunk_ReasoningNoticeFrame(t *testing.T) {
	raw := `{"id":"req-1","type":"reasoning_notice","created":1,"model":"gpt-4o-mini","reasoning_downgraded":true,"reason":"Reasoning was requested (mode='auto') but the resolved model 'gpt-4o-mini' is not reasoning-capable"}`
	ev, err := parseStreamChunk(raw, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ev.ReasoningNotice == nil {
		t.Fatalf("expected ReasoningNotice to be set, got %+v", ev)
	}
	if ev.ReasoningNotice.Model != "gpt-4o-mini" {
		t.Errorf("model = %q, want gpt-4o-mini", ev.ReasoningNotice.Model)
	}
	if ev.ReasoningNotice.Reason == "" {
		t.Error("reason must carry the gateway's explanation")
	}
}

// Regression: a normal OpenAI content chunk must still decode to a Delta
// and must NOT be mistaken for an error/fallback frame.
func TestParseStreamChunk_NormalContentUnaffected(t *testing.T) {
	raw := `{"object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":null}]}`
	ev, err := parseStreamChunk(raw, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ev.ProviderError != nil || ev.FallbackInfo != nil {
		t.Fatalf("normal chunk misclassified: %+v", ev)
	}
	if ev.Delta != "hello" {
		t.Errorf("delta = %q, want hello", ev.Delta)
	}
}

// Regression: a reasoning frame still decodes correctly after the
// error/fallback sniffing was added ahead of it.
func TestParseStreamChunk_ReasoningStillWorks(t *testing.T) {
	raw := `{"type":"reasoning_summary_delta","text":"thinking..."}`
	ev, err := parseStreamChunk(raw, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ev.ReasoningDelta != "thinking..." {
		t.Errorf("reasoning = %q, want thinking...", ev.ReasoningDelta)
	}
}
