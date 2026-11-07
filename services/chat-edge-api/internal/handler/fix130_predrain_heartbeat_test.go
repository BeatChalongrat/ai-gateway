package handler

import (
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// FIX130 C1 — the stretch between the FIX33 early open (compaction) and the
// drain loop is heartbeated too, and the pinger is really gone once stopped:
// nothing it writes can follow the frame the handler writes next.
func TestFIX130_PreDrainHeartbeatPingsUntilStoppedAndNeverAfter(t *testing.T) {
	old := heartbeatInterval
	heartbeatInterval = 10 * time.Millisecond
	defer func() { heartbeatInterval = old }()

	rec := httptest.NewRecorder()
	var mu sync.Mutex
	stop := startPreDrainHeartbeat(rec, rec, &mu)
	// The extraction pause: wait (bounded) for three pings — a fixed sleep
	// flakes on a loaded Windows host, where timers fire at ~15 ms steps.
	pings := 0
	for deadline := time.Now().Add(2 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		mu.Lock()
		pings = strings.Count(rec.Body.String(), ": ping\n\n")
		mu.Unlock()
		if pings >= 3 {
			break
		}
	}
	stop()
	stop() // idempotent
	if pings < 3 {
		t.Fatalf("want >=3 pings during the pre-drain pause, got %d", pings)
	}
	before := rec.Body.Len()
	time.Sleep(40 * time.Millisecond)
	if rec.Body.Len() != before {
		t.Fatalf("the pinger wrote after stop(): %q", rec.Body.String())
	}
}
