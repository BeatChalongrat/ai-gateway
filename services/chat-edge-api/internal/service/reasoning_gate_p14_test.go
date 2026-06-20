package service

// P1-4 — pinned reasoning-model triviality gate. When a user PINS a concrete
// reasoning-capable model (not Auto), a trivial turn ("ไง") should not burn a
// thinking budget: attachReasoning sends mode="off". Complex turns and explicit
// user modes are unaffected; Auto-model turns are unaffected.

import (
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

const reasoningModel = "claude-sonnet-4-20250514" // isReasoningCapableID == true
const plainModel = "gpt-4o-mini"                  // not reasoning-capable

func reasoningMode(conv *model.Conversation, modelID, message string) string {
	req := &GatewayChatRequest{}
	attachReasoning(req, conv, modelID, message)
	if req.Reasoning == nil {
		return "<nil>"
	}
	return req.Reasoning.Mode
}


// --- isTrivialQuery unit ---------------------------------------------------

func TestIsTrivialQuery(t *testing.T) {
	cases := []struct {
		msg  string
		want bool
	}{
		{"ไง", true},
		{"หวัดดี", true},
		{"hi", true},
		{"ขอบคุณ", true},
		{"วิเคราะห์ code นี้", false},   // complex signal
		{"analyze this", false},          // complex signal
		{"วันนี้วันอะไร?", false},        // a question wants an answer
		{"```\nx=1\n```", false},         // code fence → complex
		{"please write a fairly long sentence that clearly exceeds thirty runes", false},
	}
	for _, c := range cases {
		if got := isTrivialQuery(c.msg); got != c.want {
			t.Errorf("isTrivialQuery(%q) = %v, want %v", c.msg, got, c.want)
		}
	}
}


// --- attachReasoning gate --------------------------------------------------

func TestPinnedThinkingModelTrivialTurnIsOff(t *testing.T) {
	conv := &model.Conversation{ModelID: reasoningModel}
	for _, msg := range []string{"ไง", "หวัดดี", "hi"} {
		if mode := reasoningMode(conv, reasoningModel, msg); mode != "off" {
			t.Errorf("pinned thinking model + %q: mode=%q, want off", msg, mode)
		}
	}
}

func TestPinnedThinkingModelComplexTurnIsAuto(t *testing.T) {
	conv := &model.Conversation{ModelID: reasoningModel}
	if mode := reasoningMode(conv, reasoningModel, "วิเคราะห์ code นี้ให้หน่อย"); mode != "auto" {
		t.Errorf("pinned thinking model + complex: mode=%q, want auto", mode)
	}
}

func TestExplicitUserModeWins(t *testing.T) {
	// Even a trivial turn keeps the user's explicit depth choice.
	conv := &model.Conversation{
		ModelID: reasoningModel, ReasoningEnabled: true, ReasoningMode: "high",
	}
	if mode := reasoningMode(conv, reasoningModel, "ไง"); mode != "high" {
		t.Errorf("explicit high + trivial: mode=%q, want high", mode)
	}
}

func TestPinnedNonThinkingModelUnchanged(t *testing.T) {
	// Non-reasoning pin: gate does not apply; mode stays the auto default
	// (the gateway omits the thinking block downstream for a plain model).
	conv := &model.Conversation{ModelID: plainModel}
	if mode := reasoningMode(conv, plainModel, "ไง"); mode != "auto" {
		t.Errorf("pinned plain model + trivial: mode=%q, want auto", mode)
	}
}

func TestAutoModelTrivialTurnUnchanged(t *testing.T) {
	// Auto model: the gate is skipped (router handles trivial routing); the
	// modelID forwarded for an auto turn is the "auto" sentinel.
	conv := &model.Conversation{ModelID: AutoModelID}
	if mode := reasoningMode(conv, AutoModelID, "ไง"); mode != "auto" {
		t.Errorf("auto model + trivial: mode=%q, want auto", mode)
	}
}

func TestConversationLevelOffRespected(t *testing.T) {
	conv := &model.Conversation{ModelID: reasoningModel, ReasoningMode: "off"}
	if mode := reasoningMode(conv, reasoningModel, "วิเคราะห์ code นี้"); mode != "off" {
		t.Errorf("conversation off: mode=%q, want off", mode)
	}
}
