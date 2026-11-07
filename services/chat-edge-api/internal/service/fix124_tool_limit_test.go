package service

// FIX124 R3 — reaching the tool-round cap is never silent.
//
// Before: after the last allowed round's tools the pump opened one more
// stream, fell out of its loop, closed that stream UNREAD and emitted a bare
// Done. The row was saved `completed` holding only what the model had written
// before its first tool call. Now the round after the last tool round is the
// answer round: tool_choice "none", read like any other; when it writes
// nothing (or asks for tools anyway) the Done carries ToolLimit and
// stop_reason tool_use, which the handler puts on message_complete and the
// row.

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func sseToolRound(preamble, callID string) string {
	head := ""
	if preamble != "" {
		head = `data: {"choices":[{"delta":{"content":` + jsonString(preamble) + `},"finish_reason":null}]}` + "\n\n"
	}
	return head +
		`data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"` + callID + `","type":"function","function":{"name":"no_such_tool","arguments":"{}"}}]},"finish_reason":null}]}` + "\n\n" +
		`data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}` + "\n\n" + "data: [DONE]\n\n"
}

const sseEmptyStop = `data: {"choices":[{"delta":{},"finish_reason":"stop"}]}` + "\n\n" + "data: [DONE]\n\n"

// runToolTurn drives the pump against a gateway that answers request n
// (0-based) with script(n); it returns the updates and every request body.
func runToolTurn(t *testing.T, script func(n int) string) ([]StreamUpdate, []map[string]any) {
	t.Helper()
	gw := newScriptedGateway(t, func(n int, _ map[string]any) string { return script(n - 1) }) // the helper counts from 1
	s := NewMessageService(nil, nil, NewGatewayClient(gw.server.URL, "test-key", "", time.Minute, ""), nil, allowAllModels{}, 50)
	s.toolExec = &ToolExecutor{}
	ctx := context.Background()
	req := GatewayChatRequest{Model: "m", Messages: []GatewayMessage{{Role: "user", Content: "x"}}, Tools: []GatewayTool{askUserToolDef()}, ToolChoice: "auto"}
	callCtx := GatewayCallContext{TenantID: uuid.New(), UserID: uuid.New(), ConversationID: uuid.New()}
	stream, err := s.gateway.SendChatCompletionStream(ctx, callCtx, req)
	if err != nil {
		t.Fatalf("open stream: %v", err)
	}
	out := make(chan StreamUpdate, 256)
	go s.pumpStreamWithTools(streamPumpInput{initialStream: stream, initialReq: req, callCtx: callCtx, streamCtx: ctx, toolsAttached: true, out: out,
		toolExecCtx: ToolExecContext{TenantID: callCtx.TenantID, UserID: callCtx.UserID, ConversationID: callCtx.ConversationID, MessageID: uuid.New()}})
	var updates []StreamUpdate
	for u := range out {
		updates = append(updates, u)
	}
	gw.mu.Lock()
	defer gw.mu.Unlock()
	return updates, append([]map[string]any(nil), gw.bodies...)
}

func lastDone(t *testing.T, updates []StreamUpdate) StreamUpdate {
	t.Helper()
	for i := len(updates) - 1; i >= 0; i-- {
		if updates[i].Err != nil {
			t.Fatalf("turn ended in an error: %v", updates[i].Err)
		}
		if updates[i].Done {
			return updates[i]
		}
	}
	t.Fatal("no Done")
	return StreamUpdate{}
}

// Cap reached → one final call, tools not callable, and its answer is the
// turn's answer.
func TestToolLimit_AnswerRoundIsReadAndCannotCallTools(t *testing.T) {
	updates, bodies := runToolTurn(t, func(n int) string {
		if n < maxToolIterations {
			preamble := ""
			if n == 0 {
				preamble = "Let me work on it. "
			}
			return sseToolRound(preamble, "call_"+string(rune('a'+n)))
		}
		return sseText("Here is the result of all five steps.")
	})
	text, _, _, _ := summarise(updates)
	done := lastDone(t, updates)
	if !strings.Contains(text, "Here is the result of all five steps.") {
		t.Fatalf("the answer round's text must reach the client, got %q", text)
	}
	if done.ToolLimit != 0 || done.StopReason != StopReasonEndTurn {
		t.Fatalf("an answered turn ends like any turn: %+v", done)
	}
	if len(bodies) != maxToolIterations+1 {
		t.Fatalf("gateway calls = %d, want %d tool rounds + 1 answer round", len(bodies), maxToolIterations)
	}
	for i, b := range bodies {
		want := "auto"
		if i == maxToolIterations {
			want = toolChoiceNone
		}
		if b["tool_choice"] != want {
			t.Errorf("call %d: tool_choice = %v, want %q", i, b["tool_choice"], want)
		}
		if _, ok := b["tools"]; !ok {
			t.Errorf("call %d: tools must stay in the request (a history of tool calls without them is refused by some providers)", i)
		}
	}
	// the answer round sees the last round's tool results (then the notice)
	msgs := bodies[maxToolIterations]["messages"].([]any)
	if prev := msgs[len(msgs)-2].(map[string]any); prev["role"] != "tool" {
		t.Errorf("before the notice comes %v, want the last tool result", prev["role"])
	}
}

// The answer round writes nothing → the turn says it stopped at the cap.
func TestToolLimit_EmptyAnswerRoundIsNotAFinishedAnswer(t *testing.T) {
	updates, _ := runToolTurn(t, func(n int) string {
		if n < maxToolIterations {
			preamble := ""
			if n == 0 {
				preamble = "Let me work on it. "
			}
			return sseToolRound(preamble, "call_"+string(rune('a'+n)))
		}
		return sseEmptyStop
	})
	text, _, _, _ := summarise(updates)
	done := lastDone(t, updates)
	if text != "Let me work on it. " {
		t.Fatalf("text = %q", text)
	}
	if done.ToolLimit != maxToolIterations || done.StopReason != StopReasonToolUse {
		t.Fatalf("want ToolLimit=%d stop_reason=tool_use, got %+v", maxToolIterations, done)
	}
}

// The answer round asks for tools all the same → nothing more runs, same ending.
func TestToolLimit_AnswerRoundStillAskingForTools(t *testing.T) {
	updates, bodies := runToolTurn(t, func(n int) string { return sseToolRound("", "call_"+string(rune('a'+n))) })
	done := lastDone(t, updates)
	if done.ToolLimit != maxToolIterations || done.StopReason != StopReasonToolUse {
		t.Fatalf("got %+v", done)
	}
	if len(bodies) != maxToolIterations+1 {
		t.Fatalf("gateway calls = %d: the loop must stop after the answer round", len(bodies))
	}
}

// Measured live on deepseek-v4-flash: forbidden to call tools, the model wrote
// its next tool call out as TEXT. That is not an answer: none of it reaches
// the client, and the turn ends as the limit.
func TestToolLimit_ToolCallWrittenAsTextIsNotAnAnswer(t *testing.T) {
	for _, markup := range []string{
		"<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name=\"run_python\">\n<｜｜DSML｜｜ parameter name=\"code\">print(6**3)",
		"<tool_call>\n{\"name\": \"run_python\"}\n</tool_call>",
		"  <function_calls><invoke name=\"run_python\"></invoke></function_calls>",
	} {
		updates, _ := runToolTurn(t, func(n int) string {
			if n < maxToolIterations {
				preamble := ""
				if n == 0 {
					preamble = "Let me work on it. "
				}
				return sseToolRound(preamble, "call_"+string(rune('a'+n)))
			}
			return sseText(markup)
		})
		text, _, _, _ := summarise(updates)
		done := lastDone(t, updates)
		if text != "Let me work on it. " {
			t.Errorf("markup reached the client: %q", text)
		}
		if done.ToolLimit != maxToolIterations || done.StopReason != StopReasonToolUse {
			t.Errorf("want the tool-limit ending, got %+v", done)
		}
	}
}

// The answer round's request ends with the app's notice (never stored), and a
// SHORT real answer — shorter than the probe window — still arrives whole.
func TestToolLimit_AnswerRoundNoticeAndShortAnswer(t *testing.T) {
	updates, bodies := runToolTurn(t, func(n int) string {
		if n < maxToolIterations {
			return sseToolRound("", "call_"+string(rune('a'+n)))
		}
		return sseText("216")
	})
	text, _, _, _ := summarise(updates)
	if done := lastDone(t, updates); text != "216" || done.ToolLimit != 0 {
		t.Fatalf("text=%q done=%+v", text, done)
	}
	msgs := bodies[maxToolIterations]["messages"].([]any)
	last := msgs[len(msgs)-1].(map[string]any)
	if last["role"] != "user" || !strings.Contains(last["content"].(string), "tool-step limit") {
		t.Errorf("answer round must end with the notice, got %v", last)
	}
	for i := 0; i < maxToolIterations; i++ {
		for _, m := range bodies[i]["messages"].([]any) {
			if c, _ := m.(map[string]any)["content"].(string); strings.Contains(c, "tool-step limit") {
				t.Errorf("call %d carries the notice; only the answer round may", i)
			}
		}
	}
}

// A turn that needs fewer rounds than the cap is untouched.
func TestToolLimit_ShortTurnUnchanged(t *testing.T) {
	updates, bodies := runToolTurn(t, func(n int) string {
		if n == 0 {
			return sseToolRound("", "call_a")
		}
		return sseText("done")
	})
	done := lastDone(t, updates)
	if done.ToolLimit != 0 || len(bodies) != 2 || bodies[1]["tool_choice"] != "auto" {
		t.Fatalf("done=%+v calls=%d tool_choice=%v", done, len(bodies), bodies[1]["tool_choice"])
	}
}

func TestSetMaxToolIterations(t *testing.T) {
	was := maxToolIterations
	defer func() { maxToolIterations = was }()
	SetMaxToolIterations(0)
	if maxToolIterations != was {
		t.Fatal("a value below 1 must be ignored")
	}
	SetMaxToolIterations(12)
	if maxToolIterations != 12 {
		t.Fatalf("got %d", maxToolIterations)
	}
}
