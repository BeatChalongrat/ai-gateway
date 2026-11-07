package service

// FIX64 — the stale-catalog repair.
//
// Measured before the fix: an admin turns a model's tools switch off; the
// Gateway refuses tools for it at once (model_no_tool_support, not
// retryable), but chat-edge's model catalog is cached for five minutes
// and keeps attaching tools — every turn on that model died for the rest
// of the cache window. The repair: drop the cache, re-ask, resend the
// same turn without tools once. These tests pin when it fires and when
// it must not.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/google/uuid"
)

func fix64Pump(tools bool) streamPumpInput {
	return streamPumpInput{
		streamCtx:     context.Background(),
		toolsAttached: tools,
		toolExecCtx:   ToolExecContext{ConversationID: uuid.New()},
	}
}

func fix64Req(tools bool) GatewayChatRequest {
	req := GatewayChatRequest{Model: "qwen3.8-27b-fp8", Messages: []GatewayMessage{{Role: "user", Content: "hi"}}}
	if tools {
		req.Tools = []GatewayTool{{Type: "function"}}
		req.ToolChoice = "auto"
	}
	return req
}

func TestRetryWithoutTools_OnlyForThatErrorAndOnlyWhenToolsWereSent(t *testing.T) {
	invalidations := 0
	s := &MessageService{
		invalidateCatalog: func() { invalidations++ },
		nonToolModel:      func(context.Context, string) bool { return true },
	}
	healed := false

	// A different error code: not ours to repair.
	req := fix64Req(true)
	if got := s.retryWithoutTools(fix64Pump(true), &req, &StreamProviderError{Code: "provider_no_credit"}, &healed, 0, 0, ""); got != nil {
		t.Fatal("a non-tools error must not be retried")
	}
	// No tools on the wire: the refusal cannot be about our catalog.
	req = fix64Req(false)
	if got := s.retryWithoutTools(fix64Pump(false), &req, &StreamProviderError{Code: "model_no_tool_support"}, &healed, 0, 0, ""); got != nil {
		t.Fatal("a turn without tools must not be retried")
	}
	// Something already forwarded (a later tool round, accumulated
	// calls, a finish reason): the turn is past the point of resending.
	req = fix64Req(true)
	for _, c := range []struct {
		iter, acc int
		fin       string
	}{{1, 0, ""}, {0, 1, ""}, {0, 0, "stop"}} {
		if got := s.retryWithoutTools(fix64Pump(true), &req, &StreamProviderError{Code: "model_no_tool_support"}, &healed, c.iter, c.acc, c.fin); got != nil {
			t.Fatalf("case %+v: must not retry mid-turn", c)
		}
	}
	if invalidations != 0 || healed {
		t.Fatalf("nothing should have been invalidated: invalidations=%d healed=%v", invalidations, healed)
	}
}

func TestRetryWithoutTools_CatalogStillSaysTools_NoRetry_ButOnlyOnce(t *testing.T) {
	invalidations, lookups := 0, 0
	s := &MessageService{
		invalidateCatalog: func() { invalidations++ },
		nonToolModel:      func(context.Context, string) bool { lookups++; return false }, // fresh catalog: still tools-capable
	}
	healed := false
	req := fix64Req(true)
	perr := &StreamProviderError{Code: "model_no_tool_support"}
	if got := s.retryWithoutTools(fix64Pump(true), &req, perr, &healed, 0, 0, ""); got != nil {
		t.Fatal("when the fresh catalog still says tools, the error must reach the client unchanged")
	}
	if invalidations != 1 || lookups != 1 || !healed {
		t.Fatalf("expected one invalidation and one fresh lookup: invalidations=%d lookups=%d healed=%v", invalidations, lookups, healed)
	}
	if len(req.Tools) == 0 {
		t.Fatal("the request must keep its tools when no retry happens")
	}
	// A second error frame in the same pump never re-triggers.
	if got := s.retryWithoutTools(fix64Pump(true), &req, perr, &healed, 0, 0, ""); got != nil || invalidations != 1 {
		t.Fatal("the repair runs at most once per pump")
	}
}

func TestRetryWithoutTools_ResendsTheTurnWithoutTools(t *testing.T) {
	var bodies []map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		bodies = append(bodies, body)
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"OK\"},\"finish_reason\":null}]}\n\n"))
		_, _ = w.Write([]byte("data: [DONE]\n\n"))
	}))
	defer srv.Close()

	s := &MessageService{
		gateway:           NewGatewayClient(srv.URL, "test-key", "", time.Minute, ""),
		invalidateCatalog: func() {},
		nonToolModel:      func(context.Context, string) bool { return true }, // fresh catalog: tools-blind
	}
	healed := false
	req := fix64Req(true)
	stream := s.retryWithoutTools(fix64Pump(true), &req, &StreamProviderError{Code: "model_no_tool_support"}, &healed, 0, 0, "")
	if stream == nil {
		t.Fatal("expected a retried stream")
	}
	ev, err := stream.Next()
	_ = stream.Close()
	if err != nil {
		t.Fatalf("first frame of the retried stream: %v", err)
	}
	if ev.Delta != "OK" {
		t.Fatalf("expected the retried stream's delta, got %+v", ev)
	}
	if len(req.Tools) != 0 || req.ToolChoice != "" {
		t.Fatalf("the resent request must carry no tools: %+v", req)
	}
	if len(bodies) != 1 {
		t.Fatalf("expected exactly one resend, got %d", len(bodies))
	}
	if _, has := bodies[0]["tools"]; has {
		t.Fatalf("the body on the wire still had tools: %v", bodies[0])
	}
	if bodies[0]["model"] != "qwen3.8-27b-fp8" {
		t.Fatalf("the resend must target the same model: %v", bodies[0]["model"])
	}
}

func TestRetryWithoutTools_NothingWiredMeansOldBehaviour(t *testing.T) {
	s := &MessageService{} // no invalidator, no lookup
	healed := false
	req := fix64Req(true)
	if got := s.retryWithoutTools(fix64Pump(true), &req, &StreamProviderError{Code: "model_no_tool_support"}, &healed, 0, 0, ""); got != nil {
		t.Fatal("without the hooks the error must pass through as before")
	}
}
