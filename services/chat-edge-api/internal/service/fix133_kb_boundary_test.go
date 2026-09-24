package service

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// FIX133 E6a — a turn that carries knowledge-base passages is answered
// inside the self-hosted boundary: the gateway is pinned to the allowed
// providers, and a chat pinned to an external model is answered by Auto
// for that turn, with a notice. A turn without KB passages is untouched.
func TestFix133KBBoundary(t *testing.T) {
	conv := &model.Conversation{ID: uuid.New(), ModelID: "deepseek-v4-flash", RoutingTier: "standard"}
	kbSources := func() *SourcesAccumulator {
		acc := NewSourcesAccumulator()
		acc.Add(ContextSource{SourceType: SourceTypeKnowledgeChunk, DisplayLabel: "manual.pdf"})
		return acc
	}
	noKB := func() *SourcesAccumulator {
		acc := NewSourcesAccumulator()
		acc.Add(ContextSource{SourceType: "memory", DisplayLabel: "a memory"})
		return acc
	}
	external := func(_ context.Context, id string) (bool, bool) {
		switch id {
		case "qwen3.8-27b-fp8":
			return true, true
		case "deepseek-v4-flash":
			return false, true
		}
		return false, false
	}

	t.Run("off by default: nothing changes", func(t *testing.T) {
		s := &MessageService{}
		req := GatewayChatRequest{Model: "deepseek-v4-flash"}
		if n := s.applyKBBoundary(context.Background(), conv, &req, kbSources(), "q", false, false, 2); n != nil {
			t.Fatalf("boundary off must not touch the request: %+v", n)
		}
		if req.Model != "deepseek-v4-flash" || req.Metadata != nil {
			t.Fatalf("request changed: %+v", req)
		}
	})

	s := &MessageService{}
	s.SetKBBoundary(true, []string{"sranai"})
	s.SetSelfHostedModelChecker(external)

	t.Run("no KB passages: untouched", func(t *testing.T) {
		req := GatewayChatRequest{Model: "deepseek-v4-flash"}
		if n := s.applyKBBoundary(context.Background(), conv, &req, noKB(), "q", false, false, 2); n != nil {
			t.Fatalf("no KB → no notice, got %+v", n)
		}
		if req.Metadata != nil || req.Model != "deepseek-v4-flash" {
			t.Fatalf("request changed without KB: %+v", req)
		}
	})

	t.Run("external model + KB: Auto inside the boundary, with a notice", func(t *testing.T) {
		req := GatewayChatRequest{Model: "deepseek-v4-flash"}
		n := s.applyKBBoundary(context.Background(), conv, &req, kbSources(), "what is the part number", false, false, 4)
		if n == nil || n.RequestedModel != "deepseek-v4-flash" || n.Reason != "knowledge_base_self_hosted" {
			t.Fatalf("notice: %+v", n)
		}
		if !IsAutoModel(req.Model) {
			t.Fatalf("model must be Auto for this turn, got %q", req.Model)
		}
		allowed, _ := req.Metadata["allowed_providers"].([]string)
		if len(allowed) != 1 || allowed[0] != "sranai" {
			t.Fatalf("allowed_providers: %+v", req.Metadata)
		}
		if req.RoutingHints == nil || req.RoutingHints.Tier != "standard" {
			t.Fatalf("routing hints must be built for the Auto turn: %+v", req.RoutingHints)
		}
	})

	t.Run("self-hosted model + KB: pinned providers only, no notice", func(t *testing.T) {
		req := GatewayChatRequest{Model: "qwen3.8-27b-fp8"}
		if n := s.applyKBBoundary(context.Background(), conv, &req, kbSources(), "q", false, false, 2); n != nil {
			t.Fatalf("a self-hosted model needs no notice: %+v", n)
		}
		if req.Model != "qwen3.8-27b-fp8" || req.Metadata["allowed_providers"] == nil {
			t.Fatalf("request: %+v", req)
		}
	})

	t.Run("Auto + KB: pinned providers only, no notice", func(t *testing.T) {
		req := GatewayChatRequest{Model: AutoModelID}
		if n := s.applyKBBoundary(context.Background(), conv, &req, kbSources(), "q", false, false, 2); n != nil {
			t.Fatalf("Auto needs no notice: %+v", n)
		}
		if req.Metadata["allowed_providers"] == nil {
			t.Fatalf("Auto must still be pinned: %+v", req.Metadata)
		}
	})

	t.Run("unknown model + KB: treated as external", func(t *testing.T) {
		req := GatewayChatRequest{Model: "some-new-model"}
		n := s.applyKBBoundary(context.Background(), conv, &req, kbSources(), "q", false, false, 2)
		if n == nil || !IsAutoModel(req.Model) {
			t.Fatalf("unknown model must not carry KB passages out: notice=%+v model=%q", n, req.Model)
		}
	})
}
