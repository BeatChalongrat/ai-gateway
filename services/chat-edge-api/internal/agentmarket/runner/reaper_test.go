package runner

import (
	"testing"
	"time"
)

// AM1c — which sandbox containers the sweep may remove.
func TestOrphans(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	grace := time.Minute
	me := Identity{Runner: "agent-runner-abc", Instance: "i-now"}
	box := func(name, runner, instance, call string, deadline time.Time) Box {
		return Box{Name: name, Owner: OwnerRunner, Runner: runner, Instance: instance, Call: call, Deadline: deadline}
	}
	ahead := now.Add(4 * time.Minute)
	boxes := []Box{
		box("mine-in-flight", me.Runner, me.Instance, "c-live", ahead),
		box("mine-call-returned", me.Runner, me.Instance, "c-done", ahead),
		box("previous-process", me.Runner, "i-before", "c-old", ahead),
		box("other-runner-live", "agent-runner-xyz", "i-xyz", "c-xyz", ahead),
		box("other-runner-in-grace", "agent-runner-xyz", "i-xyz", "c-xyz2", now.Add(-30*time.Second)),
		box("other-runner-past-deadline", "agent-runner-gone", "i-gone", "c-gone", now.Add(-2*time.Minute)),
		box("no-deadline-other-runner", "agent-runner-xyz", "i-xyz", "c-nd", time.Time{}),
		{Name: "chat-sandbox", Deadline: now.Add(-time.Hour)},                                        // no owner label
		{Name: "someone-else", Owner: "chat-edge", Runner: me.Runner, Deadline: now.Add(-time.Hour)}, // another owner
		// In flight here wins over every other rule, even past a deadline.
		box("in-flight-past-deadline", "agent-runner-gone", "i-gone", "c-live2", now.Add(-time.Hour)),
	}
	inflight := map[string]bool{"c-live": true, "c-live2": true}

	got := map[string]Reap{}
	for _, r := range Orphans(boxes, me, inflight, now, grace) {
		got[r.Box.Name] = r
	}
	want := map[string]bool{ // name → owner gone
		"mine-call-returned":         false,
		"previous-process":           true,
		"other-runner-past-deadline": true,
	}
	for name, ownerGone := range want {
		r, ok := got[name]
		if !ok {
			t.Errorf("%s: kept, want reaped", name)
			continue
		}
		if r.OwnerGone != ownerGone {
			t.Errorf("%s: OwnerGone=%v, want %v", name, r.OwnerGone, ownerGone)
		}
	}
	for name, r := range got {
		if _, ok := want[name]; !ok {
			t.Errorf("%s: reaped (%s), must be kept", name, r.Reason)
		}
	}
}

// With no identity (a sweep that is not a runner's) only deadlines count.
func TestOrphansWithoutIdentityOnlyDeadlines(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	boxes := []Box{
		{Name: "a", Owner: OwnerRunner, Runner: "r", Instance: "i", Deadline: now.Add(time.Minute)},
		{Name: "b", Owner: OwnerRunner, Runner: "r", Instance: "i", Deadline: now.Add(-2 * time.Minute)},
	}
	got := Orphans(boxes, Identity{}, nil, now, time.Minute)
	if len(got) != 1 || got[0].Box.Name != "b" {
		t.Fatalf("got %+v", got)
	}
}
