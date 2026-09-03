package service

// Generic tool_activity frames — tools WITHOUT a dedicated progress
// event get a started/finished pair bracketing execution; tools WITH a
// dedicated event (image/video/search/fetch/python) are suppressed so
// the client never double-renders one call. Broker snapshot replays the
// latest frame per call id so a mid-stream reconnect restores the
// indicator.
//
// The execution tests use the DLP-blocked-marker arguments so
// ToolExecutor.Execute returns via its safe early path without touching
// any wired service — the bracket logic under test lives in
// executeToolCalls, not in the tools themselves.

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

const dlpMarkerArgs = `{"_dlp_blocked": true, "reason": "test"}`

func collectActivity(ec *ToolExecContext) *[]ToolActivityUpdate {
	var got []ToolActivityUpdate
	ec.Emit = func(u StreamUpdate) {
		if u.ToolActivity != nil {
			got = append(got, *u.ToolActivity)
		}
	}
	return &got
}

func call(id, name, args string) GatewayToolCall {
	return GatewayToolCall{
		ID:   id,
		Type: "function",
		Function: GatewayToolCallFunction{
			Name:      name,
			Arguments: args,
		},
	}
}

func TestExecuteToolCalls_VisibleToolEmitsStartedThenFinished(t *testing.T) {
	s := &MessageService{toolExec: &ToolExecutor{}}
	ec := ToolExecContext{}
	frames := collectActivity(&ec)

	// User-visible actions (past-chat search, canvas edit) and unknown
	// tool names (the future-MCP shape) all emit the generic pair.
	_, results := s.executeToolCalls(context.Background(), []GatewayToolCall{
		call("call_1", "search_past_conversations", dlpMarkerArgs),
		call("call_2", "update_canvas_artifact", dlpMarkerArgs),
		call("call_3", "some_future_mcp_tool", "{}"),
	}, ec)

	if len(results) != 3 {
		t.Fatalf("expected 3 tool results, got %d", len(results))
	}
	want := []ToolActivityUpdate{
		{ID: "call_1", Tool: "search_past_conversations", Phase: "started"},
		{ID: "call_1", Tool: "search_past_conversations", Phase: "finished"},
		{ID: "call_2", Tool: "update_canvas_artifact", Phase: "started"},
		{ID: "call_2", Tool: "update_canvas_artifact", Phase: "finished"},
		{ID: "call_3", Tool: "some_future_mcp_tool", Phase: "started"},
		{ID: "call_3", Tool: "some_future_mcp_tool", Phase: "finished"},
	}
	if len(*frames) != len(want) {
		t.Fatalf("expected %d frames, got %d: %+v", len(want), len(*frames), *frames)
	}
	for i, w := range want {
		g := (*frames)[i]
		if g.ID != w.ID || g.Tool != w.Tool || g.Phase != w.Phase {
			t.Errorf("frame[%d] = %+v, want %+v", i, g, w)
		}
		// FIX50 P1-D — the finished frame carries the tool's work
		// (args + result preview + success), the started frame stays
		// thin. Without the preview a tool-produced answer reads as a
		// bare number (FIX48 §2.4).
		if g.Phase == "finished" {
			if g.ResultPreview == "" {
				t.Errorf("frame[%d] finished without resultPreview", i)
			}
			if g.Success == nil {
				t.Errorf("frame[%d] finished without success", i)
			}
		} else if g.ArgsSummary != "" || g.ResultPreview != "" || g.Success != nil {
			t.Errorf("frame[%d] started frame must stay thin: %+v", i, g)
		}
	}
}

func TestExecuteToolCalls_SilentByDesignToolsEmitNothing(t *testing.T) {
	s := &MessageService{toolExec: &ToolExecutor{}}
	ec := ToolExecContext{}
	frames := collectActivity(&ec)

	// Memory/state bookkeeping stays silent (big-vendor behavior): the
	// model's reply text is the acknowledgement, a badge double-reports.
	calls := []GatewayToolCall{
		call("m1", "save_memory", dlpMarkerArgs),
		call("m2", "list_memories", dlpMarkerArgs),
		call("m3", "update_conversation_state", dlpMarkerArgs),
	}
	_, results := s.executeToolCalls(context.Background(), calls, ec)

	if len(results) != len(calls) {
		t.Fatalf("expected %d tool results, got %d", len(calls), len(results))
	}
	if len(*frames) != 0 {
		t.Errorf("expected no frames for silent-by-design tools, got %+v", *frames)
	}
}

func TestExecuteToolCalls_DedicatedEventToolEmitsNoGeneric(t *testing.T) {
	s := &MessageService{toolExec: &ToolExecutor{}}
	ec := ToolExecContext{}
	frames := collectActivity(&ec)

	// Every tool that already has its own SSE event must stay silent on
	// the generic channel — otherwise the client renders the dedicated
	// placeholder AND a generic activity row for the same call.
	calls := []GatewayToolCall{
		call("c1", "generate_image", dlpMarkerArgs),
		call("c2", "generate_video", dlpMarkerArgs),
		call("c3", "web_search", dlpMarkerArgs),
		call("c4", "web_fetch", dlpMarkerArgs),
		call("c5", "run_python", dlpMarkerArgs),
	}
	_, results := s.executeToolCalls(context.Background(), calls, ec)

	if len(results) != len(calls) {
		t.Fatalf("expected %d tool results, got %d", len(calls), len(results))
	}
	if len(*frames) != 0 {
		t.Errorf("expected no generic frames for dedicated-event tools, got %+v", *frames)
	}
}

func TestExecuteToolCalls_NilEmitterIsSafe(t *testing.T) {
	s := &MessageService{toolExec: &ToolExecutor{}}
	// Non-streaming path: Emit is nil by design — must not panic.
	_, results := s.executeToolCalls(context.Background(), []GatewayToolCall{
		call("call_1", "search_past_conversations", dlpMarkerArgs),
	}, ToolExecContext{})
	if len(results) != 1 {
		t.Fatalf("expected 1 tool result, got %d", len(results))
	}
}

func TestBroker_SnapshotReplaysLatestToolActivityPerCall(t *testing.T) {
	b := NewStreamBroker()
	msgID := uuid.New()
	in := make(chan StreamUpdate, 8)
	out := b.Tee(msgID, in)

	in <- StreamUpdate{ToolActivity: &ToolActivityUpdate{ID: "a", Tool: "save_memory", Phase: "started"}}
	in <- StreamUpdate{ToolActivity: &ToolActivityUpdate{ID: "b", Tool: "update_canvas_artifact", Phase: "started"}}
	in <- StreamUpdate{ToolActivity: &ToolActivityUpdate{ID: "a", Tool: "save_memory", Phase: "finished"}}
	for i := 0; i < 3; i++ {
		<-out
	}

	snap, _, cancel, _, ok := b.Subscribe(msgID)
	if !ok {
		t.Fatal("expected a live entry")
	}
	defer cancel()

	if len(snap.ToolActivities) != 2 {
		t.Fatalf("expected 2 activities, got %d: %+v", len(snap.ToolActivities), snap.ToolActivities)
	}
	if snap.ToolActivities[0].ID != "a" || snap.ToolActivities[1].ID != "b" {
		t.Errorf("activities must replay in first-seen order, got %q then %q",
			snap.ToolActivities[0].ID, snap.ToolActivities[1].ID)
	}
	if snap.ToolActivities[0].Phase != "finished" {
		t.Errorf("call a must replay its LATEST frame (finished), got %q", snap.ToolActivities[0].Phase)
	}
	if snap.ToolActivities[1].Phase != "started" {
		t.Errorf("call b is still in flight, got %q", snap.ToolActivities[1].Phase)
	}

	close(in)
}
