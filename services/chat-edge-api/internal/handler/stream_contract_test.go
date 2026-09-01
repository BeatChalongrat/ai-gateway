package handler

// FIX41 C6 — the stream contract, pinned.
//
// These are the properties docs/STREAM_CONTRACT.md promises and that
// docs/fix41/A-baseline.md measured as broken. They run in the normal
// suite (no database, no network) so a future change that quietly ends a
// run with silence, or renumbers a replay, fails here rather than in
// somebody's browser.

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// frameLog is one parsed SSE frame plus its envelope.
type frameLog struct {
	event    string
	runID    string
	seq      int64
	finalSeq int64
}

func readFrames(t *testing.T, raw string) []frameLog {
	t.Helper()
	var out []frameLog
	for _, block := range strings.Split(raw, "\n\n") {
		block = strings.TrimSpace(block)
		if block == "" {
			continue
		}
		var ev, data string
		for _, line := range strings.Split(block, "\n") {
			switch {
			case strings.HasPrefix(line, "event: "):
				ev = strings.TrimPrefix(line, "event: ")
			case strings.HasPrefix(line, "data: "):
				data = strings.TrimPrefix(line, "data: ")
			}
		}
		if data == "" {
			continue // a comment frame (": ping") — not an event
		}
		var env struct {
			RunID    string `json:"run_id"`
			Seq      int64  `json:"seq"`
			FinalSeq int64  `json:"final_seq"`
		}
		if err := json.Unmarshal([]byte(data), &env); err != nil {
			t.Fatalf("frame %q payload is not an object: %v", ev, err)
		}
		out = append(out, frameLog{event: ev, runID: env.RunID, seq: env.Seq, finalSeq: env.FinalSeq})
	}
	return out
}

// runFixture wires a runWriter to a real broker entry, exactly as the
// three stream handlers do.
func runFixture(t *testing.T) (*service.StreamBroker, uuid.UUID, string, *httptest.ResponseRecorder, *runWriter) {
	t.Helper()
	broker := service.NewStreamBroker()
	msgID := uuid.New()
	runID := uuid.NewString()
	broker.OpenRun(msgID, runID)
	rec := httptest.NewRecorder()
	rw := newRunWriter(rec, runID, func(event string, build func(seq, final int64) []byte) (int64, bool) {
		return broker.Record(msgID, event, build)
	})
	rw.started = true
	return broker, msgID, runID, rec, rw
}

// A completed run: seq is 1..N with no gap and no repeat, message_start
// is 1, every frame names the same run, and the last frame is a terminal
// carrying its own seq as final_seq.
func TestStreamContract_SeqIsGaplessAndEndsOnTerminal(t *testing.T) {
	_, _, runID, rec, rw := runFixture(t)

	writeSSEEvent(rw, rec, "message_start", map[string]any{"message_id": "m1"})
	writeSSEEvent(rw, rec, "model_resolved", map[string]any{"model": "probe"})
	writeSSEEvent(rw, rec, "content_delta", map[string]any{"delta": "hello "})
	writeSSEEvent(rw, rec, "content_delta", map[string]any{"delta": "world"})
	writeSSEEvent(rw, rec, "message_complete", map[string]any{"message_id": "m1"})

	frames := readFrames(t, rec.Body.String())
	if len(frames) != 5 {
		t.Fatalf("got %d frames, want 5: %+v", len(frames), frames)
	}
	if frames[0].event != "message_start" || frames[0].seq != 1 {
		t.Errorf("message_start must be seq 1, got %q seq=%d", frames[0].event, frames[0].seq)
	}
	for i, f := range frames {
		if f.seq != int64(i+1) {
			t.Errorf("frame %d (%s) has seq %d, want %d — seq must be gapless", i, f.event, f.seq, i+1)
		}
		if f.runID != runID {
			t.Errorf("frame %d (%s) run_id = %q, want %q", i, f.event, f.runID, runID)
		}
	}
	last := frames[len(frames)-1]
	if !isTerminalEvent(last.event) {
		t.Fatalf("last frame is %q, which is not a terminal event", last.event)
	}
	if last.finalSeq != last.seq {
		t.Errorf("final_seq = %d, want %d (its own seq)", last.finalSeq, last.seq)
	}
	for _, f := range frames[:len(frames)-1] {
		if f.finalSeq != 0 {
			t.Errorf("non-terminal frame %q carries final_seq=%d", f.event, f.finalSeq)
		}
	}
}

// Heartbeats are comment frames. They must not consume a seq — a client
// that saw seq 1..N and counted N frames would otherwise find holes on
// every quiet stretch of a long tool run.
func TestStreamContract_HeartbeatDoesNotConsumeSeq(t *testing.T) {
	_, _, _, rec, rw := runFixture(t)

	writeSSEEvent(rw, rec, "message_start", map[string]any{"message_id": "m1"})
	writeSSEHeartbeat(rec, rec)
	writeSSEHeartbeat(rec, rec)
	writeSSEEvent(rw, rec, "content_delta", map[string]any{"delta": "x"})
	writeSSEHeartbeat(rec, rec)
	writeSSEEvent(rw, rec, "message_complete", map[string]any{"message_id": "m1"})

	body := rec.Body.String()
	if !strings.Contains(body, ": ping") {
		t.Fatal("the heartbeats did not reach the wire; this test would pass vacuously")
	}
	frames := readFrames(t, body)
	if len(frames) != 3 {
		t.Fatalf("got %d events, want 3 — heartbeats must not be events: %+v", len(frames), frames)
	}
	for i, f := range frames {
		if f.seq != int64(i+1) {
			t.Errorf("frame %d (%s) seq=%d, want %d — a heartbeat consumed a number", i, f.event, f.seq, i+1)
		}
	}
}

// A provider failure must END on `error`. Before FIX41 the drain spent
// the error mid-stream and the handler appended message_complete after
// it, so the last frame of a failed turn was the success terminator.
func TestStreamContract_ProviderErrorIsTheLastFrame(t *testing.T) {
	_, _, _, rec, rw := runFixture(t)

	events := make(chan service.StreamUpdate, 4)
	events <- service.StreamUpdate{Delta: "partial "}
	events <- service.StreamUpdate{ProviderError: &service.StreamProviderError{
		Code: "all_models_failed", Message: "no provider answered",
	}}
	close(events)

	writeSSEEvent(rw, rec, "message_start", map[string]any{"message_id": "m1"})
	res := drainStreamEvents(rw, rec, make(chan struct{}), events, func() {})
	if res.err == nil {
		t.Fatal("the drain did not record the provider error")
	}
	// The drain must NOT have written an error frame of its own.
	if got := strings.Count(rec.Body.String(), "event: error"); got != 0 {
		t.Fatalf("the drain wrote %d error frames; the terminal owns that event", got)
	}
	writeSSEEvent(rw, rec, "error", res.terminalErrorPayload())

	frames := readFrames(t, rec.Body.String())
	last := frames[len(frames)-1]
	if last.event != "error" {
		t.Fatalf("last frame is %q, want error", last.event)
	}
	if last.finalSeq != last.seq {
		t.Errorf("terminal error final_seq=%d, want %d", last.finalSeq, last.seq)
	}
	if !strings.Contains(rec.Body.String(), "all_models_failed") {
		t.Error("the terminal error dropped the provider's code")
	}
}

// A run a person stopped ends on `cancelled` — not on an internal-error
// frame followed by a success terminator, which is what every OTHER
// viewer of that generation used to see.
func TestStreamContract_CancelEndsOnCancelled(t *testing.T) {
	_, _, _, rec, rw := runFixture(t)

	writeSSEEvent(rw, rec, "message_start", map[string]any{"message_id": "m1"})
	writeSSEEvent(rw, rec, "content_delta", map[string]any{"delta": "half an ans"})
	writeSSEEvent(rw, rec, "cancelled", map[string]any{"message_id": "m1", "partial": true})

	frames := readFrames(t, rec.Body.String())
	last := frames[len(frames)-1]
	if last.event != "cancelled" {
		t.Fatalf("last frame is %q, want cancelled", last.event)
	}
	if last.finalSeq != last.seq || last.seq != 3 {
		t.Errorf("cancelled seq=%d final_seq=%d, want 3/3", last.seq, last.finalSeq)
	}
	if strings.Contains(rec.Body.String(), "event: message_complete") {
		t.Error("a cancelled run must not also claim to have completed")
	}
}

// Cancelling twice is harmless. CancelStream is the whole cancel path on
// a service with no active stream, which is exactly the second press.
func TestStreamContract_CancelIsIdempotent(t *testing.T) {
	svc := service.NewMessageService(nil, nil, nil, nil, nil, 10)
	convID := uuid.New()
	if got := svc.CancelStream(convID); got {
		t.Errorf("CancelStream on a quiet conversation = %v, want false", got)
	}
	// Twice more: no panic, same answer.
	_ = svc.CancelStream(convID)
	if got := svc.CancelStream(convID); got {
		t.Errorf("repeat CancelStream = %v, want false", got)
	}
}

// A stream that reaches the end of its handler without a terminal gets
// one. This is the property that makes "however it ends, it says so" true
// for return paths nobody has written yet.
func TestStreamContract_SilentReturnStillTerminates(t *testing.T) {
	_, _, _, rec, rw := runFixture(t)
	writeSSEEvent(rw, rec, "message_start", map[string]any{"message_id": "m1"})

	func() {
		defer rw.ensureTerminal(rec)
		// ...a handler that just returns.
	}()

	frames := readFrames(t, rec.Body.String())
	last := frames[len(frames)-1]
	if !isTerminalEvent(last.event) {
		t.Fatalf("a silent return left the run open on %q", last.event)
	}
	if last.event != "error" {
		t.Errorf("the backstop terminal is %q; a run that produced no result is an error", last.event)
	}
}

// ensureTerminal must not append a second terminal to a run that already
// ended properly.
func TestStreamContract_EnsureTerminalIsNotADuplicate(t *testing.T) {
	_, _, _, rec, rw := runFixture(t)
	writeSSEEvent(rw, rec, "message_start", map[string]any{"message_id": "m1"})
	writeSSEEvent(rw, rec, "message_complete", map[string]any{"message_id": "m1"})
	rw.ensureTerminal(rec)

	frames := readFrames(t, rec.Body.String())
	if len(frames) != 2 {
		t.Fatalf("got %d frames, want 2: %+v", len(frames), frames)
	}
}

// A replay hands back the ORIGINAL numbers. Renumbering would make a
// client's cursor meaningless the moment it reattached.
func TestStreamContract_ReplayKeepsOriginalSeqAndRunID(t *testing.T) {
	broker, msgID, runID, rec, rw := runFixture(t)

	writeSSEEvent(rw, rec, "message_start", map[string]any{"message_id": "m1"})
	writeSSEEvent(rw, rec, "content_delta", map[string]any{"delta": "a"})
	writeSSEEvent(rw, rec, "tool_activity", &service.ToolActivityUpdate{ID: "t", Tool: "web_fetch", Phase: "started"})
	writeSSEEvent(rw, rec, "content_delta", map[string]any{"delta": "b"})
	writeSSEEvent(rw, rec, "message_complete", map[string]any{"message_id": "m1"})
	live := readFrames(t, rec.Body.String())

	fa, err, ok := broker.SubscribeFrames(msgID, 0)
	if !ok || err != nil {
		t.Fatalf("SubscribeFrames ok=%v err=%v", ok, err)
	}
	defer fa.Cancel()
	if fa.RunID != runID {
		t.Errorf("attachment run id = %q, want %q", fa.RunID, runID)
	}
	rec2 := httptest.NewRecorder()
	rrw := newReplayWriter(rec2, fa.RunID)
	for _, le := range fa.Replay {
		rrw.writeRecorded(rec2, le.Event, le.Data)
	}
	replay := readFrames(t, rec2.Body.String())

	if len(replay) != len(live) {
		t.Fatalf("replay has %d frames, live had %d", len(replay), len(live))
	}
	for i := range live {
		if replay[i] != live[i] {
			t.Errorf("frame %d: replay %+v != live %+v", i, replay[i], live[i])
		}
	}
}

// starting_after hands back only what the client is missing, still under
// the original numbers — no gap, no repeat.
func TestStreamContract_StartingAfterResumesExactlyWhereAsked(t *testing.T) {
	broker, msgID, _, rec, rw := runFixture(t)
	for i := 0; i < 6; i++ {
		writeSSEEvent(rw, rec, "content_delta", map[string]any{"delta": "x"})
	}
	writeSSEEvent(rw, rec, "message_complete", map[string]any{"message_id": "m1"})

	fa, err, ok := broker.SubscribeFrames(msgID, 4)
	if !ok || err != nil {
		t.Fatalf("SubscribeFrames ok=%v err=%v", ok, err)
	}
	defer fa.Cancel()
	if len(fa.Replay) != 3 {
		t.Fatalf("replay after seq 4 has %d frames, want 3", len(fa.Replay))
	}
	for i, le := range fa.Replay {
		if le.Seq != int64(i+5) {
			t.Errorf("replay[%d].Seq = %d, want %d", i, le.Seq, i+5)
		}
	}
	if fa.Live != nil {
		t.Error("a finished run must not hand out a live channel")
	}
	if fa.FinalSeq != 7 {
		t.Errorf("FinalSeq = %d, want 7", fa.FinalSeq)
	}
}

// A cursor past frames the cap dropped is a hole the client cannot see.
// It must be told, not quietly under-served.
func TestStreamContract_TrimmedLedgerReportsTooOld(t *testing.T) {
	broker := service.NewStreamBroker()
	broker.SetLedgerBounds(time.Minute, 3) // keep only the last 3 frames
	msgID := uuid.New()
	broker.OpenRun(msgID, "run-trim")
	rec := httptest.NewRecorder()
	rw := newRunWriter(rec, "run-trim", func(event string, build func(seq, final int64) []byte) (int64, bool) {
		return broker.Record(msgID, event, build)
	})
	for i := 0; i < 10; i++ {
		writeSSEEvent(rw, rec, "content_delta", map[string]any{"delta": "x"})
	}

	// Frames 1..7 are gone; only 8, 9, 10 survive.
	if _, err, ok := broker.SubscribeFrames(msgID, 2); !ok || err == nil {
		t.Fatalf("resuming from a dropped position: ok=%v err=%v, want a too-old error", ok, err)
	}
	// A cursor that lands exactly on the boundary is still serviceable.
	fa, err, ok := broker.SubscribeFrames(msgID, 7)
	if !ok || err != nil {
		t.Fatalf("resuming from the oldest retained position: ok=%v err=%v", ok, err)
	}
	defer fa.Cancel()
	if len(fa.Replay) != 3 || fa.Replay[0].Seq != 8 {
		t.Errorf("replay = %d frames starting at %d, want 3 starting at 8", len(fa.Replay), fa.Replay[0].Seq)
	}
}

// The envelope must not corrupt a payload that carries raw JSON
// (message_sources forwards stored JSON verbatim) or an empty object.
func TestStreamContract_EnvelopePreservesPayloads(t *testing.T) {
	raw := json.RawMessage(`[{"url":"https://example.test","title":"t"}]`)
	out := envelope([]byte(`{"message_id":"m","sources":`+string(raw)+`}`), "r1", 4, 0)
	var got map[string]any
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("enveloped payload is not valid JSON: %v (%s)", err, out)
	}
	if got["run_id"] != "r1" || got["seq"].(float64) != 4 {
		t.Errorf("envelope lost its own fields: %s", out)
	}
	if _, ok := got["sources"].([]any); !ok {
		t.Errorf("envelope mangled the raw JSON payload: %s", out)
	}

	empty := envelope([]byte(`{}`), "r1", 1, 1)
	if err := json.Unmarshal(empty, &got); err != nil {
		t.Fatalf("empty payload became invalid JSON: %v (%s)", err, empty)
	}
	if got["final_seq"].(float64) != 1 {
		t.Errorf("final_seq missing from an empty payload: %s", empty)
	}
}
