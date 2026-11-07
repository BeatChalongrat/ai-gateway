package repository_test

// FIX12 Item 3 — user-skill repo tests.
//
//   * size caps — Create rejects oversize instructions WITHOUT touching
//     the DB (pure unit, no pool needed);
//   * cross-user scoping — user B cannot Get / Delete user A's skill;
//   * ListEnabledByUser ignores disabled skills.
//
// The scoping + listing tests are INTEGRATION tests against the real
// chat schema; they skip cleanly when the DB is unreachable. Migration
// 034 is fully idempotent, so the helper applies it before running.

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestSkillRepo_Create_RejectsOversizeInstructions(t *testing.T) {
	// No DB: validateSkillCaps runs before any query, so a nil pool is
	// never dereferenced.
	r := repository.NewSkillRepo(nil)
	_, err := r.Create(context.Background(), &model.Skill{
		TenantID:     uuid.New(),
		UserID:       uuid.New(),
		Name:         "Too Big",
		Instructions: strings.Repeat("x", model.SkillInstructionsMaxLen+1),
	})
	if !errors.Is(err, repository.ErrInvalidSkill) {
		t.Fatalf("expected ErrInvalidSkill for oversize instructions, got %v", err)
	}

	// Empty instructions also rejected.
	_, err = r.Create(context.Background(), &model.Skill{
		TenantID: uuid.New(), UserID: uuid.New(), Name: "Empty", Instructions: "   ",
	})
	if !errors.Is(err, repository.ErrInvalidSkill) {
		t.Fatalf("expected ErrInvalidSkill for empty instructions, got %v", err)
	}

	// Oversize name rejected.
	_, err = r.Create(context.Background(), &model.Skill{
		TenantID: uuid.New(), UserID: uuid.New(),
		Name: strings.Repeat("n", model.SkillNameMaxLen+1), Instructions: "ok",
	})
	if !errors.Is(err, repository.ErrInvalidSkill) {
		t.Fatalf("expected ErrInvalidSkill for oversize name, got %v", err)
	}
}

func skillPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	// FIX119 Part C1 — a private clone of the test template, never the
	// live database (internal/testdb refuses it and fails without a DB).
	return testdb.Fresh(t)
}

func TestSkillRepo_ScopingAndListing(t *testing.T) {
	pool := skillPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	r := repository.NewSkillRepo(pool)

	tenantID := uuid.New()
	userA, userB := uuid.New(), uuid.New()
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.user_skills WHERE tenant_id = $1", tenantID)
	})

	// A creates two skills; one disabled.
	enabled, err := r.Create(ctx, &model.Skill{
		TenantID: tenantID, UserID: userA, Name: "Contract Review",
		Description: "review Thai contracts", Triggers: []string{"contract", "สัญญา"},
		Instructions: "อ้างอิงเลขข้อเสมอ", Enabled: true,
	})
	if err != nil {
		t.Fatalf("create enabled: %v", err)
	}
	if _, err := r.Create(ctx, &model.Skill{
		TenantID: tenantID, UserID: userA, Name: "Draft Memo",
		Instructions: "keep it formal", Enabled: false,
	}); err != nil {
		t.Fatalf("create disabled: %v", err)
	}

	// ListEnabledByUser returns only the enabled skill.
	en, err := r.ListEnabledByUser(ctx, tenantID, userA)
	if err != nil {
		t.Fatalf("list enabled: %v", err)
	}
	if len(en) != 1 || en[0].Name != "Contract Review" {
		t.Fatalf("ListEnabledByUser must exclude disabled skills, got %+v", en)
	}
	if len(en[0].Triggers) != 2 {
		t.Fatalf("triggers round-trip failed: %+v", en[0].Triggers)
	}

	// ListByUser returns both.
	all, err := r.ListByUser(ctx, tenantID, userA)
	if err != nil || len(all) != 2 {
		t.Fatalf("ListByUser expected 2, got %d err=%v", len(all), err)
	}

	// Cross-user scoping: B cannot Get A's skill.
	if _, err := r.GetByID(ctx, tenantID, userB, enabled.ID); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("user B must not read user A's skill, got %v", err)
	}
	// B cannot resolve it by name either.
	if _, err := r.GetByName(ctx, tenantID, userB, "Contract Review"); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("user B must not resolve user A's skill by name, got %v", err)
	}
	// B cannot Delete it.
	if err := r.Delete(ctx, tenantID, userB, enabled.ID); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("user B must not delete user A's skill, got %v", err)
	}
	// A's skill is still there.
	if _, err := r.GetByID(ctx, tenantID, userA, enabled.ID); err != nil {
		t.Fatalf("owner read after failed cross-user delete: %v", err)
	}

	// Owner can toggle + delete.
	if err := r.SetEnabled(ctx, tenantID, userA, enabled.ID, false); err != nil {
		t.Fatalf("owner SetEnabled: %v", err)
	}
	if err := r.Delete(ctx, tenantID, userA, enabled.ID); err != nil {
		t.Fatalf("owner Delete: %v", err)
	}
}
