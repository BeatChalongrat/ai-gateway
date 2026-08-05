package service

// Phase 5 — StreamBroker unit tests. Pure in-memory; no DB, no HTTP.

import (
	"testing"
	"time"

	"github.com/google/uuid"
)

// collect drains a channel until close, with a deadline so a broken
// broker fails the test instead of hanging it.
func collect(t *testing.T, ch <-chan StreamUpdate) []StreamUpdate {
	t.Helper()
	var got []StreamUpdate
	timeout := time.After(2 * time.Second)
	for {
		select {
		case ev, ok := <-ch:
			if !ok {
				return got
			}
			got = append(got, ev)
		case <-timeout:
			t.Fatal("channel never closed")
		}
	}
}

func TestBroker_TeeMirrorsAndSubscriberGetsPrefixPlusLiveTail(t *testing.T) {
	b := NewStreamBroker()
	msgID := uuid.New()
	in := make(chan StreamUpdate)
	out := b.Tee(msgID, in)

	// Primary consumer runs concurrently like the handler drain does.
	primaryDone := make(chan []StreamUpdate, 1)
	go func() { primaryDone <- collectNoT(out) }()

	// Two deltas flow BEFORE the client reattaches. A channel send
	// returns when the tee RECEIVES the value — publish runs just
	// after — so poll until the snapshot reflects all three events
	// instead of racing the tee goroutine.
	in <- StreamUpdate{Delta: "Hello, "}
	in <- StreamUpdate{ReasoningDelta: "think"}
	in <- StreamUpdate{Delta: "world"}
	waitSnapshot(t, b, msgID, func(s StreamSnapshot) bool {
		return s.Content == "Hello, world" && s.Reasoning == "think"
	})

	snap, events, cancelSub, lagged, ok := b.Subscribe(msgID)
	if !ok {
		t.Fatal("subscribe: entry not found while streaming")
	}
	defer cancelSub()
	defer func() {
		if lagged() {
			t.Error("subscriber wrongly flagged lagged")
		}
	}()
	if snap.Content != "Hello, world" || snap.Reasoning != "think" {
		t.Fatalf("snapshot prefix = %q / %q", snap.Content, snap.Reasoning)
	}
	if snap.Terminal != nil {
		t.Fatal("snapshot must not be terminal while streaming")
	}

	// Live tail after the reattach.
	in <- StreamUpdate{Delta: "!"}
	in <- StreamUpdate{Done: true, FinishReason: "stop"}
	close(in)

	tail := collect(t, events)
	var tailContent string
	sawDone := false
	for _, ev := range tail {
		tailContent += ev.Delta
		if ev.Done {
			sawDone = true
		}
	}
	if tailContent != "!" || !sawDone {
		t.Fatalf("live tail = %q sawDone=%v, want %q true", tailContent, sawDone, "!")
	}

	// The primary consumer must have seen EVERYTHING (tee is a mirror,
	// not a diversion).
	prim := <-primaryDone
	var primContent string
	for _, ev := range prim {
		primContent += ev.Delta
	}
	if primContent != "Hello, world!" {
		t.Fatalf("primary consumer content = %q, want %q", primContent, "Hello, world!")
	}
}

func collectNoT(ch <-chan StreamUpdate) []StreamUpdate {
	var got []StreamUpdate
	for ev := range ch {
		got = append(got, ev)
	}
	return got
}

func TestBroker_SubscribeAfterTerminalReturnsFullSnapshot(t *testing.T) {
	b := NewStreamBroker()
	// Freeze cleanup so the entry survives the tee's end for this test.
	b.afterFunc = func(time.Duration, func()) *time.Timer { return nil }
	msgID := uuid.New()
	in := make(chan StreamUpdate, 4)
	in <- StreamUpdate{Delta: "done text"}
	in <- StreamUpdate{Done: true, FinishReason: "stop", Usage: &GatewayChatUsage{CompletionTokens: 9}}
	close(in)
	out := b.Tee(msgID, in)
	collectNoT(out) // drain primary to completion

	snap, events, _, _, ok := b.Subscribe(msgID)
	if !ok {
		t.Fatal("terminal entry should still be subscribable within TTL")
	}
	if events != nil {
		t.Fatal("terminal subscribe must not return a live channel")
	}
	if snap.Content != "done text" || snap.Terminal == nil || !snap.Terminal.Done {
		t.Fatalf("terminal snapshot wrong: %+v", snap)
	}
	if snap.Terminal.Usage == nil || snap.Terminal.Usage.CompletionTokens != 9 {
		t.Fatalf("terminal usage not retained: %+v", snap.Terminal.Usage)
	}
}

func TestBroker_UnsubscribeFreesSlotNoLeak(t *testing.T) {
	b := NewStreamBroker()
	msgID := uuid.New()
	in := make(chan StreamUpdate)
	out := b.Tee(msgID, in)
	go collectNoT(out)

	in <- StreamUpdate{Delta: "x"}
	_, _, cancel1, _, _ := b.Subscribe(msgID)
	_, _, cancel2, _, _ := b.Subscribe(msgID)
	if n := b.SubscriberCount(msgID); n != 2 {
		t.Fatalf("subscribers = %d, want 2", n)
	}
	cancel1()
	cancel1() // idempotent
	if n := b.SubscriberCount(msgID); n != 1 {
		t.Fatalf("after one unsubscribe = %d, want 1", n)
	}
	cancel2()
	if n := b.SubscriberCount(msgID); n != 0 {
		t.Fatalf("after both unsubscribed = %d, want 0", n)
	}
	// Terminal with zero subscribers must not panic or leak.
	in <- StreamUpdate{Done: true}
	close(in)
}

func TestBroker_TerminalClosesAllSubscribers(t *testing.T) {
	b := NewStreamBroker()
	msgID := uuid.New()
	in := make(chan StreamUpdate)
	out := b.Tee(msgID, in)
	go collectNoT(out)

	in <- StreamUpdate{Delta: "a"}
	_, ev1, _, _, _ := b.Subscribe(msgID)
	_, ev2, _, _, _ := b.Subscribe(msgID)
	in <- StreamUpdate{Done: true}
	close(in)

	collect(t, ev1)
	collect(t, ev2)
	if n := b.SubscriberCount(msgID); n != 0 {
		t.Fatalf("terminal left %d subscriber slots, want 0", n)
	}
}

func TestBroker_LaggedSubscriberCutLoose(t *testing.T) {
	b := NewStreamBroker()
	msgID := uuid.New()
	in := make(chan StreamUpdate)
	out := b.Tee(msgID, in)
	go collectNoT(out)

	in <- StreamUpdate{Delta: "seed"}
	_, events, cancelSub, lagged, _ := b.Subscribe(msgID)
	defer cancelSub()

	// Never read from events: overflow the subscriber buffer.
	for i := 0; i < resumeSubBuffer+8; i++ {
		in <- StreamUpdate{Delta: "x"}
	}
	// The subscriber must have been dropped (closed channel) and
	// flagged lagged; the publisher must never have blocked (we got
	// here at all).
	collect(t, events)
	if !lagged() {
		t.Fatal("overflowed subscriber not flagged lagged")
	}
	if n := b.SubscriberCount(msgID); n != 0 {
		t.Fatalf("lagged subscriber slot leaked: %d", n)
	}
	in <- StreamUpdate{Done: true}
	close(in)
}

func TestBroker_EntryDroppedAfterTTL(t *testing.T) {
	b := NewStreamBroker()
	fired := make(chan func(), 1)
	b.afterFunc = func(_ time.Duration, f func()) *time.Timer {
		fired <- f
		return nil
	}
	msgID := uuid.New()
	in := make(chan StreamUpdate, 2)
	in <- StreamUpdate{Done: true}
	close(in)
	collectNoT(b.Tee(msgID, in))

	cleanup := <-fired
	if !b.Has(msgID) {
		t.Fatal("entry should be retained until the TTL fires")
	}
	cleanup()
	if b.Has(msgID) {
		t.Fatal("entry not dropped after TTL")
	}
	if _, _, _, _, ok := b.Subscribe(msgID); ok {
		t.Fatal("subscribe must miss after the entry is dropped")
	}
}

func TestBroker_BareChannelCloseSynthesizesTerminal(t *testing.T) {
	// rejectImageStream closes its channel without a Done event; the
	// tee must still turn the entry terminal so late subscribers don't
	// hang forever.
	b := NewStreamBroker()
	b.afterFunc = func(time.Duration, func()) *time.Timer { return nil }
	msgID := uuid.New()
	in := make(chan StreamUpdate, 1)
	in <- StreamUpdate{Delta: "only"}
	close(in)
	collectNoT(b.Tee(msgID, in))

	snap, events, _, _, ok := b.Subscribe(msgID)
	if !ok || events != nil || snap.Terminal == nil || !snap.Terminal.Done {
		t.Fatalf("bare close must synthesize a Done terminal: ok=%v events=%v snap=%+v", ok, events, snap)
	}
	if snap.Content != "only" {
		t.Fatalf("prefix lost on bare close: %q", snap.Content)
	}
}

func TestBroker_StickyPhaseMarkersInSnapshot(t *testing.T) {
	b := NewStreamBroker()
	msgID := uuid.New()
	in := make(chan StreamUpdate)
	out := b.Tee(msgID, in)
	go collectNoT(out)

	in <- StreamUpdate{SearchStatus: "searching"}
	in <- StreamUpdate{AnalysisStatus: "started"}
	in <- StreamUpdate{ImageStatus: "started"}
	waitSnapshot(t, b, msgID, func(s StreamSnapshot) bool {
		return s.SearchStatus == "searching" && s.AnalysisStatus == "started" && s.ImageStatus == "started"
	})
	in <- StreamUpdate{Done: true}
	close(in)
}

// waitSnapshot polls Subscribe (immediately unsubscribing each probe)
// until the snapshot satisfies cond — the tee publishes asynchronously
// after receiving from ``in``, so asserting right after a send races.
func waitSnapshot(t *testing.T, b *StreamBroker, msgID uuid.UUID, cond func(StreamSnapshot) bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		snap, _, cancelSub, _, ok := b.Subscribe(msgID)
		cancelSub()
		if ok && cond(snap) {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("snapshot never reached expected state")
}
