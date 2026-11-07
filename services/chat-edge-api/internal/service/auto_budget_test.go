// FIX102 Part A — "auto" is budgeted by the model that answers.
//
// FIX101 made the ceiling real and, in doing so, put every "auto"
// conversation (221 of the owner's) on the 14,500 default, because the
// sentinel is in neither the catalogue nor the seed map. chat-edge never
// resolves "auto": the gateway's router picks after the budget is fixed. So
// the budget follows the model that answered the last completed turn, and
// the router is told how big the request is.
package service

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func served(role, status, modelID string) model.Message {
	m := model.Message{ID: uuid.New(), Role: role, Status: status, ContentMasked: "x"}
	if modelID != "" {
		id := modelID
		m.ModelID = &id
	}
	return m
}

func TestFIX102_LastServedModelIsTheNewestCompletedConcreteReply(t *testing.T) {
	h := []model.Message{
		served("user", "completed", ""),
		served("assistant", "completed", "gpt-5.6-terra"),
		served("user", "completed", ""),
		served("assistant", "completed", "qwen3.8-27b-fp8"),
		served("user", "completed", ""),
		served("assistant", "error", "auto"),        // a failed turn keeps the sentinel
		served("assistant", "regenerated", "gpt-4"), // superseded
		served("assistant", "streaming", "auto"),    // this turn's placeholder
	}
	if got := lastServedModel(h); got != "qwen3.8-27b-fp8" {
		t.Fatalf("last served = %q, want qwen3.8-27b-fp8", got)
	}
	if got := lastServedModel([]model.Message{served("user", "completed", ""), served("assistant", "completed", "auto")}); got != "" {
		t.Fatalf("a row that still says auto is not evidence, got %q", got)
	}
}

func TestFIX102_AutoBudgetFollowsTheModelThatAnswered(t *testing.T) {
	ctx := context.Background()
	lookup := lookupOf(measuredWindows)
	s := &MessageService{contextWindow: lookup}
	autoConv := &model.Conversation{ID: uuid.New(), ModelID: AutoModelID, MemoryEnabled: true}
	h := []model.Message{served("user", "completed", ""), served("assistant", "completed", "qwen3.8-27b-fp8"), served("user", "completed", "")}

	if got := s.budgetModelFor(ctx, autoConv, h); got != "qwen3.8-27b-fp8" {
		t.Fatalf("auto conversation answered by qwen budgets against %q", got)
	}
	// A pinned model is its own budget; the compiler reads Conv.ModelID.
	pinned := &model.Conversation{ModelID: "Typhoon-S-ThaiLLM-8B-Instruct"}
	if got := s.budgetModelFor(ctx, pinned, h); got != "" {
		t.Fatalf("a pinned conversation must not be re-pointed, got %q", got)
	}
	// No evidence → the conservative default, exactly as before.
	if got := s.budgetModelFor(ctx, autoConv, h[:1]); got != "" {
		t.Fatalf("a new auto conversation has no served model, got %q", got)
	}
	// A model the catalogue does not list (no usable key, deactivated,
	// never catalogued) is not budgeted for.
	gone := []model.Message{served("assistant", "completed", "a-model-whose-key-was-removed")}
	if got := s.budgetModelFor(ctx, autoConv, gone); got != "" {
		t.Fatalf("an unlisted served model must fall back to the default, got %q", got)
	}

	c := NewContextCompiler(nil)
	c.SetContextWindowLookup(lookup)
	out, _ := c.Compile(ctx, CompileInputs{
		Conv: autoConv, History: h, NumTools: 11,
		BuildPrefix:   func() (string, []ContextSource) { return strings.Repeat("s", 4000), nil },
		BudgetModelID: s.budgetModelFor(ctx, autoConv, h),
	})
	if out.Budget.TotalBudget != 117500 || out.BudgetModel != "qwen3.8-27b-fp8" || out.BudgetWindow != 131072 {
		t.Fatalf("budget %d model %q window %d, want 117500 / qwen3.8-27b-fp8 / 131072",
			out.Budget.TotalBudget, out.BudgetModel, out.BudgetWindow)
	}
	// The sentinel with no evidence is still the default (FIX101's guard).
	fresh, _ := c.Compile(ctx, CompileInputs{
		Conv: autoConv, History: h[:1],
		BuildPrefix: func() (string, []ContextSource) { return "S", nil },
	})
	if fresh.Budget.TotalBudget != defaultContextLimit || fresh.BudgetWindow != 0 {
		t.Fatalf("no evidence → %d (window %d), want the default %d", fresh.Budget.TotalBudget, fresh.BudgetWindow, defaultContextLimit)
	}
}

func TestFIX102_TheRouterIsToldWhatTheRequestNeeds(t *testing.T) {
	cc := &CompiledContext{
		Budget:       ContextBudget{SystemPromptTokens: 4731, ToolReserve: 2200, OutputReserve: 16000},
		SentTokens:   9000,
		BudgetWindow: 131072,
	}
	h := &RoutingHints{HasTools: true}
	applyContextHints(h, cc)
	if h.MinContextTokens != 4731+2200+9000+16000 {
		t.Fatalf("min context %d", h.MinContextTokens)
	}
	if h.PreferredContextTokens != 131072 {
		t.Fatalf("preferred %d", h.PreferredContextTokens)
	}
	cc.Compacted = true
	applyContextHints(h, cc)
	if h.MinContextTokens != 4731+2200+9000+16000+contextNoteReserve {
		t.Fatal("a compacted request carries the context note too")
	}
	applyContextHints(nil, cc) // a pinned turn has no hints; must not panic
}

// The number the brief asked for, from the calibration FIX101 measured on
// the owner's conversation: ~880 tokens per turn, 11 tools, a ~4,730-token
// system prefix. Logged for the report; asserted where it was the defect.
func TestFIX102_TurnsBeforeFirstTrim(t *testing.T) {
	ctx := context.Background()
	lookup := lookupOf(map[string]int{
		"Typhoon-S-ThaiLLM-8B-Instruct": 16384, "qwen3.5-9b": 32768, "qwen3.8-27b-fp8": 131072,
		"gpt-5.6-terra": 128000, "gpt-5-mini": 272000,
	})
	turns := func(modelID string) int {
		b := CalculateBudgetWith(ctx, lookup, modelID, 4730+4, 0, 0, 0, 11*perToolTokens)
		return ratioOf(b.MessagesRemaining, DefaultCompactTriggerRatio) / 880
	}
	autoFresh := turns(AutoModelID)
	for _, id := range []string{"Typhoon-S-ThaiLLM-8B-Instruct", "qwen3.5-9b", "qwen3.8-27b-fp8", "gpt-5.6-terra", "gpt-5-mini"} {
		t.Logf("%-32s ~%d turns; auto answered by it: ~%d turns", id, turns(id), turns(id))
	}
	t.Logf("auto with no served turn yet (first turn only): ~%d turns", autoFresh)
	if turns("gpt-5.6-terra") < 80 {
		t.Fatalf("auto answered by gpt-5.6-terra must have ~88 turns of room, has %d", turns("gpt-5.6-terra"))
	}
}
