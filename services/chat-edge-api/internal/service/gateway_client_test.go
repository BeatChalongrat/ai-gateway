package service

import (
	"bufio"
	"errors"
	"io"
	"strings"
	"testing"
)

// newStreamReader builds a StreamReader over a fixed SSE body, mirroring
// the wiring in SendChatCompletionStream.
func newStreamReader(sse string) *StreamReader {
	rc := io.NopCloser(strings.NewReader(sse))
	return &StreamReader{body: rc, scanner: bufio.NewScanner(rc)}
}

// TestParseStreamChunkErrorFrame — a Phase-C ``error`` frame must parse
// into a StreamEvent carrying the error (never a silent empty content
// chunk that would swallow the failure).
func TestParseStreamChunkErrorFrame(t *testing.T) {
	ev, err := parseStreamChunk(
		`{"object":"error","error":{"code":"provider_auth_failed","message":"bad key"}}`,
	)
	if err != nil {
		t.Fatalf("unexpected parse error: %v", err)
	}
	if ev.Error == nil {
		t.Fatal("expected ev.Error to be set on an error frame")
	}
	if ev.Error.Code != "provider_auth_failed" {
		t.Errorf("code = %q, want provider_auth_failed", ev.Error.Code)
	}
	if ev.Error.Message != "bad key" {
		t.Errorf("message = %q, want 'bad key'", ev.Error.Message)
	}
	if ev.Delta != "" {
		t.Errorf("error frame must not carry a content delta, got %q", ev.Delta)
	}
}

// TestParseStreamChunkContentStillWorks — a normal content chunk is
// unaffected by the new error sniff.
func TestParseStreamChunkContentStillWorks(t *testing.T) {
	ev, err := parseStreamChunk(
		`{"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}`,
	)
	if err != nil {
		t.Fatalf("unexpected parse error: %v", err)
	}
	if ev.Error != nil {
		t.Fatalf("content chunk must not be read as an error: %+v", ev.Error)
	}
	if ev.Delta != "hi" {
		t.Errorf("delta = %q, want 'hi'", ev.Delta)
	}
}

// TestNextSurfacesErrorFrame — Next() converts an error frame into a
// returned *GatewayStreamError so the pump's error path fires (which the
// handler renders as an ``event: error`` SSE to the web client).
func TestNextSurfacesErrorFrame(t *testing.T) {
	sse := "data: {\"object\":\"error\",\"error\":{\"code\":\"provider_auth_failed\",\"message\":\"rejected\"}}\n" +
		"data: [DONE]\n"
	r := newStreamReader(sse)

	_, err := r.Next()
	if err == nil {
		t.Fatal("expected an error from Next() on an error frame")
	}
	var se *GatewayStreamError
	if !errors.As(err, &se) {
		t.Fatalf("error = %T, want *GatewayStreamError", err)
	}
	if se.Code != "provider_auth_failed" {
		t.Errorf("code = %q, want provider_auth_failed", se.Code)
	}
	if se.Message != "rejected" {
		t.Errorf("message = %q, want 'rejected'", se.Message)
	}
}

// TestNextNormalThenDone — a healthy stream yields its delta then the
// [DONE] sentinel, with no spurious error.
func TestNextNormalThenDone(t *testing.T) {
	sse := "data: {\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}\n" +
		"data: [DONE]\n"
	r := newStreamReader(sse)

	ev, err := r.Next()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ev.Delta != "hello" {
		t.Errorf("delta = %q, want 'hello'", ev.Delta)
	}

	ev, err = r.Next()
	if err != nil {
		t.Fatalf("unexpected error on DONE: %v", err)
	}
	if !ev.Done {
		t.Error("expected Done=true on [DONE] sentinel")
	}
}
