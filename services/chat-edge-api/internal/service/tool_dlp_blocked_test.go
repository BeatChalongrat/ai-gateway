package service

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// spyWebSearcher records whether the external search was invoked.
type spyWebSearcher struct{ called int }

func (s *spyWebSearcher) Search(
	ctx context.Context, callCtx GatewayCallContext, req GatewaySearchRequest,
) (*GatewaySearchResponse, error) {
	s.called++
	return &GatewaySearchResponse{Query: req.Query, Provider: "spy"}, nil
}

type noopSourcesSaver struct{}

func (noopSourcesSaver) SaveSearchSources(
	ctx context.Context, messageID uuid.UUID, sources []GatewaySearchResult,
) error {
	return nil
}

func toolCall(name, args string) GatewayToolCall {
	return GatewayToolCall{
		ID:       "call_1",
		Type:     "function",
		Function: GatewayToolCallFunction{Name: name, Arguments: args},
	}
}

func blockedMarker(tool, reason string) string {
	b, _ := json.Marshal(map[string]any{
		"_dlp_blocked": true, "reason": reason, "tool": tool,
	})
	return string(b)
}

func assertSafeBlockedResult(t *testing.T, out, tool string) {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Fatalf("blocked result is not valid JSON: %v (%q)", err, out)
	}
	if m["error"] != "tool_call_blocked_by_dlp" {
		t.Fatalf("expected blocked error result, got %q", out)
	}
	if m["ok"] != false {
		t.Fatalf("expected ok=false, got %q", out)
	}
	if m["tool"] != tool {
		t.Fatalf("expected tool=%q, got %q", tool, out)
	}
	// Must not echo the marker key or any placeholder back.
	if strings.Contains(out, "_dlp_blocked") || strings.Contains(out, "<r") {
		t.Fatalf("blocked result leaked marker/placeholder: %q", out)
	}
}

func newExecutorWithSearch() (*ToolExecutor, *spyWebSearcher) {
	e := NewToolExecutor(nil)
	spy := &spyWebSearcher{}
	e.SetWebSearch(spy, noopSourcesSaver{})
	return e, spy
}

// 1 + 2 (web_search) + 7 (non-streaming path uses Execute)
func TestExecute_BlockedWebSearch_NotCalledExternally(t *testing.T) {
	e, spy := newExecutorWithSearch()
	out := e.Execute(context.Background(),
		toolCall("web_search", blockedMarker("web_search", "external_egress_blocked_masked_args")),
		ToolExecContext{})
	assertSafeBlockedResult(t, out, "web_search")
	if spy.called != 0 {
		t.Fatalf("external web search was called %d times; expected 0", spy.called)
	}
}

// 3 (fake send_email — plaintext-required, not registered)
func TestExecute_BlockedSendEmail_NotExecuted(t *testing.T) {
	e, _ := newExecutorWithSearch()
	out := e.Execute(context.Background(),
		toolCall("send_email", blockedMarker("send_email", "requires_plaintext_blocked_masked_args")),
		ToolExecContext{})
	assertSafeBlockedResult(t, out, "send_email")
}

// 3 (unknown tool)
func TestExecute_BlockedUnknownTool_NotExecuted(t *testing.T) {
	e, _ := newExecutorWithSearch()
	out := e.Execute(context.Background(),
		toolCall("mystery_tool", blockedMarker("mystery_tool", "unknown_tool_blocked_masked_args")),
		ToolExecContext{})
	assertSafeBlockedResult(t, out, "mystery_tool")
}

// 5 (malformed blocked marker JSON → safe failure, no external call)
func TestExecute_MalformedBlockedMarker_NoExternalCall(t *testing.T) {
	e, spy := newExecutorWithSearch()
	// Contains the marker key but is not valid JSON.
	out := e.Execute(context.Background(),
		toolCall("web_search", `{"_dlp_blocked": true,, "reason":`),
		ToolExecContext{})
	assertSafeBlockedResult(t, out, "web_search")
	if spy.called != 0 {
		t.Fatalf("external search called on malformed marker; expected 0, got %d", spy.called)
	}
}

// 4 + 9 (clean args → executes normally)
func TestExecute_CleanWebSearch_Executes(t *testing.T) {
	e, spy := newExecutorWithSearch()
	out := e.Execute(context.Background(),
		toolCall("web_search", `{"query":"weather in Bangkok"}`),
		ToolExecContext{})
	if spy.called != 1 {
		t.Fatalf("clean web search should execute once; got %d", spy.called)
	}
	if strings.Contains(out, "tool_call_blocked_by_dlp") {
		t.Fatalf("clean call should not be blocked: %q", out)
	}
}

// 6 (streaming-accumulated tool call with blocked marker → no execution).
// Reuses the real streaming accumulator so the same Execute path the
// streaming loop takes is exercised.
func TestExecute_StreamingAccumulatedBlockedMarker_NotExecuted(t *testing.T) {
	e, spy := newExecutorWithSearch()
	marker := blockedMarker("web_search", "external_egress_blocked_masked_args")
	// Simulate the provider streaming the marker in two fragments.
	half := len(marker) / 2
	acc := accumulateToolCallDeltas(nil, []StreamToolCallDelta{
		{Index: 0, ID: "call_1", FunctionName: "web_search", FunctionArgsFrag: marker[:half]},
	})
	acc = accumulateToolCallDeltas(acc, []StreamToolCallDelta{
		{Index: 0, FunctionArgsFrag: marker[half:]},
	})
	if len(acc) != 1 {
		t.Fatalf("expected 1 accumulated tool call, got %d", len(acc))
	}
	out := e.Execute(context.Background(), acc[0], ToolExecContext{})
	assertSafeBlockedResult(t, out, "web_search")
	if spy.called != 0 {
		t.Fatalf("external search called for streamed blocked marker; expected 0, got %d", spy.called)
	}
}

// 8 (no raw args / placeholder in logs)
func TestExecute_BlockedMarker_NoRawArgsInLogs(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	defer slog.SetDefault(prev)

	e, _ := newExecutorWithSearch()
	// Inject a sentinel placeholder into a marker field; our log must
	// never echo arguments, so the sentinel must not appear in logs.
	args := `{"_dlp_blocked": true, "reason": "external_egress_blocked_masked_args", "leftover": "<r9_SENTINEL>"}`
	_ = e.Execute(context.Background(), toolCall("web_search", args), ToolExecContext{})

	logged := buf.String()
	if !strings.Contains(logged, "tool_call_blocked_by_dlp") {
		t.Fatalf("expected sanitized block log; got %q", logged)
	}
	if strings.Contains(logged, "<r9_SENTINEL>") {
		t.Fatalf("raw argument content leaked into logs: %q", logged)
	}
}

// Unit-level coverage of the detector helper.
func TestDlpBlockedMarker(t *testing.T) {
	cases := []struct {
		name    string
		args    string
		blocked bool
	}{
		{"valid marker", `{"_dlp_blocked": true, "reason": "x"}`, true},
		{"false marker", `{"_dlp_blocked": false}`, false},
		{"clean args", `{"query": "weather"}`, false},
		{"empty", ``, false},
		{"malformed with key", `{"_dlp_blocked": true,,}`, true},
	}
	for _, c := range cases {
		got, _ := dlpBlockedMarker(c.args)
		if got != c.blocked {
			t.Errorf("%s: dlpBlockedMarker=%v want %v", c.name, got, c.blocked)
		}
	}
}
