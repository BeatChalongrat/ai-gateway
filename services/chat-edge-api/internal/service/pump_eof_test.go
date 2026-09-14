package service

// FIX118 Part B — an upstream stream that closes without saying it is done
// is NOT a Done.
//
// Before: the pump turned a bare EOF into a synthetic Done, so a cut stream
// terminated message_complete and persisted `completed` with whatever text
// had arrived. After: EOF with no [DONE], no error frame and no finish
// reason is reported as ErrStreamEOFWithoutTerminal (the handler classifies
// it as stream_interrupted / eof_without_terminal). EOF after an error
// frame — the gateway's own error path never sends [DONE] — still ends on
// the synthetic Done so the handler's terminal carries the provider's code.

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
)

func pumpOnce(t *testing.T, body string) []StreamUpdate {
	t.Helper()
	gw := newScriptedGateway(t, func(n int, _ map[string]any) string { return body })
	s := NewMessageService(nil, nil, NewGatewayClient(gw.server.URL, "test-key", "", time.Minute, ""), nil, allowAllModels{}, 50)
	ctx := context.Background()
	req := GatewayChatRequest{Model: "m", Messages: []GatewayMessage{{Role: "user", Content: "x"}}}
	callCtx := GatewayCallContext{TenantID: uuid.New(), UserID: uuid.New(), ConversationID: uuid.New()}
	stream, err := s.gateway.SendChatCompletionStream(ctx, callCtx, req)
	if err != nil {
		t.Fatalf("open stream: %v", err)
	}
	out := make(chan StreamUpdate, 64)
	go s.pumpStreamWithTools(streamPumpInput{initialStream: stream, initialReq: req, callCtx: callCtx, streamCtx: ctx, out: out,
		toolExecCtx: ToolExecContext{TenantID: callCtx.TenantID, UserID: callCtx.UserID, ConversationID: callCtx.ConversationID, MessageID: uuid.New()}})
	var updates []StreamUpdate
	for u := range out {
		updates = append(updates, u)
	}
	return updates
}

func summarise(updates []StreamUpdate) (text string, done bool, err error, provider string) {
	for _, u := range updates {
		text += u.Delta
		if u.Done {
			done = true
		}
		if u.Err != nil {
			err = u.Err
		}
		if u.ProviderError != nil {
			provider = u.ProviderError.Code
		}
	}
	return
}

func TestPump_EOFWithoutTerminalIsAnError(t *testing.T) {
	// Two deltas, then the socket closes: no finish_reason, no [DONE].
	body := `data: {"choices":[{"delta":{"content":"half an "},"finish_reason":null}]}` + "\n\n" +
		`data: {"choices":[{"delta":{"content":"answer"},"finish_reason":null}]}` + "\n\n"
	text, done, err, _ := summarise(pumpOnce(t, body))
	if text != "half an answer" {
		t.Fatalf("the text that arrived must still be forwarded, got %q", text)
	}
	if done {
		t.Fatal("a stream that never said it was done must not produce a Done")
	}
	if !errors.Is(err, ErrStreamEOFWithoutTerminal) {
		t.Fatalf("want ErrStreamEOFWithoutTerminal, got %v", err)
	}
}

func TestPump_EOFAfterErrorFrameKeepsTheProviderCode(t *testing.T) {
	body := `data: {"choices":[{"delta":{"content":"a bit"},"finish_reason":null}]}` + "\n\n" +
		`data: {"error":{"code":"provider_rate_limited","message":"slow down","retryable":true}}` + "\n\n"
	_, done, err, provider := summarise(pumpOnce(t, body))
	if provider != "provider_rate_limited" {
		t.Fatalf("the provider error frame must be forwarded, got %q", provider)
	}
	if err != nil {
		t.Fatalf("EOF after an error frame is the gateway finishing its error path, not a second failure: %v", err)
	}
	if !done {
		t.Fatal("the synthetic Done still fires so the handler's terminal (error, with the provider's code) goes out")
	}
}

func TestPump_EOFAfterFinishReasonIsStillDone(t *testing.T) {
	// finish_reason=stop arrived, only the [DONE] line is missing.
	body := `data: {"choices":[{"delta":{"content":"whole answer"},"finish_reason":null}]}` + "\n\n" +
		`data: {"choices":[{"delta":{},"finish_reason":"stop"}]}` + "\n\n"
	text, done, err, _ := summarise(pumpOnce(t, body))
	if text != "whole answer" || !done || err != nil {
		t.Fatalf("a finished stream missing only [DONE] is complete: text=%q done=%v err=%v", text, done, err)
	}
}

func TestPump_DoneLineIsStillDone(t *testing.T) {
	text, done, err, _ := summarise(pumpOnce(t, sseText("fine")))
	if text != "fine" || !done || err != nil {
		t.Fatalf("the normal path is unchanged: text=%q done=%v err=%v", text, done, err)
	}
}
