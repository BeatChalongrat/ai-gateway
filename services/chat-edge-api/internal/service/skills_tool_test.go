package service

// FIX12 Item 3 — use_skill tool test. Verifies the tool returns the
// named skill's instruction text and is scoped to the caller: user B
// asking for user A's skill by name gets "not found", never A's text.
//
// INTEGRATION: the tool depends on the concrete *repository.SkillRepo,
// so this seeds real rows. Skips cleanly when the DB is unreachable;
// migration 034 is idempotent and applied by the helper.

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func skillToolPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	// FIX119 Part C1 — a private clone of the test template, never the
	// live database (internal/testdb refuses it and fails without a DB).
	return testdb.Fresh(t)
}

func TestExecUseSkill_ReturnsInstructionsAndScoped(t *testing.T) {
	pool := skillToolPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	repo := repository.NewSkillRepo(pool)

	tenantID := uuid.New()
	userA, userB := uuid.New(), uuid.New()
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.user_skills WHERE tenant_id = $1", tenantID)
	})

	if _, err := repo.Create(ctx, &model.Skill{
		TenantID: tenantID, UserID: userA, Name: "Contract Review",
		Instructions: "อ้างอิงเลขข้อสัญญาเสมอ", Enabled: true,
	}); err != nil {
		t.Fatalf("seed skill: %v", err)
	}

	e := &ToolExecutor{}
	e.SetSkillRepo(repo)

	call := GatewayToolCall{
		Function: GatewayToolCallFunction{Name: "use_skill", Arguments: `{"name":"contract review"}`},
	}

	// Owner (A) gets the instruction text (case-insensitive name match).
	out := e.Execute(ctx, call, ToolExecContext{TenantID: tenantID, UserID: userA})
	if !strings.Contains(out, "อ้างอิงเลขข้อสัญญาเสมอ") {
		t.Fatalf("owner should get the skill instructions, got:\n%s", out)
	}
	if !strings.Contains(out, "--- Skill: Contract Review ---") {
		t.Fatalf("owner result missing skill header, got:\n%s", out)
	}

	// User B must NOT get A's text — scoped "not found".
	outB := e.Execute(ctx, call, ToolExecContext{TenantID: tenantID, UserID: userB})
	if strings.Contains(outB, "อ้างอิงเลขข้อสัญญาเสมอ") {
		t.Fatalf("cross-user leak: user B received user A's skill text:\n%s", outB)
	}
	if !strings.Contains(strings.ToLower(outB), "no skill named") {
		t.Fatalf("user B should get a not-found message, got:\n%s", outB)
	}
}

func TestToolDefs_IncludesUseSkillWhenWired(t *testing.T) {
	e := &ToolExecutor{}
	conv := &model.Conversation{MemoryEnabled: false}
	if defsContainUseSkill(e.ToolDefs(conv)) {
		t.Fatal("use_skill must not be offered when the skill repo is unwired")
	}
	e.SetSkillRepo(&repository.SkillRepo{}) // non-nil ⇒ HasSkills() true
	if !defsContainUseSkill(e.ToolDefs(conv)) {
		t.Fatal("use_skill must be offered once the skill repo is wired")
	}
}

func defsContainUseSkill(defs []GatewayTool) bool {
	for _, d := range defs {
		if d.Function.Name == "use_skill" {
			return true
		}
	}
	return false
}
