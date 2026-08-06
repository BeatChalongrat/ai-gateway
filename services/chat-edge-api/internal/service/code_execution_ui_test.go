package service

// Phase 8 Task 2 — the run_python progress frames that drive the
// code+output card, and their replay after a mid-stream reconnect.
//
// The invariant that matters most: a card must never be left spinning.
// Every exit path of execRunPython settles the card, including the ones
// that return before the sandbox is reached.

import (
	"context"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

// collectExec records the CodeExecution frames a tool emitted.
func collectExec(ec *ToolExecContext) *[]CodeExecutionUpdate {
	var got []CodeExecutionUpdate
	ec.Emit = func(u StreamUpdate) {
		if u.CodeExecution != nil {
			got = append(got, *u.CodeExecution)
		}
	}
	return &got
}

func TestExecRunPython_EmitsRunningThenCompleted(t *testing.T) {
	runner := &fakeSandboxRunner{res: &sandbox.Result{
		Stdout: []byte("42\n"),
		Exit:   0,
	}}
	e := newCodeExecutor(runner, &fakeCodeResolver{}, &fakeGeneratedFileSaver{})
	ec := baseExecCtx()
	frames := collectExec(&ec)

	e.execRunPython(context.Background(), "call_a", `{"code":"print(6*7)"}`, ec)

	if len(*frames) != 2 {
		t.Fatalf("expected running+terminal frames, got %d: %+v", len(*frames), *frames)
	}
	running, done := (*frames)[0], (*frames)[1]

	if running.Status != "running" {
		t.Errorf("first frame status = %q, want running", running.Status)
	}
	if running.Code != "print(6*7)" {
		t.Errorf("the running frame must carry the code the model wrote, got %q", running.Code)
	}
	if running.ID != "call_a" || done.ID != "call_a" {
		t.Errorf("both frames must share the tool_call id, got %q / %q", running.ID, done.ID)
	}
	if done.Status != "completed" {
		t.Errorf("terminal status = %q, want completed", done.Status)
	}
	if done.Stdout != "42\n" {
		t.Errorf("stdout = %q, want 42", done.Stdout)
	}
	if done.Code == "" {
		t.Error("the terminal frame must repeat the code (a reattach may only see this one)")
	}
}

func TestExecRunPython_NonZeroExitIsAnErrorCard(t *testing.T) {
	runner := &fakeSandboxRunner{res: &sandbox.Result{
		Stderr: []byte("ZeroDivisionError"),
		Exit:   1,
	}}
	e := newCodeExecutor(runner, &fakeCodeResolver{}, &fakeGeneratedFileSaver{})
	ec := baseExecCtx()
	frames := collectExec(&ec)

	e.execRunPython(context.Background(), "call_a", `{"code":"1/0"}`, ec)

	last := (*frames)[len(*frames)-1]
	if last.Status != "error" {
		t.Fatalf("status = %q, want error", last.Status)
	}
	if last.ExitCode != 1 {
		t.Errorf("exit code = %d, want 1", last.ExitCode)
	}
	if !strings.Contains(last.Stderr, "ZeroDivisionError") {
		t.Errorf("stderr must reach the card, got %q", last.Stderr)
	}
}

func TestExecRunPython_TimeoutIsAnErrorCard(t *testing.T) {
	runner := &fakeSandboxRunner{res: &sandbox.Result{TimedOut: true, Exit: 124}}
	e := newCodeExecutor(runner, &fakeCodeResolver{}, &fakeGeneratedFileSaver{})
	ec := baseExecCtx()
	frames := collectExec(&ec)

	e.execRunPython(context.Background(), "call_a", `{"code":"while True: pass"}`, ec)

	last := (*frames)[len(*frames)-1]
	if !last.TimedOut || last.Status != "error" {
		t.Fatalf("a timed-out run must settle as an error card, got %+v", last)
	}
}

// A card stuck on "running" is the worst failure mode, so every early
// return must still settle it.
func TestExecRunPython_EarlyFailuresStillSettleTheCard(t *testing.T) {
	t.Run("denied file", func(t *testing.T) {
		e := newCodeExecutor(
			&fakeSandboxRunner{res: &sandbox.Result{}},
			&fakeCodeResolver{err: ErrFileNotFound},
			&fakeGeneratedFileSaver{},
		)
		ec := baseExecCtx()
		frames := collectExec(&ec)

		e.execRunPython(context.Background(), "call_a",
			`{"code":"print(1)","files":["`+uuid.NewString()+`"]}`, ec)

		last := (*frames)[len(*frames)-1]
		if last.Status != "error" {
			t.Fatalf("status = %q, want error", last.Status)
		}
		if last.Stderr == "" {
			t.Error("the card should explain why it failed")
		}
	})

	t.Run("sandbox unavailable", func(t *testing.T) {
		e := newCodeExecutor(
			&fakeSandboxRunner{err: sandbox.ErrUnavailable},
			&fakeCodeResolver{},
			&fakeGeneratedFileSaver{},
		)
		ec := baseExecCtx()
		frames := collectExec(&ec)

		e.execRunPython(context.Background(), "call_a", `{"code":"print(1)"}`, ec)

		if len(*frames) < 2 {
			t.Fatalf("expected the card to be settled, got %+v", *frames)
		}
		if (*frames)[len(*frames)-1].Status != "error" {
			t.Fatalf("status = %q, want error", (*frames)[len(*frames)-1].Status)
		}
	})
}

// The tool must work exactly as before when nothing is listening (the
// non-streaming path passes no emitter).
func TestExecRunPython_NilEmitterIsSafe(t *testing.T) {
	runner := &fakeSandboxRunner{res: &sandbox.Result{Stdout: []byte("ok"), Exit: 0}}
	e := newCodeExecutor(runner, &fakeCodeResolver{}, &fakeGeneratedFileSaver{})

	out := e.execRunPython(context.Background(), "call_a", `{"code":"print(1)"}`, baseExecCtx())
	if !strings.Contains(out, "ok") {
		t.Fatalf("tool result should be unchanged without an emitter, got %q", out)
	}
}

func TestTruncateForDisplay(t *testing.T) {
	short := "สวัสดี"
	if got, clipped := truncateForDisplay(short); got != short || clipped {
		t.Fatalf("short output must pass through untouched, got %q clipped=%v", got, clipped)
	}

	// Thai is 3 bytes/rune, so a byte-cut lands mid-rune unless the
	// helper backs off to a boundary.
	long := strings.Repeat("ก", maxDisplayOutputBytes)
	got, clipped := truncateForDisplay(long)
	if !clipped {
		t.Fatal("oversized output must report that it was clipped")
	}
	if len(got) > maxDisplayOutputBytes {
		t.Fatalf("clipped to %d bytes, want <= %d", len(got), maxDisplayOutputBytes)
	}
	if !utf8.ValidString(got) {
		t.Fatal("clipping must not split a multi-byte character")
	}
}

// ─── broker replay (Phase 5 interop) ────────────────────────────────

func TestBroker_SnapshotReplaysLatestFramePerRun(t *testing.T) {
	b := NewStreamBroker()
	msgID := uuid.New()
	in := make(chan StreamUpdate, 8)
	out := b.Tee(msgID, in)

	in <- StreamUpdate{CodeExecution: &CodeExecutionUpdate{ID: "a", Status: "running", Code: "1"}}
	in <- StreamUpdate{CodeExecution: &CodeExecutionUpdate{ID: "b", Status: "running", Code: "2"}}
	in <- StreamUpdate{CodeExecution: &CodeExecutionUpdate{
		ID: "a", Status: "completed", Code: "1", Stdout: "one",
	}}
	// Drain so the tee has published all three.
	for i := 0; i < 3; i++ {
		<-out
	}

	snap, _, cancel, _, ok := b.Subscribe(msgID)
	if !ok {
		t.Fatal("expected a live entry")
	}
	defer cancel()

	if len(snap.CodeExecutions) != 2 {
		t.Fatalf("expected 2 cards, got %d: %+v", len(snap.CodeExecutions), snap.CodeExecutions)
	}
	if snap.CodeExecutions[0].ID != "a" || snap.CodeExecutions[1].ID != "b" {
		t.Errorf("cards must replay in first-seen order, got %q then %q",
			snap.CodeExecutions[0].ID, snap.CodeExecutions[1].ID)
	}
	if snap.CodeExecutions[0].Status != "completed" || snap.CodeExecutions[0].Stdout != "one" {
		t.Errorf("run a must replay its LATEST frame, got %+v", snap.CodeExecutions[0])
	}
	if snap.CodeExecutions[1].Status != "running" {
		t.Errorf("run b is still in flight, got %q", snap.CodeExecutions[1].Status)
	}

	close(in)
}

func TestBroker_NoCodeRunsWhenNoneEmitted(t *testing.T) {
	b := NewStreamBroker()
	msgID := uuid.New()
	in := make(chan StreamUpdate, 4)
	out := b.Tee(msgID, in)
	in <- StreamUpdate{Delta: "hello"}
	<-out

	snap, _, cancel, _, ok := b.Subscribe(msgID)
	if !ok {
		t.Fatal("expected a live entry")
	}
	defer cancel()
	if snap.CodeExecutions != nil {
		t.Fatalf("a turn without run_python must carry no cards, got %+v", snap.CodeExecutions)
	}
	close(in)
}
