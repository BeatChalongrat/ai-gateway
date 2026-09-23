package service

// FIX12 Item 3 — user-skill prompt-injection + trigger-matching unit
// tests. Two properties matter:
//
//   * with a matched ActiveSkill the compiled prompt carries the skill's
//     instruction text; with none the prompt is byte-identical to a
//     no-skills prompt (proves the compiler-level injection criterion);
//   * trigger matching is a case-insensitive substring test against the
//     latest user message; the catalog carries ALL enabled skills.
//
// No DB — the compiler is repo-free and resolveSkillsForTurn is driven
// by a stub SkillReader.

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func TestSkillsBlock_ActiveInjectsInstructions(t *testing.T) {
	pc := newPromptCompiler(stubPrefsReader{block: ""}, nil, nil)
	base := CompileContext{Model: "gpt-4o", TenantID: uuid.New(), UserID: uuid.New()}

	// Empty → byte-identical to a no-skills prompt, and no skill markers.
	withActive := base
	withActive.ActiveSkills = []model.Skill{{
		Name:         "Contract Review",
		Instructions: "ตรวจสัญญาโดยอ้างอิงเลขข้อเสมอ",
	}}
	empty, _ := pc.Build(context.Background(), base)
	got, _ := pc.Build(context.Background(), withActive)

	if strings.Contains(empty, "--- Skill:") || strings.Contains(empty, "Available skills") {
		t.Fatalf("empty skills leaked a skill block into the prompt:\n%s", empty)
	}
	if !strings.Contains(got, "--- Skill: Contract Review ---") {
		t.Fatalf("active skill header missing:\n%s", got)
	}
	if !strings.Contains(got, "ตรวจสัญญาโดยอ้างอิงเลขข้อเสมอ") {
		t.Fatalf("active skill instructions missing:\n%s", got)
	}
}

func TestSkillsBlock_EmptyByteIdentical(t *testing.T) {
	pc := newPromptCompiler(stubPrefsReader{block: "## About the user\nx\n"}, nil, nil)
	base := CompileContext{Model: "gpt-4o", TenantID: uuid.New(), UserID: uuid.New()}

	a, _ := pc.Build(context.Background(), base)

	withNilSlices := base
	withNilSlices.ActiveSkills = nil
	withNilSlices.SkillCatalog = nil
	b, _ := pc.Build(context.Background(), withNilSlices)

	if a != b {
		t.Fatal("nil skill slices changed the compiled prompt (must be byte-identical)")
	}
}

func TestSkillsBlock_CatalogListsEnabled(t *testing.T) {
	pc := newPromptCompiler(stubPrefsReader{block: ""}, nil, nil)
	in := CompileContext{
		Model: "gpt-4o", TenantID: uuid.New(), UserID: uuid.New(),
		SkillCatalog: []model.Skill{
			{Name: "Contract Review", Description: "review Thai contracts"},
			{Name: "Translate EN-TH", Description: "translate technical docs"},
		},
	}
	got, _ := pc.Build(context.Background(), in)
	if !strings.Contains(got, "Available skills (call use_skill to load one):") {
		t.Fatalf("catalog header missing:\n%s", got)
	}
	if !strings.Contains(got, "- Contract Review: review Thai contracts") ||
		!strings.Contains(got, "- Translate EN-TH: translate technical docs") {
		t.Fatalf("catalog lines missing:\n%s", got)
	}
}

// stubSkillReader satisfies SkillReader with a fixed enabled set.
type stubSkillReader struct {
	skills []model.Skill
	err    error
}

func (s stubSkillReader) ListEnabledByUser(context.Context, uuid.UUID, uuid.UUID) ([]model.Skill, error) {
	return s.skills, s.err
}

func TestSkillTriggersMatch_CaseInsensitiveSubstring(t *testing.T) {
	low := strings.ToLower("Please REVIEW this ContRact for me")
	if !skillTriggersMatch([]string{"Contract"}, low) {
		t.Fatal("expected case-insensitive substring match")
	}
	if skillTriggersMatch([]string{"invoice"}, low) {
		t.Fatal("unrelated trigger must not match")
	}
	if skillTriggersMatch([]string{"", "   "}, low) {
		t.Fatal("blank triggers must never match")
	}
	if skillTriggersMatch([]string{"contract"}, "") {
		t.Fatal("empty text must not match")
	}
}

func TestResolveSkillsForTurn_ActiveVsCatalog(t *testing.T) {
	enabled := []model.Skill{
		{Name: "Contract Review", Triggers: []string{"contract", "สัญญา"}, Instructions: "..."},
		{Name: "Translate", Triggers: []string{"translate"}, Instructions: "..."},
	}
	s := &MessageService{skillReader: stubSkillReader{skills: enabled}}

	active, catalog := s.resolveSkillsForTurn(
		context.Background(), uuid.New(), uuid.New(), "help me review this CONTRACT",
	)
	if len(catalog) != 2 {
		t.Fatalf("catalog must carry all enabled skills, got %d", len(catalog))
	}
	if len(active) != 1 || active[0].Name != "Contract Review" {
		t.Fatalf("only the matched skill is active, got %+v", active)
	}

	// No reader wired → no skills, and never a panic.
	none := &MessageService{}
	a, c := none.resolveSkillsForTurn(context.Background(), uuid.New(), uuid.New(), "contract")
	if a != nil || c != nil {
		t.Fatal("nil reader must yield no skills")
	}

	// Reader error degrades to no skills (never blocks the reply).
	bad := &MessageService{skillReader: stubSkillReader{err: errors.New("boom")}}
	a, c = bad.resolveSkillsForTurn(context.Background(), uuid.New(), uuid.New(), "contract")
	if a != nil || c != nil {
		t.Fatal("reader error must yield no skills")
	}
}
