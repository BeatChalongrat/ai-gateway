package handler

// FIX119 Part A — one id per turn: the middleware mints or honours it once,
// the frames, the row and the logs carry it, the card can show it. Part B —
// the terminal frame says why the model stopped.

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

func TestRequestID_MiddlewareMintsOnceAndHonoursASafeInboundID(t *testing.T) {
	var seen string
	h := middleware.Logging(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = middleware.RequestIDFrom(r.Context())
		w.WriteHeader(http.StatusNoContent)
	}))
	// No header: minted once, the same value on the response and in the context.
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/x", nil))
	if seen == "" || rec.Header().Get("X-Request-Id") != seen {
		t.Fatalf("minted id: context %q, header %q", seen, rec.Header().Get("X-Request-Id"))
	}
	// A safe inbound id (the desktop's run id) is honoured as-is.
	rec = httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/x", nil)
	req.Header.Set("X-Request-Id", "5f3c1a2b-0000-4000-8000-0123456789ab")
	h.ServeHTTP(rec, req)
	if seen != "5f3c1a2b-0000-4000-8000-0123456789ab" || rec.Header().Get("X-Request-Id") != seen {
		t.Fatalf("honoured id: context %q, header %q", seen, rec.Header().Get("X-Request-Id"))
	}
	// An unsafe one is replaced, never echoed.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodGet, "/x", nil)
	req.Header.Set("X-Request-Id", "<script>alert(1)</script> and spaces")
	h.ServeHTTP(rec, req)
	if seen == "" || seen == "<script>alert(1)</script> and spaces" || rec.Header().Get("X-Request-Id") != seen {
		t.Fatalf("unsafe id: context %q, header %q", seen, rec.Header().Get("X-Request-Id"))
	}
}

func TestSanitizeRequestID(t *testing.T) {
	for raw, want := range map[string]string{
		"5f3c1a2b-0000-4000-8000-0123456789ab": "5f3c1a2b-0000-4000-8000-0123456789ab",
		"  run_2026.09.15-a  ":                 "run_2026.09.15-a",
		"short":                                "",
		"has space in it":                      "",
		"":                                     "",
		"x/y":                                  "",
	} {
		if got := middleware.SanitizeRequestID(raw); got != want {
			t.Fatalf("SanitizeRequestID(%q) = %q, want %q", raw, got, want)
		}
	}
}

// The run id on every frame IS the request id: the runWriter envelope
// splices what sendStream was given, and sendStream now takes it from the
// request context rather than minting a second one.
func TestRunID_EqualsTheRequestIDOnEveryFrame(t *testing.T) {
	broker := service.NewStreamBroker()
	const rid = "5f3c1a2b-0000-4000-8000-0123456789ab"
	rec := httptest.NewRecorder()
	rw := newRunWriter(rec, rid, nil)
	rw.started = true
	writeSSEEvent(rw, rec, "message_start", map[string]any{"message_id": "m"})
	writeSSEEvent(rw, rec, "content_delta", map[string]any{"delta": "x"})
	writeSSEEvent(rw, rec, "message_complete", map[string]any{"message_id": "m"})
	for _, f := range readFrames(t, rec.Body.String()) {
		if f.runID != rid {
			t.Fatalf("frame %s carries run_id %q, want the request id %q", f.event, f.runID, rid)
		}
	}
	_ = broker
}

func TestMessageComplete_CarriesStopReasonAndGatewayID(t *testing.T) {
	res := drainOf(t,
		service.StreamUpdate{Delta: "cut off he"},
		service.StreamUpdate{Done: true, FinishReason: "length", StopReason: service.StopReasonMaxTokens, GatewayRequestID: "req_abc", ProviderRequestID: "prov-1"},
	)
	if res.stopReason != service.StopReasonMaxTokens || res.gatewayRequestID != "req_abc" || res.providerRequestID != "prov-1" {
		t.Fatalf("drain: %+v", res)
	}
	ti := res.terminalInfo()
	if ti.StopReason != service.StopReasonMaxTokens || ti.Cause != "" || ti.ErrorCode != "" {
		t.Fatalf("terminalInfo %+v", ti)
	}
}

func TestErrorPayload_CarriesTheGatewayRequestID(t *testing.T) {
	res := drainOf(t,
		service.StreamUpdate{ProviderError: &service.StreamProviderError{Code: "provider_timeout", Message: "slow", RequestID: "req_fail1", TurnID: "5f3c1a2b-0000-4000-8000-0123456789ab"}},
		service.StreamUpdate{Done: true},
	)
	p := res.terminalErrorPayload()
	d, _ := p["details"].(map[string]any)
	if d == nil || d["gateway_request_id"] != "req_fail1" {
		t.Fatalf("details must name the backend's request id: %+v", p)
	}
	if p["code"] != "provider_timeout" {
		t.Fatalf("code %v", p["code"])
	}
}
