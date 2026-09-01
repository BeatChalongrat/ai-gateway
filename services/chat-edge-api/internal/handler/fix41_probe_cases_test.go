package handler

// FIX41 Part A/G — the four web-side probes (P1..P4) plus P1b.
// See fix41_probe_test.go for why they run in-process against a fake
// gateway instead of against the deployed stack.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// scriptedTurn is the shared upstream script: a turn that names its
// model, thinks, answers in pieces, reports a fallback halfway, then
// finishes. Interleaving is the point — a resume that replays a
// COLLAPSED snapshot cannot reproduce this order.
func scriptedTurn(hold <-chan struct{}, holdAfter int) *gwScript {
	return &gwScript{
		gap:       35 * time.Millisecond,
		hold:      hold,
		holdAfter: holdAfter,
		chunks: []string{
			typedChunk(map[string]any{"type": "model_resolved", "model": "probe-model-1"}),
			typedChunk(map[string]any{"type": "analysis_status", "status": "started"}),
			typedChunk(map[string]any{"type": "reasoning_summary_delta", "text": "thinking-A "}),
			typedChunk(map[string]any{"type": "analysis_status", "status": "completed"}),
			contentChunk("ONE "),
			contentChunk("TWO "),
			typedChunk(map[string]any{"type": "fallback_info", "fallback_info": map[string]any{
				"requested_model": "probe-model-1", "actual_model": "probe-model-2"}}),
			contentChunk("THREE "),
			contentChunk("FOUR "),
			contentChunk("FIVE"),
		},
	}
}

const scriptedTurnText = "ONE TWO THREE FOUR FIVE"

func messageIDFrom(fs []probeFrame) string {
	for _, f := range fs {
		if f.Event != "message_start" {
			continue
		}
		var d struct {
			MessageID string `json:"message_id"`
		}
		if json.Unmarshal(f.Data, &d) == nil {
			return d.MessageID
		}
	}
	return ""
}

func sendBody() string { return `{"content":"FIX41 probe turn","stream":true}` }

// ---------------------------------------------------------------------------
// P1 — close the tab mid-stream, reopen within 5s
// ---------------------------------------------------------------------------

func TestFix41ProbeP1_ReconnectMidStream(t *testing.T) {
	probeEnabled(t)
	hold := make(chan struct{})
	gw := newFakeGateway(t, scriptedTurn(hold, 5)) // hold after "TWO "
	st := newProbeStack(t, gw.URL+"/v1")

	a, code := openSSE(t, http.MethodPost,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages", st.srv.URL, st.convID), st.token, sendBody())
	if code != http.StatusOK {
		t.Fatalf("send returned %d, body=%s", code, a.raw())
	}
	if !waitFor(func() bool { return a.countEvent("content_delta") >= 2 }, 10*time.Second) {
		t.Fatalf("stream never produced 2 deltas; got %v", eventNames(a.frames()))
	}
	livePrefix := a.frames()
	msgID := messageIDFrom(livePrefix)
	if msgID == "" {
		t.Fatalf("no message_start in %v", eventNames(livePrefix))
	}
	// "close the tab"
	a.close()

	// "reopen within 5 seconds" — the browser's resume call.
	b, rcode := openSSE(t, http.MethodGet,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages/%s/stream", st.srv.URL, st.convID, msgID), st.token, "")
	if rcode != http.StatusOK {
		t.Fatalf("resume returned %d", rcode)
	}
	close(hold) // let the generation finish while the new client watches
	if !waitFor(func() bool { return b.countEvent("message_complete") >= 1 }, 20*time.Second) {
		t.Fatalf("resume never terminated; got %v", eventNames(b.frames()))
	}
	b.close()

	resume := b.frames()
	// The real client (chatStore.resetBubble) throws away the prefix it
	// already rendered before consuming the resume, so what the user ends
	// up with is exactly the resume stream.
	finalText := concatDeltas(resume)
	notes := []string{}
	verdict := "PASS"
	if finalText != scriptedTurnText {
		verdict = "FAIL"
		notes = append(notes, fmt.Sprintf("text after resume = %q, want %q", finalText, scriptedTurnText))
	}
	// Text completeness is only half the question. A resume that hands back
	// the right characters in the wrong event order still loses the tool
	// cards / phase markers that happened during the gap (see P1b).
	if countName(resume, "content_delta") != 5 && verdict == "PASS" {
		verdict = "PARTIAL"
	}
	liveOrder := strings.Join(eventNames(livePrefix), ",")
	resumeOrder := strings.Join(eventNames(resume), ",")
	// Order fidelity: in the LIVE stream fallback_info arrives BETWEEN
	// content deltas. Does the resume preserve any interleaving at all?
	liveInterleaved := strings.Contains(liveOrder, "content_delta,fallback_info") ||
		strings.Contains(liveOrder, "fallback_info,content_delta")
	resumeContentIdx, resumeFallbackIdx := -1, -1
	for i, n := range eventNames(resume) {
		if n == "content_delta" && resumeContentIdx < 0 {
			resumeContentIdx = i
		}
		if n == "fallback_info" && resumeFallbackIdx < 0 {
			resumeFallbackIdx = i
		}
	}
	notes = append(notes,
		fmt.Sprintf("live order:   %s", liveOrder),
		fmt.Sprintf("resume order: %s", resumeOrder),
		fmt.Sprintf("resume content_delta frames: %d (live stream sent 5)", countName(resume, "content_delta")),
	)
	if countName(resume, "content_delta") != 5 {
		notes = append(notes, "resume COLLAPSED the prefix into one content_delta — per-event ordering is lost")
	}
	record(t, probeResult{
		Probe:    "P1-web-reconnect",
		Question: "close the tab mid-stream, reopen within 5s: is the text complete, duplicated, and do interleaved non-content events survive in order?",
		Verdict:  verdict,
		Notes:    notes,
		Streams:  map[string][]probeFrame{"live_prefix": livePrefix, "resume": resume},
		Metrics: map[string]any{
			"final_text":                  finalText,
			"expected_text":               scriptedTurnText,
			"text_complete":               finalText == scriptedTurnText,
			"live_had_interleaving":       liveInterleaved,
			"resume_first_content_index":  resumeContentIdx,
			"resume_first_fallback_index": resumeFallbackIdx,
			"resume_content_delta_frames": countName(resume, "content_delta"),
			"resume_has_run_id":           anyHasRunID(resume),
			"resume_has_seq":              anyHasSeq(resume),
			"terminal_event":              lastEvent(resume),
		},
	})
	if verdict == "FAIL" {
		t.Logf("P1 FAIL (recorded): %v", notes)
	}
}

func countName(fs []probeFrame, name string) int {
	n := 0
	for _, f := range fs {
		if f.Event == name {
			n++
		}
	}
	return n
}

func lastEvent(fs []probeFrame) string {
	if len(fs) == 0 {
		return ""
	}
	return fs[len(fs)-1].Event
}

func anyHasRunID(fs []probeFrame) bool {
	for _, f := range fs {
		if f.RunID != "" {
			return true
		}
	}
	return false
}

func anyHasSeq(fs []probeFrame) bool {
	for _, f := range fs {
		if f.Seq != 0 {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// P1b — do tool_activity / code_execution that happened during the gap
//        survive the resume IN ORDER?
//
// The fake gateway cannot make chat-edge execute a tool (that needs the
// whole tool-executor wiring), so this probe drives the exact two
// functions that decide the answer — the real StreamBroker and the real
// writeResumeSnapshot — with a scripted event sequence, and diffs the
// resume order against the live order.
// ---------------------------------------------------------------------------

func TestFix41ProbeP1b_ToolEventOrderThroughResume(t *testing.T) {
	probeEnabled(t)

	seq := []service.StreamUpdate{
		{Delta: "before "},
		{ToolActivity: &service.ToolActivityUpdate{ID: "t1", Tool: "web_fetch", Phase: "started"}},
		{Delta: "during "},
		{CodeExecution: &service.CodeExecutionUpdate{ID: "c1", Status: "running", Code: "print(1)"}},
		{ToolActivity: &service.ToolActivityUpdate{ID: "t1", Tool: "web_fetch", Phase: "finished"}},
		{Delta: "after "},
		{CodeExecution: &service.CodeExecutionUpdate{ID: "c1", Status: "completed", Stdout: "1"}},
		{Delta: "end"},
	}

	// LIVE order: what a connected client sees, straight through the real
	// drain.
	live := &capWriter{}
	in := make(chan service.StreamUpdate, len(seq)+1)
	broker := service.NewStreamBroker()
	msgID := uuid.New()
	out := broker.Tee(msgID, in)
	for _, ev := range seq {
		in <- ev
	}
	in <- service.StreamUpdate{Done: true}
	close(in)
	done := make(chan struct{})
	drainStreamEvents(live, live, done, out, func() {})
	liveFrames := parseFrames(live.body())

	// RESUME order: what a client that reattached AFTER all of that sees.
	snap, _, cancel, _, ok := broker.Subscribe(msgID)
	if !ok {
		t.Fatalf("broker lost the entry")
	}
	defer cancel()
	rep := &capWriter{}
	h := &MessageHandler{}
	h.writeResumeSnapshot(rep, rep, snap)
	resumeFrames := parseFrames(rep.body())

	liveOrder := strings.Join(eventNames(liveFrames), ",")
	resumeOrder := strings.Join(eventNames(resumeFrames), ",")
	verdict := "PASS"
	notes := []string{
		"live order:   " + liveOrder,
		"resume order: " + resumeOrder,
		"NOTE (after FIX41): this probe drives writeResumeSnapshot, which is",
		"now only the FALLBACK for an entry that recorded no frames. The path",
		"a resume actually takes is the run ledger — measured by P1c. This",
		"probe is kept, and kept failing, because it is the evidence that a",
		"folded snapshot cannot reproduce an order it never stored.",
	}
	if liveOrder != resumeOrder {
		verdict = "FAIL"
		notes = append(notes, "the resume replay does NOT reproduce the live event order")
	}
	record(t, probeResult{
		Probe:    "P1b-tool-event-order",
		Question: "do tool_activity / code_execution frames emitted during the gap come back in their original position after a resume?",
		Verdict:  verdict,
		Notes:    notes,
		Streams:  map[string][]probeFrame{"live": liveFrames, "resume": resumeFrames},
		Metrics: map[string]any{
			"live_order":            liveOrder,
			"resume_order":          resumeOrder,
			"orders_match":          liveOrder == resumeOrder,
			"live_content_deltas":   countName(liveFrames, "content_delta"),
			"resume_content_deltas": countName(resumeFrames, "content_delta"),
		},
	})
}

// ---------------------------------------------------------------------------
// P2 — restart chat-edge-api mid-stream, then resume
// ---------------------------------------------------------------------------

func TestFix41ProbeP2_RestartMidStream(t *testing.T) {
	probeEnabled(t)
	hold := make(chan struct{})
	defer func() {
		select {
		case <-hold:
		default:
			close(hold)
		}
	}()
	gw := newFakeGateway(t, scriptedTurn(hold, 5))
	st := newProbeStack(t, gw.URL+"/v1")

	a, code := openSSE(t, http.MethodPost,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages", st.srv.URL, st.convID), st.token, sendBody())
	if code != http.StatusOK {
		t.Fatalf("send returned %d, body=%s", code, a.raw())
	}
	if !waitFor(func() bool { return a.countEvent("content_delta") >= 2 }, 10*time.Second) {
		t.Fatalf("stream never produced 2 deltas; got %v", eventNames(a.frames()))
	}
	livePrefix := a.frames()
	msgID := messageIDFrom(livePrefix)
	a.close()

	// THE RESTART. Everything the old process held in memory goes: the
	// MessageService, and with it the StreamBroker holding the only copy
	// of the in-flight prefix. The database is untouched — exactly what
	// `docker compose restart chat-edge-api` does. The new server has its
	// own empty broker; the row is still status=streaming, which is the
	// state a real restart leaves behind.
	//
	// The old httptest server is deliberately NOT closed here.
	// httptest.Server.Close() BLOCKS until every in-flight request
	// finishes, which lets the old generation run to completion and
	// persist — that turns the probe into "wait for the turn to end, then
	// resume" and hides the very failure it exists to measure. The first
	// run of this probe did exactly that and reported a false PASS.
	_, srv2 := bringUp(t, st.pool, gw.URL+"/v1")

	b, rcode := openSSE(t, http.MethodGet,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages/%s/stream", srv2.URL, st.convID, msgID), st.token, "")
	body := ""
	if rcode == http.StatusOK {
		waitFor(func() bool { return b.countEvent("message_complete") >= 1 }, 8*time.Second)
	} else {
		waitFor(func() bool { return len(b.raw()) > 0 }, 3*time.Second)
		body = b.raw()
	}
	b.close()
	resume := b.frames()

	verdict := "PASS"
	notes := []string{fmt.Sprintf("resume HTTP status after restart: %d", rcode)}
	if rcode != http.StatusOK {
		verdict = "FAIL"
		notes = append(notes, "resume was REFUSED after the restart: "+strings.TrimSpace(body))
	} else if concatDeltas(resume) == "" {
		verdict = "FAIL"
		notes = append(notes, "resume attached but replayed no content")
	}
	record(t, probeResult{
		Probe:    "P2-web-restart",
		Question: "restart chat-edge-api mid-stream, then resume: does the client get its generation back?",
		Verdict:  verdict,
		Notes:    notes,
		Streams:  map[string][]probeFrame{"live_prefix": livePrefix, "resume_after_restart": resume},
		Metrics: map[string]any{
			"resume_http_status": rcode,
			"resume_body":        body,
			"resume_text":        concatDeltas(resume),
			"terminal_event":     lastEvent(resume),
		},
	})
}

// ---------------------------------------------------------------------------
// P3 — press Stop mid-stream
// ---------------------------------------------------------------------------

func TestFix41ProbeP3_Cancel(t *testing.T) {
	probeEnabled(t)
	hold := make(chan struct{})
	defer func() {
		select {
		case <-hold:
		default:
			close(hold)
		}
	}()
	gw := newFakeGateway(t, scriptedTurn(hold, 5))
	st := newProbeStack(t, gw.URL+"/v1")

	a, code := openSSE(t, http.MethodPost,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages", st.srv.URL, st.convID), st.token, sendBody())
	if code != http.StatusOK {
		t.Fatalf("send returned %d, body=%s", code, a.raw())
	}
	if !waitFor(func() bool { return a.countEvent("content_delta") >= 2 }, 10*time.Second) {
		t.Fatalf("no deltas; got %v", eventNames(a.frames()))
	}
	msgID := messageIDFrom(a.frames())

	// A SECOND viewer — the only client that can be TOLD anything by the
	// wire. (The tab that presses Stop aborts its own fetch and never
	// reads another byte, so it can't learn "cancelled" from the stream
	// either way.)
	b, rcode := openSSE(t, http.MethodGet,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages/%s/stream", st.srv.URL, st.convID, msgID), st.token, "")
	if rcode != http.StatusOK {
		t.Fatalf("resume viewer returned %d", rcode)
	}
	waitFor(func() bool { return b.countEvent("message_start") >= 1 }, 5*time.Second)

	// Stop.
	c1, s1 := postJSON(t, fmt.Sprintf("%s/api/v1/conversations/%s/cancel", st.srv.URL, st.convID), st.token)
	// Idempotency: press it again.
	c2, s2 := postJSON(t, fmt.Sprintf("%s/api/v1/conversations/%s/cancel", st.srv.URL, st.convID), st.token)

	waitFor(func() bool {
		return b.countEvent("message_complete") >= 1 || b.countEvent("error") >= 1 || b.countEvent("cancelled") >= 1
	}, 10*time.Second)
	// Settle: the handler writes files/sources and its terminator AFTER
	// the drain's last frame (the resume path even sleeps 250 ms first).
	// Closing the moment the first terminal-ish frame lands would record a
	// truncated frame log and mis-name the terminal event.
	time.Sleep(1200 * time.Millisecond)
	a.close()
	b.close()

	primary := a.frames()
	viewer := b.frames()
	term := lastEvent(viewer)
	verdict := "PASS"
	notes := []string{
		fmt.Sprintf("cancel #1 → %d %s", s1, strings.TrimSpace(c1)),
		fmt.Sprintf("cancel #2 → %d %s", s2, strings.TrimSpace(c2)),
		"attached viewer's last frame: " + term,
		"primary stream's last frame:  " + lastEvent(primary),
	}
	if term != "cancelled" {
		verdict = "FAIL"
		notes = append(notes, "a client that did NOT press Stop cannot tell a cancel from a normal completion")
	}
	if s2 >= 500 {
		verdict = "FAIL"
		notes = append(notes, "second cancel was not idempotent")
	}
	record(t, probeResult{
		Probe:    "P3-web-cancel",
		Question: "press Stop mid-stream: how does a client learn it was cancelled rather than disconnected?",
		Verdict:  verdict,
		Notes:    notes,
		Streams:  map[string][]probeFrame{"primary": primary, "attached_viewer": viewer},
		Metrics: map[string]any{
			"cancel_1_status":        s1,
			"cancel_1_body":          strings.TrimSpace(c1),
			"cancel_2_status":        s2,
			"cancel_2_body":          strings.TrimSpace(c2),
			"viewer_terminal_event":  term,
			"primary_terminal_event": lastEvent(primary),
		},
	})
}

func postJSON(t *testing.T, url, token string) (string, int) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, url, strings.NewReader("{}"))
	if err != nil {
		t.Fatalf("build cancel request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("cancel: %v", err)
	}
	defer resp.Body.Close()
	var sb strings.Builder
	buf := make([]byte, 1024)
	for {
		n, err := resp.Body.Read(buf)
		sb.Write(buf[:n])
		if err != nil {
			break
		}
	}
	return sb.String(), resp.StatusCode
}

// ---------------------------------------------------------------------------
// P4 — the provider errors mid-stream
// ---------------------------------------------------------------------------

func TestFix41ProbeP4_ProviderErrorMidStream(t *testing.T) {
	probeEnabled(t)
	gw := newFakeGateway(t, &gwScript{
		gap: 25 * time.Millisecond,
		chunks: []string{
			contentChunk("ONE "),
			contentChunk("TWO "),
			typedChunk(map[string]any{"error": map[string]any{
				"code": "all_models_failed", "message": "probe-induced provider failure",
				"attempts": []any{map[string]any{"model": "probe-model-1", "error": "503"}},
			}}),
		},
	})
	st := newProbeStack(t, gw.URL+"/v1")

	a, code := openSSE(t, http.MethodPost,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages", st.srv.URL, st.convID), st.token, sendBody())
	if code != http.StatusOK {
		t.Fatalf("send returned %d, body=%s", code, a.raw())
	}
	waitFor(func() bool {
		return a.countEvent("message_complete") >= 1 || (a.countEvent("error") >= 1 && a.countEvent("message_complete") >= 1)
	}, 15*time.Second)
	time.Sleep(300 * time.Millisecond)
	a.close()
	fs := a.frames()

	term := lastEvent(fs)
	verdict := "PASS"
	notes := []string{"last frame: " + term, "order: " + strings.Join(eventNames(fs), ",")}
	if term != "error" {
		verdict = "FAIL"
		notes = append(notes, "a failed turn does NOT end on a terminal `error` frame — the last thing the client sees is a success terminator")
	}
	record(t, probeResult{
		Probe:    "P4-web-provider-error",
		Question: "the provider errors mid-stream: does the client get a terminal error frame?",
		Verdict:  verdict,
		Notes:    notes,
		Streams:  map[string][]probeFrame{"primary": fs},
		Metrics: map[string]any{
			"terminal_event":  term,
			"error_frames":    countName(fs, "error"),
			"complete_frames": countName(fs, "message_complete"),
			"has_run_id":      anyHasRunID(fs),
			"has_seq":         anyHasSeq(fs),
		},
	})
}

// ---------------------------------------------------------------------------
// P1c — the same question as P1b, asked of the path a resume ACTUALLY takes
//        after FIX41: the run ledger.
//
// P1b stays as it is and keeps failing, because it measures
// writeResumeSnapshot, which after FIX41 is only the fallback for an entry
// that recorded nothing. Deleting it would erase the evidence that the
// folded snapshot cannot reproduce an order it never stored; rewriting it
// would be moving the goalposts. So the new path gets its own probe, and
// the report shows both.
//
// P1c cannot be run against the pre-FIX41 build: there was no ledger to
// measure. That is stated rather than presented as an improved number.
// ---------------------------------------------------------------------------

func TestFix41ProbeP1c_ToolEventOrderThroughLedger(t *testing.T) {
	probeEnabled(t)

	broker := service.NewStreamBroker()
	msgID := uuid.New()
	runID := "probe-run-p1c"
	broker.OpenRun(msgID, runID)

	live := &capWriter{}
	rw := newRunWriter(live, runID, func(event string, build func(seq, final int64) []byte) (int64, bool) {
		return broker.Record(msgID, event, build)
	})
	rw.started = true

	// The same interleaving P1b uses, written the way the primary stream
	// writes it: through writeSSEEvent, which is the only place frames are
	// numbered and recorded.
	writeSSEEvent(rw, live, "message_start", map[string]any{"message_id": msgID.String()})
	writeSSEEvent(rw, live, "content_delta", map[string]any{"delta": "before "})
	writeSSEEvent(rw, live, "tool_activity", &service.ToolActivityUpdate{ID: "t1", Tool: "web_fetch", Phase: "started"})
	writeSSEEvent(rw, live, "content_delta", map[string]any{"delta": "during "})
	writeSSEEvent(rw, live, "code_execution", &service.CodeExecutionUpdate{ID: "c1", Status: "running", Code: "print(1)"})
	writeSSEEvent(rw, live, "tool_activity", &service.ToolActivityUpdate{ID: "t1", Tool: "web_fetch", Phase: "finished"})
	writeSSEEvent(rw, live, "content_delta", map[string]any{"delta": "after "})
	writeSSEEvent(rw, live, "code_execution", &service.CodeExecutionUpdate{ID: "c1", Status: "completed", Stdout: "1"})
	writeSSEEvent(rw, live, "content_delta", map[string]any{"delta": "end"})
	writeSSEEvent(rw, live, "message_complete", map[string]any{"message_id": msgID.String()})

	liveFrames := parseFrames(live.body())

	// A client that reattaches after all of that, with no cursor.
	fa, ferr, ok := broker.SubscribeFrames(msgID, 0)
	if !ok || ferr != nil {
		t.Fatalf("SubscribeFrames ok=%v err=%v", ok, ferr)
	}
	defer fa.Cancel()
	rep := &capWriter{}
	rrw := newReplayWriter(rep, fa.RunID)
	rrw.started = true
	for _, le := range fa.Replay {
		rrw.writeRecorded(rep, le.Event, le.Data)
	}
	resumeFrames := parseFrames(rep.body())

	// And a client that already had the first four frames.
	faCursor, ferr2, ok2 := broker.SubscribeFrames(msgID, 4)
	if !ok2 || ferr2 != nil {
		t.Fatalf("SubscribeFrames(4) ok=%v err=%v", ok2, ferr2)
	}
	defer faCursor.Cancel()
	cur := &capWriter{}
	crw := newReplayWriter(cur, faCursor.RunID)
	crw.started = true
	for _, le := range faCursor.Replay {
		crw.writeRecorded(cur, le.Event, le.Data)
	}
	cursorFrames := parseFrames(cur.body())

	liveOrder := strings.Join(eventNames(liveFrames), ",")
	resumeOrder := strings.Join(eventNames(resumeFrames), ",")

	// seq must be 1..N with no gap and no repeat, and identical between
	// the live stream and its replay.
	seqOK := len(liveFrames) > 0 && len(liveFrames) == len(resumeFrames)
	for i := range liveFrames {
		if liveFrames[i].Seq != int64(i+1) {
			seqOK = false
			break
		}
		if i < len(resumeFrames) && (resumeFrames[i].Seq != liveFrames[i].Seq || resumeFrames[i].RunID != liveFrames[i].RunID) {
			seqOK = false
			break
		}
	}
	cursorSeqOK := true
	for i, f := range cursorFrames {
		if f.Seq != int64(i+5) {
			cursorSeqOK = false
			break
		}
	}

	verdict := "PASS"
	notes := []string{
		"live order:   " + liveOrder,
		"resume order: " + resumeOrder,
		fmt.Sprintf("starting_after=4 replayed %d frames, first seq %d", len(cursorFrames), firstSeq(cursorFrames)),
	}
	if liveOrder != resumeOrder {
		verdict = "FAIL"
		notes = append(notes, "the ledger replay does NOT reproduce the live event order")
	}
	if !seqOK {
		verdict = "FAIL"
		notes = append(notes, "seq/run_id are not identical between the live stream and its replay")
	}
	if !cursorSeqOK {
		verdict = "FAIL"
		notes = append(notes, "starting_after=4 did not resume at seq 5")
	}
	record(t, probeResult{
		Probe:    "P1c-tool-event-order-ledger",
		Question: "through the run ledger (the path a resume takes after FIX41): do tool_activity / code_execution come back in position, with their original seq?",
		Verdict:  verdict,
		Notes:    notes,
		Streams: map[string][]probeFrame{
			"live": liveFrames, "resume_full": resumeFrames, "resume_after_4": cursorFrames,
		},
		Metrics: map[string]any{
			"live_order":            liveOrder,
			"resume_order":          resumeOrder,
			"orders_match":          liveOrder == resumeOrder,
			"seq_identical":         seqOK,
			"cursor_resumes_at_5":   cursorSeqOK,
			"live_frames":           len(liveFrames),
			"resume_frames":         len(resumeFrames),
			"resume_after_4_frames": len(cursorFrames),
			"run_id":                fa.RunID,
		},
	})
}

func firstSeq(fs []probeFrame) int64 {
	if len(fs) == 0 {
		return 0
	}
	return fs[0].Seq
}
