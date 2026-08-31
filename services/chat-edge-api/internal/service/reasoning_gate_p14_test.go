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

// reasoningMode exercises the nil-checker path — i.e. the static
// isReasoningCapableID fallback (gateway-down behaviour).
func reasoningMode(conv *model.Conversation, modelID, message string) string {
	return reasoningModeWith(conv, modelID, message, nil)
}

// reasoningModeWith is the catalog-injection fixture: the checker plays
// the role of handler.ModelHandler.ReasoningCapable bound to a ctx.
func reasoningModeWith(conv *model.Conversation, modelID, message string, capable func(string) bool) string {
	req := &GatewayChatRequest{}
	attachReasoning(req, conv, modelID, message, capable)
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
		{"วิเคราะห์ code นี้", false}, // complex signal
		{"analyze this", false},       // complex signal
		{"วันนี้วันอะไร?", false},     // a question wants an answer
		{"```\nx=1\n```", false},      // code fence → complex
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

// --- catalog-backed gate (models-catalog step 3) ---------------------------

// The gate follows an injected live-catalog checker: a DB-onboarded
// reasoning model unknown to the static set gets its trivial turns
// downgraded, and a model the catalog says does NOT reason keeps "auto"
// even if the static set claims otherwise.
func TestGateFollowsInjectedCatalog(t *testing.T) {
	dbModel := "deepseek/deepseek-r1" // in model_configs, never in the static set
	catalog := func(id string) bool { return id == dbModel }

	conv := &model.Conversation{ModelID: dbModel}
	if mode := reasoningModeWith(conv, dbModel, "ไง", catalog); mode != "off" {
		t.Errorf("catalog-known reasoning model, trivial turn: mode=%q, want off", mode)
	}
	// Same model through the static fallback: unknown -> no downgrade.
	if mode := reasoningMode(conv, dbModel, "ไง"); mode != "auto" {
		t.Errorf("static fallback should not know %s: mode=%q, want auto", dbModel, mode)
	}
	// Catalog says the pinned model does NOT reason -> gate never fires.
	convStatic := &model.Conversation{ModelID: reasoningModel}
	no := func(string) bool { return false }
	if mode := reasoningModeWith(convStatic, reasoningModel, "ไง", no); mode != "auto" {
		t.Errorf("catalog-declined model: mode=%q, want auto", mode)
	}
}
