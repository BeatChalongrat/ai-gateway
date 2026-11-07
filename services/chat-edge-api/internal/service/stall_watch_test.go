package service

import (
	"testing"
	"time"
)

// The stall watcher is the whole point of server-side stall detection:
// only chat-edge can tell "the provider went quiet" from "the server is
// busy running a tool". These tests pin that distinction.

func drain(t *testing.T, ch <-chan StreamUpdate, within time.Duration) *ProviderStallNotice {
	t.Helper()
	select {
	case ev := <-ch:
		return ev.ProviderStall
	case <-time.After(within):
		return nil
	}
}

func withFastStall(t *testing.T, threshold, poll time.Duration) {
	t.Helper()
	oldT, oldP := providerStallAfter, providerStallPoll
	providerStallAfter, providerStallPoll = threshold, poll
	t.Cleanup(func() { providerStallAfter, providerStallPoll = oldT, oldP })
}

func TestStallWatch_FiresOnceWhileAwaitingProvider(t *testing.T) {
	withFastStall(t, 40*time.Millisecond, 5*time.Millisecond)
	out := make(chan StreamUpdate, 8)
	w := newStallWatch(out, providerStallAfter)
	defer w.Stop()

	w.BeginAwait()

	got := drain(t, out, time.Second)
	if got == nil || !got.Stalled {
		t.Fatalf("expected a stall notice, got %+v", got)
	}
	if got.WaitingSeconds < 0 {
		t.Errorf("waiting_seconds must not be negative: %d", got.WaitingSeconds)
	}

	// It must announce the stall ONCE — a notice re-sent every poll would
	// be a stream of duplicate frames for as long as the provider is out.
	if extra := drain(t, out, 150*time.Millisecond); extra != nil {
		t.Fatalf("stall notice repeated: %+v", extra)
	}
}

func TestStallWatch_SilentToolExecutionNeverStalls(t *testing.T) {
	withFastStall(t, 30*time.Millisecond, 5*time.Millisecond)
	out := make(chan StreamUpdate, 8)
	w := newStallWatch(out, providerStallAfter)
	defer w.Stop()

	// The pump is running a tool: a sandboxed run_python call can hold
	// the turn for a legitimate minute with zero provider output.
	w.BeginAwait()
	w.EndAwait()

	time.Sleep(120 * time.Millisecond) // >> threshold

	if got := drain(t, out, 50*time.Millisecond); got != nil {
		t.Fatalf("tool execution must never raise a stall, got %+v", got)
	}
}

func TestStallWatch_ProviderOutputClearsAndRearms(t *testing.T) {
	withFastStall(t, 30*time.Millisecond, 5*time.Millisecond)
	out := make(chan StreamUpdate, 8)
	w := newStallWatch(out, providerStallAfter)
	defer w.Stop()

	w.BeginAwait()
	if got := drain(t, out, time.Second); got == nil || !got.Stalled {
		t.Fatalf("expected the first stall, got %+v", got)
	}

	// Provider comes back — the client must be told so the notice clears
	// itself, exactly like the byte-based path does today.
	w.Activity()
	recovery := drain(t, out, time.Second)
	if recovery == nil || recovery.Stalled {
		t.Fatalf("expected a recovery notice (stalled=false), got %+v", recovery)
	}

	// ...and the watcher re-arms: a SECOND silence must be reported too.
	if got := drain(t, out, time.Second); got == nil || !got.Stalled {
		t.Fatalf("expected the watcher to re-arm after recovery, got %+v", got)
	}
}

func TestStallWatch_ActivityWithoutAStallStaysSilent(t *testing.T) {
	withFastStall(t, time.Hour, 5*time.Millisecond)
	out := make(chan StreamUpdate, 8)
	w := newStallWatch(out, providerStallAfter)
	defer w.Stop()

	w.BeginAwait()
	for i := 0; i < 5; i++ {
		w.Activity() // a normally streaming turn: frame after frame
	}

	if got := drain(t, out, 60*time.Millisecond); got != nil {
		t.Fatalf("a healthy stream must emit nothing, got %+v", got)
	}
}

// Stop() must return only once the watcher can no longer send, because
// the pump closes the channel immediately afterwards — a late send would
// panic the request goroutine. Run under -race with the pump's ordering.
func TestStallWatch_StopIsSafeBeforeChannelClose(t *testing.T) {
	withFastStall(t, time.Millisecond, time.Millisecond)
	for i := 0; i < 50; i++ {
		out := make(chan StreamUpdate, 1)
		w := newStallWatch(out, providerStallAfter)
		w.BeginAwait()
		time.Sleep(2 * time.Millisecond)
		w.Stop()
		close(out) // the pump's deferred close — must not panic
	}
}
