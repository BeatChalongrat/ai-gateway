package service

// FIX36 Part C — pinning semantics, locked by tests:
//
//   auto      never pins: every turn ships the "auto" sentinel plus fresh
//             hints, so the gateway re-chooses per turn (a key rotation or a
//             different kind of question changes the served model).
//   explicit  pins the whole chat: the exact conversation model id goes to
//             the gateway with NO hints — the gateway's explicit path serves
//             that id verbatim (openrouter/auto included: it is an explicit
//             id here and passes through untouched).
//
// SetLockedModel still exists for legacy rows but nothing on the send path
// calls it — resolveAutoIfNeeded never writes a lock.

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func pinConv(modelID, tier string) *model.Conversation {
	return &model.Conversation{
		ID:          uuid.New(),
		ModelID:     modelID,
		RoutingTier: tier,
	}
}

func TestResolveAuto_AutoSentinelNeverPins(t *testing.T) {
	s := &MessageService{}
	conv := pinConv(AutoModelID, "")
	got, hints := s.resolveAutoIfNeeded(context.Background(), conv, uuid.New(), "สวัสดี ช่วยสรุปให้หน่อย", nil, 3)
	if got != AutoModelID {
		t.Fatalf("auto turn must ship the auto sentinel, got %q", got)
	}
	if hints == nil {
		t.Fatal("auto turn must carry routing hints so the gateway re-chooses per turn")
	}
}

func TestResolveAuto_EmptyModelTreatedAsAuto(t *testing.T) {
	s := &MessageService{}
	conv := pinConv("", "")
	got, hints := s.resolveAutoIfNeeded(context.Background(), conv, uuid.New(), "hello", nil, 0)
	if got != AutoModelID || hints == nil {
		t.Fatalf("empty model id must route as auto, got (%q, hints=%v)", got, hints != nil)
	}
}

func TestResolveAuto_ExplicitModelPinsWholeChat(t *testing.T) {
	s := &MessageService{}
	for _, id := range []string{"gpt-4o", "claude-sonnet-4-5", "openrouter/auto", "deepseek/deepseek-chat"} {
		conv := pinConv(id, "")
		got, hints := s.resolveAutoIfNeeded(context.Background(), conv, uuid.New(), "อธิบาย quantum computing", nil, 10)
		if got != id {
			t.Fatalf("explicit %q must pass through verbatim, got %q", id, got)
		}
		if hints != nil {
			t.Fatalf("explicit %q must ship no hints — the gateway must not re-route it", id)
		}
	}
}

func TestResolveAuto_ConversationTierRidesInHints(t *testing.T) {
	s := &MessageService{}
	conv := pinConv(AutoModelID, "low")
	_, hints := s.resolveAutoIfNeeded(context.Background(), conv, uuid.New(), "hi", nil, 0)
	if hints == nil || hints.Tier != "low" {
		t.Fatalf("conversation routing tier must ride along in hints, got %+v", hints)
	}
}

func TestResolveAuto_TierEmptyMeansWorkspaceDefault(t *testing.T) {
	s := &MessageService{}
	conv := pinConv(AutoModelID, "")
	_, hints := s.resolveAutoIfNeeded(context.Background(), conv, uuid.New(), "hi", nil, 0)
	if hints == nil || hints.Tier != "" {
		t.Fatalf("unset tier must stay empty (gateway applies tenant/deployment default), got %+v", hints)
	}
}
