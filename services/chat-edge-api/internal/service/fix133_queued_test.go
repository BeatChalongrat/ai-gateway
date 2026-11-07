package service

import "testing"

// FIX133 A2 — the gateway's ``queued`` frame is parsed into a typed event
// (position, ahead, active, waiting, max_active, estimate_s) and never mistaken
// for content; position 0 (admitted) parses too, so the client can clear.
func TestFix133QueuedFrameParses(t *testing.T) {
	ev, err := parseStreamChunk(`{"type":"queued","position":3,"ahead":2,"active":12,"waiting":4,"max_active":12,"estimate_s":20}`, "")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if ev.Queued == nil {
		t.Fatalf("expected a queued event, got %+v", ev)
	}
	q := ev.Queued
	if q.Position != 3 || q.Ahead != 2 || q.Active != 12 || q.Waiting != 4 || q.MaxActive != 12 {
		t.Fatalf("fields: %+v", q)
	}
	if q.EstimateS == nil || *q.EstimateS != 20 {
		t.Fatalf("estimate: %+v", q.EstimateS)
	}
	if ev.Delta != "" || ev.Keepalive || ev.Done {
		t.Fatalf("a queued frame carries no content: %+v", ev)
	}
	ev, err = parseStreamChunk(`{"type":"queued","position":0,"ahead":0,"active":12,"waiting":1,"max_active":12}`, "")
	if err != nil || ev.Queued == nil || ev.Queued.Position != 0 || ev.Queued.EstimateS != nil {
		t.Fatalf("admitted frame: %+v err=%v", ev.Queued, err)
	}
}

// The resume broker keeps the LATEST queue state, so a client reattaching
// while the turn still waits sees that it waits (and one reattaching after
// it started sees position 0 — nothing).
func TestFix133QueuedIsStickyInTheBroker(t *testing.T) {
	e := &brokerEntry{}
	e.publish(StreamUpdate{Queued: &QueueStatus{Position: 4, Ahead: 3}})
	if e.queued == nil || e.queued.Position != 4 {
		t.Fatalf("first frame not kept: %+v", e.queued)
	}
	e.publish(StreamUpdate{Queued: &QueueStatus{Position: 0}})
	if e.queued == nil || e.queued.Position != 0 {
		t.Fatalf("latest state not kept: %+v", e.queued)
	}
	e.mu.Lock()
	snap := e.snapshotLocked()
	e.mu.Unlock()
	if snap.Queued == nil || snap.Queued.Position != 0 {
		t.Fatalf("snapshot: %+v", snap.Queued)
	}
}
