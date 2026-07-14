package handler

import (
	"encoding/json"
	"strings"
	"testing"
)

// Wire-shape tests for the desktop import DTOs — the desktop client
// (SranDesktop ChatEdgeApiClient) serializes against exactly these names.

func TestImportConversationRequestWireShape(t *testing.T) {
	var req importConversationRequest
	err := json.Unmarshal([]byte(
		`{"client_id":"local-abc","title":"My chat","model_id":"gpt-4o-mini","created_at":"2026-07-01T10:00:00Z"}`,
	), &req)
	if err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if req.ClientID != "local-abc" || req.Title != "My chat" ||
		req.ModelID != "gpt-4o-mini" || req.CreatedAt != "2026-07-01T10:00:00Z" {
		t.Fatalf("fields not mapped: %+v", req)
	}
}

func TestImportConversationResponseWireShape(t *testing.T) {
	b, err := json.Marshal(importConversationResponse{
		Conversation: conversationResponse{ID: "srv-1", Title: "My chat"},
		Created:      true,
	})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	s := string(b)
	if !strings.Contains(s, `"conversation":{`) || !strings.Contains(s, `"created":true`) ||
		!strings.Contains(s, `"id":"srv-1"`) {
		t.Fatalf("unexpected wire shape: %s", s)
	}
}

func TestImportMessagesRequestWireShape(t *testing.T) {
	var req importMessagesRequest
	err := json.Unmarshal([]byte(
		`{"messages":[{"client_id":"m1","role":"user","content":"hi","model_id":"gpt-4o","created_at":"2026-07-01T10:00:00Z"}]}`,
	), &req)
	if err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if len(req.Messages) != 1 {
		t.Fatalf("want 1 message, got %d", len(req.Messages))
	}
	m := req.Messages[0]
	if m.ClientID != "m1" || m.Role != "user" || m.Content != "hi" ||
		m.ModelID != "gpt-4o" || m.CreatedAt != "2026-07-01T10:00:00Z" {
		t.Fatalf("fields not mapped: %+v", m)
	}
}

func TestImportMessagesResponseWireShape(t *testing.T) {
	b, err := json.Marshal(importMessagesResponse{Imported: 3, Skipped: 2})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if string(b) != `{"imported":3,"skipped":2}` {
		t.Fatalf("unexpected wire shape: %s", b)
	}
}
