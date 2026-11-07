package service

import "testing"

// FIX35 Part B — the gateway names the model that actually answered; the
// stream pump must surface it from BOTH the typed frame and the chunk field.
func TestParseStreamChunk_ServedModel(t *testing.T) {
	ev, err := parseStreamChunk(`{"type":"model_resolved","model":"deepseek/deepseek-v4-flash-0731","requested_model":"auto"}`, "")
	if err != nil {
		t.Fatalf("typed frame: %v", err)
	}
	if ev.ServedModel != "deepseek/deepseek-v4-flash-0731" {
		t.Fatalf("typed frame ServedModel = %q", ev.ServedModel)
	}
	if ev.Delta != "" || ev.Done {
		t.Fatalf("typed frame must carry no content/done: %+v", ev)
	}

	ev, err = parseStreamChunk(`{"id":"req_1","object":"chat.completion.chunk","model":"anthropic/claude-sonnet-4.5","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}`, "")
	if err != nil {
		t.Fatalf("chunk: %v", err)
	}
	if ev.ServedModel != "anthropic/claude-sonnet-4.5" || ev.Delta != "ok" {
		t.Fatalf("chunk ServedModel/Delta = %q/%q", ev.ServedModel, ev.Delta)
	}

	// A chunk without a model names nothing (never "auto").
	ev, err = parseStreamChunk(`{"id":"req_1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}`, "")
	if err != nil {
		t.Fatalf("bare chunk: %v", err)
	}
	if ev.ServedModel != "" {
		t.Fatalf("bare chunk ServedModel = %q, want empty", ev.ServedModel)
	}
}

// The resume broker keeps the served model sticky, like fallback_info, so a
// client that reattaches mid-stream learns it too.
func TestStreamBroker_ServedModelSticky(t *testing.T) {
	e := &brokerEntry{}
	e.publish(StreamUpdate{ServedModel: "google/gemini-2.5-flash"})
	e.publish(StreamUpdate{Delta: "hello"})
	e.mu.Lock()
	snap := e.snapshotLocked()
	e.mu.Unlock()
	if snap.ServedModel != "google/gemini-2.5-flash" {
		t.Fatalf("snapshot ServedModel = %q", snap.ServedModel)
	}
	if snap.Content != "hello" {
		t.Fatalf("snapshot Content = %q", snap.Content)
	}
}
