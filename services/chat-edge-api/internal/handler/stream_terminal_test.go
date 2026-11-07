package handler

// FIX118 Part A — a mid-stream failure reaches the terminal frame as a
// catalogue code, never as the raw Go error text.
//
// Before: a transport error in the drain became code INTERNAL_ERROR with
// message "stream error: <err>" — the one shape the web mapper shows
// verbatim when it does not know the code. After: the drain classifies
// it (service.ClassifyStreamError), the frame carries code + cause +
// retryable, and the Go text rides details.transport for the log reader.

import (
	"context"
	"errors"
	"net/http/httptest"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

func drainOf(t *testing.T, updates ...service.StreamUpdate) streamDrainResult {
	t.Helper()
	rec := httptest.NewRecorder()
	events := make(chan service.StreamUpdate, len(updates))
	for _, u := range updates {
		events <- u
	}
	close(events)
	return drainStreamEvents(rec, rec, make(chan struct{}), events, func() {})
}

func TestStreamTerminal_TransportErrorIsClassified(t *testing.T) {
	cases := []struct {
		name  string
		err   error
		code  string
		cause string
	}{
		{"eof without terminal", service.ErrStreamEOFWithoutTerminal, service.CodeStreamInterrupted, service.TerminalCauseEOFWithoutTerminal},
		{"deadline", context.DeadlineExceeded, service.CodeProviderTimeout, service.TerminalCauseTimeout},
		{"reset", errors.New("read tcp 10.0.0.1:443: connection reset by peer"), service.CodeStreamInterrupted, service.TerminalCauseDisconnected},
		{"unmapped", errors.New("something nobody named"), service.CodeUnclassifiedError, service.TerminalCauseError},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			res := drainOf(t, service.StreamUpdate{Delta: "partial "}, service.StreamUpdate{Err: tc.err})
			if res.content != "partial " {
				t.Fatalf("partial text must be kept: %q", res.content)
			}
			if res.errCode != tc.code || res.cause != tc.cause {
				t.Fatalf("code/cause = %s/%s, want %s/%s", res.errCode, res.cause, tc.code, tc.cause)
			}
			p := res.terminalErrorPayload()
			if p["code"] != tc.code || p["cause"] != tc.cause {
				t.Fatalf("payload %+v", p)
			}
			msg, _ := p["message"].(string)
			if msg == "" || msg == "stream error: "+tc.err.Error() || msg == tc.err.Error() {
				t.Fatalf("the message must be the catalogue sentence, got %q", msg)
			}
			if msg != service.StreamFailureMessage(tc.code) {
				t.Fatalf("message %q is not the catalogue sentence for %s", msg, tc.code)
			}
			details, _ := p["details"].(map[string]any)
			if details == nil || details["transport"] != tc.err.Error() {
				t.Fatalf("the Go text must ride details.transport: %+v", p["details"])
			}
			if _, ok := p["retryable"].(bool); !ok {
				t.Fatal("a classified failure says whether a retry can help")
			}
			ti := res.terminalInfo()
			if ti.Cause != tc.cause || ti.ErrorCode != tc.code {
				t.Fatalf("terminalInfo %+v", ti)
			}
		})
	}
}

// An error WITH a code (the gateway's 429 / 5xx / capability refusals) is
// forwarded untouched — code, message, retryable — and its cause is `error`.
func TestStreamTerminal_ProviderErrorFrameKeepsItsCode(t *testing.T) {
	retry := true
	res := drainOf(t,
		service.StreamUpdate{Delta: "x"},
		service.StreamUpdate{ProviderError: &service.StreamProviderError{Code: "provider_rate_limited", Message: "The AI provider is rate-limiting requests.", Retryable: &retry}},
		service.StreamUpdate{Done: true},
	)
	p := res.terminalErrorPayload()
	if p["code"] != "provider_rate_limited" || p["message"] != "The AI provider is rate-limiting requests." || p["retryable"] != true {
		t.Fatalf("payload %+v", p)
	}
	if res.cause != service.TerminalCauseError || p["cause"] != service.TerminalCauseError {
		t.Fatalf("cause %q", res.cause)
	}
	if _, has := p["details"]; has {
		t.Fatal("no transport text was involved; details must stay absent")
	}
	if ti := res.terminalInfo(); ti.ErrorCode != "provider_rate_limited" || ti.Cause != service.TerminalCauseError {
		t.Fatalf("terminalInfo %+v", ti)
	}
	res5 := drainOf(t, service.StreamUpdate{ProviderError: &service.StreamProviderError{Code: "provider_unavailable", Message: "down"}}, service.StreamUpdate{Done: true})
	if res5.errCode != "provider_unavailable" || res5.terminalErrorPayload()["retryable"] != nil {
		t.Fatalf("a 5xx-class frame without a verdict is forwarded without inventing one: %+v", res5.terminalErrorPayload())
	}
}

func TestStreamTerminal_CompletedRunHasNoErrorCode(t *testing.T) {
	res := drainOf(t, service.StreamUpdate{Delta: "ok", FinishReason: "stop"}, service.StreamUpdate{Done: true})
	if res.err != nil || res.cause != "" {
		t.Fatalf("a normal run: err=%v cause=%q", res.err, res.cause)
	}
	if ti := res.terminalInfo(); ti.ErrorCode != "" || ti.Cause != "" {
		t.Fatalf("terminalInfo on success must be empty (the finish callback derives `completed`): %+v", ti)
	}
}
