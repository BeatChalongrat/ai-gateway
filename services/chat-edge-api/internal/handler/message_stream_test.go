// Regression tests for the SSE drain loop shared by sendStream,
// Regenerate and Edit (Phase 10 B1).
//
// The P0: once the request context is cancelled, its Done channel is
// permanently ready — a select that keeps watching it busy-spins at
// 100% CPU calling stream.Cancel() every iteration until the service
// pump emits its terminal event (one full upstream round-trip). The
// fix nils the done channel after the first cancel so only Events
// drives the post-cancel drain.
package handler

import (
	"strings"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// TestDrainStreamEventsDisconnectCancelsOnceNoBusySpin cancels the
// request context before the pump's terminal event arrives and
// asserts the drain loop calls Cancel exactly once. Before the fix
// the select keeps firing the permanently-ready Done case, so Cancel
// is invoked millions of times during the simulated upstream
// round-trip and this test fails.
func TestDrainStreamEventsDisconnectCancelsOnceNoBusySpin(t *testing.T) {
	rec := httptest.NewRecorder()

	// Simulate a client that has already disconnected: a closed done
	// channel is exactly what a cancelled request context exposes.
	done := make(chan struct{})
	close(done)

	var cancelCalls int64
	cancel := func() { atomic.AddInt64(&cancelCalls, 1) }

	// Pump stand-in: like pumpStreamWithTools, it notices the cancel
	// only after an upstream round-trip, then sends a terminal Done
	// update and closes the channel.
	events := make(chan service.StreamUpdate)
	go func() {
		time.Sleep(50 * time.Millisecond)
		events <- service.StreamUpdate{Done: true, FinishReason: "cancelled"}
		close(events)
	}()

	res := drainStreamEvents(rec, rec, done, events, cancel)

	if n := atomic.LoadInt64(&cancelCalls); n != 1 {
		t.Fatalf("Cancel called %d times during the post-disconnect drain; want exactly 1 (busy-spin on the Done channel)", n)
	}
	if res.finishReason != "cancelled" {
		t.Fatalf("finishReason = %q, want %q (terminal event must still be drained after cancel)", res.finishReason, "cancelled")
	}
}

// TestDrainStreamEventsDisconnectStillDrainsPendingDeltas verifies the
// drain-after-cancel contract: deltas the pump already emitted before
// noticing the cancel are still accumulated so Finish persists the
// partial content.
func TestDrainStreamEventsDisconnectStillDrainsPendingDeltas(t *testing.T) {
	rec := httptest.NewRecorder()

	done := make(chan struct{})
	close(done)

	var cancelCalls int64
	cancel := func() { atomic.AddInt64(&cancelCalls, 1) }

	events := make(chan service.StreamUpdate, 4)
	events <- service.StreamUpdate{Delta: "partial "}
	events <- service.StreamUpdate{Delta: "answer"}
	events <- service.StreamUpdate{Done: true, FinishReason: "cancelled"}
	close(events)

	res := drainStreamEvents(rec, rec, done, events, cancel)

	if res.content != "partial answer" {
		t.Fatalf("content = %q, want %q", res.content, "partial answer")
	}
	if n := atomic.LoadInt64(&cancelCalls); n > 1 {
		t.Fatalf("Cancel called %d times; want at most 1", n)
	}
}

// TestDrainStreamEventsNormalCompletionNeverCancels is the happy
// path: the client stays connected (done never fires), the stream
// completes, and Cancel is never invoked.
func TestDrainStreamEventsNormalCompletionNeverCancels(t *testing.T) {
	rec := httptest.NewRecorder()

	done := make(chan struct{}) // never closed — client stays connected

	var cancelCalls int64
	cancel := func() { atomic.AddInt64(&cancelCalls, 1) }

	usage := &service.GatewayChatUsage{PromptTokens: 3, CompletionTokens: 7}
	events := make(chan service.StreamUpdate, 4)
	events <- service.StreamUpdate{Delta: "hello"}
	events <- service.StreamUpdate{ReasoningDelta: "thinking"}
	events <- service.StreamUpdate{Done: true, FinishReason: "stop", Usage: usage}
	close(events)

	res := drainStreamEvents(rec, rec, done, events, cancel)

	if n := atomic.LoadInt64(&cancelCalls); n != 0 {
		t.Fatalf("Cancel called %d times on a clean completion; want 0", n)
	}
	if res.content != "hello" || res.reasoning != "thinking" {
		t.Fatalf("content/reasoning = %q/%q, want %q/%q", res.content, res.reasoning, "hello", "thinking")
	}
	if res.finishReason != "stop" || res.usage == nil || res.usage.CompletionTokens != 7 {
		t.Fatalf("terminal metadata not captured: %+v", res)
	}
}

// ── Batch 2 T2 — SSE heartbeats ─────────────────────────────────────

// TestDrainStreamEventsEmitsHeartbeatsOnAQuietStream: a provider or
// tool run that goes quiet must not go silent on the wire — the drain
// emits ": ping" comment frames on the heartbeat interval so proxies
// keep the connection and the clients' idle watchdogs get liveness.
func TestDrainStreamEventsEmitsHeartbeatsOnAQuietStream(t *testing.T) {
	old := heartbeatInterval
	heartbeatInterval = 15 * time.Millisecond
	defer func() { heartbeatInterval = old }()

	rec := httptest.NewRecorder()
	done := make(chan struct{}) // client stays connected

	events := make(chan service.StreamUpdate)
	go func() {
		time.Sleep(120 * time.Millisecond) // the quiet stretch
		events <- service.StreamUpdate{Delta: "late answer"}
		events <- service.StreamUpdate{Done: true, FinishReason: "stop"}
		close(events)
	}()

	res := drainStreamEvents(rec, rec, done, events, func() {})

	body := rec.Body.String()
	if got := strings.Count(body, ": ping\n\n"); got < 3 {
		t.Fatalf("want >=3 heartbeat frames during the quiet stretch, got %d in %q", got, body)
	}
	// Heartbeats are comments — the real frames still parse cleanly
	// around them and the drain result is untouched.
	if !strings.Contains(body, `"delta":"late answer"`) {
		t.Fatalf("delta frame missing from body: %q", body)
	}
	if res.content != "late answer" || res.finishReason != "stop" {
		t.Fatalf("drain result corrupted by heartbeats: %+v", res)
	}
}

// TestResumeSnapshotReplayContainsNoHeartbeats pins the replay-purity
// invariant: heartbeats are written at the HTTP edge only and must
// never appear in a resume snapshot replay (the broker never stores
// them — this asserts the replay writer emits none either).
func TestResumeSnapshotReplayContainsNoHeartbeats(t *testing.T) {
	rec := httptest.NewRecorder()

	(&MessageHandler{}).writeResumeSnapshot(rec, rec, service.StreamSnapshot{
		Content:        "already generated prefix",
		Reasoning:      "prior thinking",
		AnalysisStatus: "completed",
	})

	if strings.Contains(rec.Body.String(), ": ping") {
		t.Fatalf("resume replay must not contain heartbeat frames: %q", rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "already generated prefix") {
		t.Fatalf("replay lost the snapshot content: %q", rec.Body.String())
	}
}
