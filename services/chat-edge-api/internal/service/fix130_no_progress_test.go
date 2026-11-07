package service

// FIX130 C3.2 — a turn that cannot reach the model ends with a proper error
// instead of spinning. NIGHT2 F2: with the backend wedged, "กำลังประมวลผล…"
// was still spinning at 241 s — every hop waited on the next with no
// deadline of its own, and chat-edge's 15 s pings kept the clients' idle
// watchdogs quiet the whole time.
//
//   - the gateway never answers the call (no headers)   → provider_timeout
//   - the gateway answered, then says nothing at all      → provider_timeout
//   - the backend's own code-less stream_timeout frame    → provider_timeout
//   - tool runs and a steadily streaming model are never cut.

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func withFastGiveUp(t *testing.T, stall, giveUp, open time.Duration) {
	t.Helper()
	oldS, oldP, oldG, oldO := providerStallAfter, providerStallPoll, providerGiveUpAfter, streamOpenTimeout
	providerStallAfter, providerStallPoll, providerGiveUpAfter, streamOpenTimeout = stall, 5*time.Millisecond, giveUp, open
	t.Cleanup(func() {
		providerStallAfter, providerStallPoll, providerGiveUpAfter, streamOpenTimeout = oldS, oldP, oldG, oldO
	})
}

// fix130Release lets a handler that "never answers" go once the test is
// over, so httptest.Server.Close does not wait on it forever.
var fix130Release chan struct{}

func fix130CallCtx() GatewayCallContext {
	return GatewayCallContext{TenantID: uuid.New(), UserID: uuid.New(), ConversationID: uuid.New()}
}

func TestFIX130_GatewayThatNeverAnswersFailsWithProviderTimeout(t *testing.T) {
	withFastGiveUp(t, time.Hour, time.Hour, 80*time.Millisecond)
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.ReadAll(r.Body)
		select { // the wedged backend: headers never come
		case <-r.Context().Done():
		case <-release:
		}
	}))
	defer srv.Close()
	defer close(release)
	g := NewGatewayClient(srv.URL, "k", "", time.Minute, "")

	type result struct {
		err error
		at  time.Duration
	}
	got := make(chan result, 1)
	started := time.Now()
	go func() {
		_, err := g.SendChatCompletionStream(context.Background(), fix130CallCtx(), GatewayChatRequest{Model: "m"})
		got <- result{err, time.Since(started)}
	}()
	var res result
	select {
	case res = <-got:
	case <-time.After(3 * time.Second):
		t.Fatal("the stream request was still waiting 3 s after its 80 ms deadline — nothing bounds it")
	}
	if res.err == nil {
		t.Fatal("want an error")
	}
	if !errors.Is(res.err, context.DeadlineExceeded) {
		t.Fatalf("want a deadline error the classifier files as a timeout, got %v", res.err)
	}
	var ue *GatewayUnavailableError
	if !errors.As(res.err, &ue) {
		t.Fatalf("want the gateway-shaped error every pre-stream path already writes, got %T", res.err)
	}
	env, ok := ue.Envelope()
	if !ok || env.Code != CodeProviderTimeout || env.Retryable == nil || !*env.Retryable || ue.Status != http.StatusGatewayTimeout {
		t.Fatalf("want 504 provider_timeout retryable, got status=%d env=%+v ok=%v", ue.Status, env, ok)
	}
	if preStreamErrorCode(res.err) != CodeProviderTimeout || preStreamCause(res.err) != TerminalCauseTimeout {
		t.Fatalf("the row must record provider_timeout / timeout, got %q / %q", preStreamErrorCode(res.err), preStreamCause(res.err))
	}
}

// pumpAgainst runs the real pump against a gateway handler and collects its updates.
func pumpAgainst(t *testing.T, h http.HandlerFunc) ([]StreamUpdate, bool) {
	t.Helper()
	fix130Release = make(chan struct{})
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	t.Cleanup(func() { close(fix130Release) })
	s := NewMessageService(nil, nil, NewGatewayClient(srv.URL, "k", "", time.Minute, ""), nil, allowAllModels{}, 50)
	ctx := context.Background()
	req := GatewayChatRequest{Model: "m", Messages: []GatewayMessage{{Role: "user", Content: "x"}}}
	cc := fix130CallCtx()
	stream, err := s.gateway.SendChatCompletionStream(ctx, cc, req)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	out := make(chan StreamUpdate, 64)
	go s.pumpStreamWithTools(streamPumpInput{initialStream: stream, initialReq: req, callCtx: cc, streamCtx: ctx, out: out,
		toolExecCtx: ToolExecContext{TenantID: cc.TenantID, UserID: cc.UserID, ConversationID: cc.ConversationID, MessageID: uuid.New()}})
	var updates []StreamUpdate
	deadline := time.After(3 * time.Second)
	for {
		select {
		case u, ok := <-out:
			if !ok {
				return updates, true
			}
			updates = append(updates, u)
		case <-deadline:
			return updates, false
		}
	}
}

func TestFIX130_ProviderSilentAfterHeadersEndsTheTurnWithProviderTimeout(t *testing.T) {
	withFastGiveUp(t, 40*time.Millisecond, 120*time.Millisecond, time.Hour)
	updates, ended := pumpAgainst(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`data: {"choices":[{"delta":{"content":"เริ่ม"},"finish_reason":null}]}` + "\n\n"))
		w.(http.Flusher).Flush()
		select { // then nothing, ever
		case <-r.Context().Done():
		case <-fix130Release:
		}
	})
	if !ended {
		t.Fatal("the turn was still open 3 s after the model went silent — no deadline ends it")
	}
	var stalled bool
	var endErr error
	for _, u := range updates {
		if u.ProviderStall != nil && u.ProviderStall.Stalled {
			stalled = true
		}
		if u.Err != nil {
			endErr = u.Err
		}
	}
	if !stalled {
		t.Fatal("the stall notice still goes out first")
	}
	var np *ProviderNoProgressError
	if !errors.As(endErr, &np) {
		t.Fatalf("want ProviderNoProgressError, got %v", endErr)
	}
	f := ClassifyStreamError(endErr)
	if f.Code != CodeProviderTimeout || f.Cause != TerminalCauseTimeout || !f.Retryable {
		t.Fatalf("want provider_timeout / timeout / retryable, got %+v", f)
	}
}

func TestFIX130_SteadyReasoningIsNeverCut(t *testing.T) {
	// 300 ms of thinking fragments every 30 ms, give-up at 120 ms: the clock
	// restarts on every frame, so the turn ends normally.
	withFastGiveUp(t, time.Hour, 120*time.Millisecond, time.Hour)
	updates, ended := pumpAgainst(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		for i := 0; i < 10; i++ {
			_, _ = w.Write([]byte(`data: {"type":"reasoning_summary_delta","text":"คิด "}` + "\n\n"))
			w.(http.Flusher).Flush()
			time.Sleep(30 * time.Millisecond)
		}
		_, _ = w.Write([]byte(sseText("คำตอบ")))
	})
	if !ended {
		t.Fatal("pump did not end")
	}
	text, done, err, _ := summarise(updates)
	if err != nil || !done || text != "คำตอบ" {
		t.Fatalf("a model that keeps producing is never cut: text=%q done=%v err=%v", text, done, err)
	}
}

func TestFIX130_GiveUpNeverFiresWhileAToolRuns(t *testing.T) {
	withFastGiveUp(t, time.Hour, 30*time.Millisecond, time.Hour)
	out := make(chan StreamUpdate, 8)
	w := newStallWatch(out, providerStallAfter)
	fired := make(chan struct{}, 1)
	w.SetGiveUp(providerGiveUpAfter, func() { fired <- struct{}{} })
	defer w.Stop()
	w.BeginAwait()
	w.EndAwait() // a run_python call holds the turn
	time.Sleep(150 * time.Millisecond)
	select {
	case <-fired:
		t.Fatal("tool execution must never end the turn")
	default:
	}
	if w.GaveUp() != 0 {
		t.Fatal("GaveUp must stay zero while no provider wait is running")
	}
	w.BeginAwait() // back to waiting on the model: now it may give up
	select {
	case <-fired:
	case <-time.After(time.Second):
		t.Fatal("the give-up re-arms once the pump waits on the model again")
	}
}

func TestFIX130_CodelessBackendTimeoutFrameGetsACode(t *testing.T) {
	ev, err := parseStreamChunk(`{"error":{"type":"stream_timeout","message":"no data from the upstream for 60s; the request was closed","request_id":"req_1"}}`, "")
	if err != nil || ev.ProviderError == nil {
		t.Fatalf("parse: %v %+v", err, ev)
	}
	pe := ev.ProviderError
	if pe.Code != CodeProviderTimeout || pe.Retryable == nil || !*pe.Retryable || pe.RequestID != "req_1" {
		t.Fatalf("want provider_timeout, retryable, the request id kept; got %+v", pe)
	}
	// A frame that has a code keeps it.
	ev, _ = parseStreamChunk(`{"error":{"code":"provider_rate_limited","type":"stream_timeout","message":"x"}}`, "")
	if ev.ProviderError.Code != "provider_rate_limited" {
		t.Fatalf("an explicit code wins, got %q", ev.ProviderError.Code)
	}
	// The backend's pre-stream hard timeout (a 504 with only a type).
	ue := &GatewayUnavailableError{Status: 504, Body: `{"error":{"type":"upstream_timeout","message":"The request did not complete within 120s and was closed."}}`}
	env, ok := ue.Envelope()
	if !ok || env.Code != CodeProviderTimeout || !strings.Contains(env.Message, "120s") {
		t.Fatalf("want provider_timeout from the type, got %+v ok=%v", env, ok)
	}
}
