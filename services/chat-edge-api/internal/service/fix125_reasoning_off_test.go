package service

// FIX125 M3 — the thinking toggle OFF on a model the person pinned sends
// reasoning mode "off". It used to send "auto" (toggle off meant "return to
// automatic"), so a pinned reasoning model kept thinking while the toggle
// said it would not. Auto conversations are unchanged: the router decides.

import (
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func TestFIX125_PinnedModelToggleOffSendsOff(t *testing.T) {
	for _, id := range []string{"qwen3.8-27b-fp8", reasoningModel, plainModel, "deepseek/deepseek-r1"} {
		conv := &model.Conversation{ModelID: id, ReasoningEnabled: false, ReasoningMode: "auto", ShowReasoningSummary: true}
		// A complex, non-trivial turn: the old P1-4 gate never fired here.
		if mode := reasoningMode(conv, id, "วิเคราะห์ code นี้ให้หน่อย แล้วอธิบายทีละขั้น"); mode != "off" {
			t.Errorf("pinned %s + toggle off: mode=%q, want off", id, mode)
		}
		// Whatever the catalogue says about the model.
		yes := func(string) bool { return true }
		if mode := reasoningModeWith(conv, id, "explain this in detail please", yes); mode != "off" {
			t.Errorf("pinned %s + toggle off (catalogue: reasons): mode=%q, want off", id, mode)
		}
	}
}

func TestFIX125_AutoModelToggleOffUnchanged(t *testing.T) {
	for _, id := range []string{AutoModelID, ""} {
		conv := &model.Conversation{ModelID: id, ReasoningEnabled: false, ReasoningMode: "auto"}
		if mode := reasoningMode(conv, AutoModelID, "วิเคราะห์ code นี้ให้หน่อย"); mode != "auto" {
			t.Errorf("auto conversation (%q) + toggle off: mode=%q, want auto", id, mode)
		}
	}
}

func TestFIX125_PinnedModelToggleOnKeepsChosenMode(t *testing.T) {
	for _, m := range []string{"auto", "low", "medium", "high"} {
		conv := &model.Conversation{ModelID: reasoningModel, ReasoningEnabled: true, ReasoningMode: m}
		if mode := reasoningMode(conv, reasoningModel, "วิเคราะห์ code นี้"); mode != m {
			t.Errorf("pinned + toggle on (%s): mode=%q, want %s", m, mode, m)
		}
	}
}
