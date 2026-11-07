package service

// FIX130 — hidden thinking is model activity. When the person does not see
// the reasoning (the thinking toggle off, or a row whose box cannot stop
// thinking), the gateway streams no reasoning frame — it writes an SSE
// comment ": thinking" every few seconds instead (public_chat.py,
// HIDDEN_REASONING_KEEPALIVE_S). The reader used to drop every comment, so
// the no-progress guard (providerGiveUpAfter) cut a model that was still
// thinking. It must count exactly that comment, and nothing else it skips.

import (
	"bufio"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestFIX130_HiddenThinkingKeepaliveIsNeverCut(t *testing.T) {
	// 300 ms of ": thinking" every 30 ms, give-up at 120 ms: the turn ends
	// with the answer, not provider_timeout.
	withFastGiveUp(t, time.Hour, 120*time.Millisecond, time.Hour)
	updates, ended := pumpAgainst(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		for i := 0; i < 10; i++ {
			_, _ = w.Write([]byte(": thinking\n\n"))
			w.(http.Flusher).Flush()
			time.Sleep(30 * time.Millisecond)
		}
		_, _ = w.Write([]byte(sseText("5 ครับ")))
	})
	if !ended {
		t.Fatal("pump did not end")
	}
	text, done, err, _ := summarise(updates)
	if err != nil || !done || text != "5 ครับ" {
		t.Fatalf("hidden thinking was cut as silence: text=%q done=%v err=%v", text, done, err)
	}
}

func TestFIX130_OtherCommentsAreStillNotActivity(t *testing.T) {
	// Any other comment (a proxy's ": ping") proves nothing about the model:
	// the no-progress guard still ends the turn.
	withFastGiveUp(t, time.Hour, 120*time.Millisecond, time.Hour)
	updates, ended := pumpAgainst(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		for i := 0; i < 20; i++ {
			_, _ = w.Write([]byte(": ping\n\n"))
			w.(http.Flusher).Flush()
			select {
			case <-r.Context().Done():
				return
			case <-fix130Release:
				return
			case <-time.After(30 * time.Millisecond):
			}
		}
	})
	if !ended {
		t.Fatal("pump did not end")
	}
	_, _, err, _ := summarise(updates)
	var np *ProviderNoProgressError
	if err == nil || !strings.Contains(err.Error(), "no output from the model") {
		t.Fatalf("pings kept a silent model alive: err=%v (want %T)", err, np)
	}
}

func TestFIX130_ReaderSurfacesThinkingKeepalive(t *testing.T) {
	body := ": thinking\n\n" + `data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}` + "\n\n"
	bodyRC := io.NopCloser(strings.NewReader(body))
	sr := &StreamReader{body: bodyRC, scanner: bufio.NewScanner(bodyRC)}
	ev, err := sr.Next()
	if err != nil || ev == nil || !ev.Keepalive || ev.Delta != "" {
		t.Fatalf("first event: want a keepalive with no text, got %+v err=%v", ev, err)
	}
	ev, err = sr.Next()
	if err != nil || ev == nil || ev.Keepalive || ev.Delta != "ok" {
		t.Fatalf("second event: want the content delta, got %+v err=%v", ev, err)
	}
}
