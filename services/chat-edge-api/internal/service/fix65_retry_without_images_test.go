package service

// FIX65 — one failed image turn must not poison the chat.
//
// Measured before the fix (both clients): an admin turns a model's vision
// switch off mid-session; the gateway refuses the image turn
// (model_no_vision_support), the image stays in the history, and every
// later turn on the model is refused too. These pin the repair: image
// blocks are stripped into a note, the client is told, the turn is
// resent once — and only when the fresh catalog agrees the model is blind.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func fix65ImageReq() GatewayChatRequest {
	return GatewayChatRequest{Model: "qwen3.8-27b-fp8", Messages: []GatewayMessage{
		{Role: "system", Content: "sys"},
		{Role: "user", Content: []GatewayContentBlock{
			{Type: "image_url", ImageURL: &GatewayImageURL{URL: "data:image/png;base64,AAAA"}},
			{Type: "text", Text: "what colour?"},
		}},
		{Role: "assistant", Content: "I cannot see it."},
		{Role: "user", Content: "say OK"},
	}}
}

func TestStripImageBlocks_ReplacesImagesWithANoteAndKeepsText(t *testing.T) {
	req := fix65ImageReq()
	if got := stripImageBlocks(req.Messages); got != 1 {
		t.Fatalf("removed=%d, want 1", got)
	}
	text, ok := req.Messages[1].Content.(string)
	if !ok {
		t.Fatalf("image message must become plain text, got %T", req.Messages[1].Content)
	}
	if !strings.Contains(text, imageNotSentNote) || !strings.Contains(text, "what colour?") {
		t.Fatalf("note and original text must both survive: %q", text)
	}
	if req.Messages[3].Content != "say OK" || req.Messages[0].Content != "sys" {
		t.Fatal("messages without images must be untouched")
	}
	if stripImageBlocks(req.Messages) != 0 {
		t.Fatal("a second pass has nothing to remove")
	}
}

func TestRetryWithoutImages_OnlyThatErrorAndOnlyBeforeAnythingWasForwarded(t *testing.T) {
	calls := 0
	s := &MessageService{invalidateCatalog: func() { calls++ }, nonVisionModel: func(context.Context, string) bool { return true }}
	healed := false
	req := fix65ImageReq()
	if s.retryWithoutImages(fix64Pump(false), &req, &StreamProviderError{Code: "model_no_tool_support"}, &healed, 0, 0, "") != nil {
		t.Fatal("a different error is not repaired here")
	}
	for _, c := range []struct {
		iter, acc int
		fin       string
	}{{1, 0, ""}, {0, 1, ""}, {0, 0, "stop"}} {
		if s.retryWithoutImages(fix64Pump(false), &req, &StreamProviderError{Code: "model_no_vision_support"}, &healed, c.iter, c.acc, c.fin) != nil {
			t.Fatalf("case %+v: must not retry mid-turn", c)
		}
	}
	if calls != 0 || healed {
		t.Fatal("nothing should have been invalidated")
	}
}

func TestRetryWithoutImages_CatalogStillSaysVision_ErrorPassesThrough(t *testing.T) {
	s := &MessageService{invalidateCatalog: func() {}, nonVisionModel: func(context.Context, string) bool { return false }}
	healed := false
	req := fix65ImageReq()
	if s.retryWithoutImages(fix64Pump(false), &req, &StreamProviderError{Code: "model_no_vision_support"}, &healed, 0, 0, "") != nil {
		t.Fatal("when the fresh catalog still says vision the error must reach the client")
	}
	if !healed {
		t.Fatal("the repair runs at most once per pump")
	}
	if _, ok := req.Messages[1].Content.([]GatewayContentBlock); !ok {
		t.Fatal("the request must keep its images when no retry happens")
	}
}

func TestRetryWithoutImages_ResendsWithoutImagesAndTellsTheClient(t *testing.T) {
	var bodies []map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		bodies = append(bodies, body)
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"OK\"},\"finish_reason\":null}]}\n\n"))
		_, _ = w.Write([]byte("data: [DONE]\n\n"))
	}))
	defer srv.Close()

	out := make(chan StreamUpdate, 4)
	pump := fix64Pump(false)
	pump.out = out
	s := &MessageService{
		gateway:           NewGatewayClient(srv.URL, "test-key", "", time.Minute, ""),
		invalidateCatalog: func() {},
		nonVisionModel:    func(context.Context, string) bool { return true },
	}
	healed := false
	req := fix65ImageReq()
	stream := s.retryWithoutImages(pump, &req, &StreamProviderError{Code: "model_no_vision_support"}, &healed, 0, 0, "")
	if stream == nil {
		t.Fatal("expected a retried stream")
	}
	ev, err := stream.Next()
	_ = stream.Close()
	if err != nil || ev.Delta != "OK" {
		t.Fatalf("retried stream: err=%v ev=%+v", err, ev)
	}
	select {
	case u := <-out:
		if u.ImageNotice == nil || len(u.ImageNotice.Failures) != 1 || !strings.Contains(u.ImageNotice.Failures[0].Reason, "cannot read images") {
			t.Fatalf("the client must be told the images were not sent: %+v", u)
		}
	default:
		t.Fatal("no image notice reached the client")
	}
	if len(bodies) != 1 {
		t.Fatalf("expected one resend, got %d", len(bodies))
	}
	raw, _ := json.Marshal(bodies[0]["messages"])
	if strings.Contains(string(raw), "image_url") {
		t.Fatalf("the resent body still carried an image block: %s", raw)
	}
	if !strings.Contains(string(raw), "cannot read images") {
		t.Fatalf("the resent body must carry the note in place of the image: %s", raw)
	}
}
