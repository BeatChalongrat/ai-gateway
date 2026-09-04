package service

// FIX49 Part D — the memory block's wording contract. Instruction-class
// memories are standing instructions ("follow in every response");
// fact-class memories keep the when-relevant rule; the old blanket
// "do not force it into every response" must never again apply to the
// user's own instructions.

import (
	"strings"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func mem(mt, content string) model.Memory {
	return model.Memory{MemoryType: mt, Content: content}
}

func TestRenderMemoryBlockSplitsInstructionsFromFacts(t *testing.T) {
	block := renderMemoryBlock([]model.Memory{
		mem(model.MemoryTypeResponsePreference, "Answer in at most 5 bullets."),
		mem(model.MemoryTypeConfirmedInstruction, "Code must always be TypeScript."),
		mem(model.MemoryTypeExplicitFact, "We use PostgreSQL, not MySQL."),
	})
	insStart := strings.Index(block, "<standing_instructions>")
	insEnd := strings.Index(block, "</standing_instructions>")
	factStart := strings.Index(block, "<known_facts>")
	if insStart == -1 || factStart == -1 {
		t.Fatalf("expected both sections, got:\n%s", block)
	}
	ins := block[insStart:insEnd]
	facts := block[factStart:]
	if !strings.Contains(ins, "TypeScript") || !strings.Contains(ins, "5 bullets") {
		t.Errorf("instructions missing from standing section:\n%s", ins)
	}
	if strings.Contains(ins, "PostgreSQL") {
		t.Errorf("fact leaked into standing section:\n%s", ins)
	}
	if !strings.Contains(facts, "PostgreSQL") {
		t.Errorf("fact missing from known_facts:\n%s", facts)
	}
	// The standing section must carry the follow-always framing and
	// never the optional-colour framing.
	if !strings.Contains(ins, "Follow them in every response") {
		t.Errorf("standing framing missing:\n%s", ins)
	}
	if strings.Contains(ins, "when relevant") {
		t.Errorf("when-relevant framing must not apply to instructions:\n%s", ins)
	}
}

func TestRenderMemoryBlockFactsOnlyOmitsInstructionSection(t *testing.T) {
	block := renderMemoryBlock([]model.Memory{
		mem(model.MemoryTypeExplicitFact, "User is allergic to seafood."),
	})
	if strings.Contains(block, "<standing_instructions>") {
		t.Errorf("empty instruction section should be omitted:\n%s", block)
	}
	if !strings.Contains(block, "when relevant") {
		t.Errorf("facts keep the when-relevant rule:\n%s", block)
	}
}

func TestRenderMemoryBlockKeepsEnvelopeAndSecrecyLine(t *testing.T) {
	block := renderMemoryBlock([]model.Memory{
		mem(model.MemoryTypeOngoingInterest, "Learning Rust."),
	})
	for _, want := range []string{
		"<saved_user_memory>", "</saved_user_memory>",
		"Do not say 'according to my memory'",
	} {
		if !strings.Contains(block, want) {
			t.Errorf("missing %q in:\n%s", want, block)
		}
	}
}
