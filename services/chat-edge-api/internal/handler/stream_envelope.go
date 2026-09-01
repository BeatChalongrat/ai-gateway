package handler

// FIX41 — the run envelope (docs/STREAM_CONTRACT.md §2 and §3).
//
// Every frame that leaves chat-edge carries the id of the generation it
// belongs to and a gapless sequence number, and every run ends on one of
// three terminal events. Both facts are produced HERE and nowhere else.
//
// Why a wrapped ResponseWriter rather than a new parameter: message.go
// has 46 writeSSEEvent call sites. Threading run/seq through all of them
// would be 46 chances to forget one, and a forgotten one is an unnumbered
// frame — a hole a client cannot detect, which is the exact class of bug
// this change exists to remove. Wrapping w means the call sites stay
// byte-for-byte unchanged and only the three places that OPEN a stream
// know about runs at all.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
)

// runWriter is the single point where run_id / seq / final_seq are
// attached and where the run ledger is fed.
type runWriter struct {
	http.ResponseWriter
	runID string
	// record feeds the run ledger and hands back the assigned seq. It is
	// nil for a writer that must not author frames — a resume stream
	// replays what the primary already recorded; if it recorded too, the
	// same event would land twice under two different numbers and
	// "gapless, no duplicates" would be a lie told by whichever writer
	// got there first.
	record func(event string, build func(seq, finalSeq int64) []byte) (int64, bool)
	// localSeq numbers frames when there is no ledger to number them: a
	// replay of a row that is already terminal (the run itself is gone),
	// or a direct-constructed service in a unit test.
	localSeq int64
	mu       sync.Mutex
	terminal string
	// started reports whether SSE headers went out. Before that a failure
	// is still a plain HTTP error response, not a frame.
	started bool
}

// ensureTerminal is the backstop for STREAM_CONTRACT §3: an open stream
// that reaches the end of its handler without a terminal frame gets one.
// Deferred rather than written at each exit, because "every return path"
// includes the ones a future change adds.
func (rw *runWriter) ensureTerminal(flusher http.Flusher) {
	if !rw.started || rw.terminalWritten() {
		return
	}
	writeSSEEvent(rw, flusher, "error", map[string]any{
		"code":    CodeInternalError,
		"message": "the stream ended without a result",
	})
}

// newRunWriter wraps w for a run that is being authored.
func newRunWriter(
	w http.ResponseWriter,
	runID string,
	record func(string, func(int64, int64) []byte) (int64, bool),
) *runWriter {
	return &runWriter{ResponseWriter: w, runID: runID, record: record}
}

// newReplayWriter wraps w for a stream that only replays. It numbers its
// own frames locally under runID and records nothing.
func newReplayWriter(w http.ResponseWriter, runID string) *runWriter {
	return &runWriter{ResponseWriter: w, runID: runID}
}

// newReplayWriterFrom is newReplayWriter for a replay that CONTINUES an
// existing run: any frame this response has to synthesise (a terminal the
// dead process never got to write) picks up after the last recorded seq.
//
// Getting this wrong is silent and total: a client whose cursor is at 7
// drops a frame numbered 1 for the same run, so the terminal that says
// "this was interrupted" never reaches it and the answer stays spinning
// forever. Caught by probe P2 after the restart path started working.
func newReplayWriterFrom(w http.ResponseWriter, runID string, afterSeq int64) *runWriter {
	return &runWriter{ResponseWriter: w, runID: runID, localSeq: afterSeq}
}

func (rw *runWriter) emit(flusher http.Flusher, event string, payload []byte) {
	var out []byte
	if rw.record != nil {
		if _, ok := rw.record(event, func(seq, final int64) []byte {
			out = envelope(payload, rw.runID, seq, final)
			return out
		}); !ok {
			out = nil
		}
	}
	if out == nil {
		rw.mu.Lock()
		rw.localSeq++
		seq := rw.localSeq
		rw.mu.Unlock()
		final := int64(0)
		if isTerminalEvent(event) {
			final = seq
		}
		out = envelope(payload, rw.runID, seq, final)
	}
	rw.writeFrame(flusher, event, out)
}

// writeRecorded emits a frame that is ALREADY enveloped — a ledger
// replay, or a live frame forwarded off the ledger. It never renumbers:
// the seq inside those bytes is the run's own, and that is the whole
// point of storing the bytes.
func (rw *runWriter) writeRecorded(flusher http.Flusher, event string, payload []byte) {
	rw.writeFrame(flusher, event, payload)
}

func (rw *runWriter) writeFrame(flusher http.Flusher, event string, payload []byte) {
	if isTerminalEvent(event) {
		rw.mu.Lock()
		if rw.terminal == "" {
			rw.terminal = event
		}
		rw.mu.Unlock()
	}
	_, _ = fmt.Fprintf(rw.ResponseWriter, "event: %s\ndata: %s\n\n", event, payload)
	flusher.Flush()
}

// terminalWritten reports whether this response already carried a
// terminal frame. Stream handlers defer on it, so no return path — not
// even one added later by someone who never read this file — can end a
// run with silence.
func (rw *runWriter) terminalWritten() bool {
	rw.mu.Lock()
	defer rw.mu.Unlock()
	return rw.terminal != ""
}

func isTerminalEvent(event string) bool {
	return event == "message_complete" || event == "error" || event == "cancelled"
}

// envelope splices run_id / seq / final_seq into an already-marshalled
// payload.
//
// A byte splice rather than a re-marshal, because “data“ at the call
// sites is sometimes a struct and sometimes carries a json.RawMessage
// (message_sources forwards stored JSON verbatim). Round-tripping through
// map[string]any would re-encode and reorder a payload this code has no
// business rewriting.
//
// seq == 0 means "unnumbered" and is omitted rather than sent as 0: a
// client must be able to tell "this server does not number frames" from
// "this frame is number zero". final_seq is present only on a terminal.
// A non-object payload is passed through untouched rather than corrupted.
func envelope(payload []byte, runID string, seq, finalSeq int64) []byte {
	trimmed := bytes.TrimSpace(payload)
	if len(trimmed) == 0 || trimmed[0] != '{' {
		return payload
	}
	var b bytes.Buffer
	b.Grow(len(trimmed) + 96)
	b.WriteString(`{"run_id":`)
	idBuf, _ := json.Marshal(runID)
	b.Write(idBuf)
	if seq > 0 {
		fmt.Fprintf(&b, `,"seq":%d`, seq)
	}
	if finalSeq > 0 {
		fmt.Fprintf(&b, `,"final_seq":%d`, finalSeq)
	}
	if len(trimmed) > 2 { // not "{}"
		b.WriteByte(',')
		b.Write(trimmed[1:])
	} else {
		b.WriteByte('}')
	}
	return b.Bytes()
}
