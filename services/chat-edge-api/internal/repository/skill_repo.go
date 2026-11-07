// SkillRepo persists chat.user_skills (migration 034 — FIX12 Item 3).
//
// Every method that returns or mutates skill data filters by
// (tenant_id, user_id) — no exceptions. A skill is private to the user
// who authored it; a leaked id from another user is indistinguishable
// from a missing row.
//
// HARD SECURITY INVARIANT — instruction-only, NEVER executable: a
// skill's `instructions` is plain text handed to the model. This repo
// only ever reads/writes that text; nothing here (and nothing that
// calls it) executes a skill's content.
package repository

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type SkillRepo struct {
	db DB
}

func NewSkillRepo(db *pgxpool.Pool) *SkillRepo {
	return &SkillRepo{db: db}
}

func (r *SkillRepo) WithTx(tx pgx.Tx) *SkillRepo {
	return &SkillRepo{db: tx}
}

// skillColumns is the single source of truth for SELECT projections,
// mirroring the model.Skill field order so scanSkill can stay a
// straightforward unpack.
const skillColumns = `
	id, tenant_id, user_id,
	name, description, triggers, instructions,
	enabled,
	created_at, updated_at
`

func scanSkill(row pgx.Row, s *model.Skill) error {
	return row.Scan(
		&s.ID, &s.TenantID, &s.UserID,
		&s.Name, &s.Description, &s.Triggers, &s.Instructions,
		&s.Enabled,
		&s.CreatedAt, &s.UpdatedAt,
	)
}

// validateSkillCaps enforces the same size caps the migration's CHECK
// constraints do, so an oversize skill is rejected with a clear error
// instead of a raw constraint-violation from Postgres. Defensive: the
// handler validates too, but the repo is the last line.
func validateSkillCaps(s *model.Skill) error {
	name := strings.TrimSpace(s.Name)
	if name == "" || len(name) > model.SkillNameMaxLen {
		return fmt.Errorf("%w: name must be 1-%d chars", ErrInvalidSkill, model.SkillNameMaxLen)
	}
	if len(s.Description) > model.SkillDescriptionMaxLen {
		return fmt.Errorf("%w: description must be <= %d chars", ErrInvalidSkill, model.SkillDescriptionMaxLen)
	}
	if l := len(strings.TrimSpace(s.Instructions)); l == 0 || len(s.Instructions) > model.SkillInstructionsMaxLen {
		return fmt.Errorf("%w: instructions must be 1-%d chars", ErrInvalidSkill, model.SkillInstructionsMaxLen)
	}
	return nil
}

// ErrInvalidSkill is returned by Create when the skill fails the size
// caps. The handler maps it to 400.
var ErrInvalidSkill = errors.New("repository: invalid skill")

// Create inserts a new skill row and returns the post-insert row (with
// the generated id + timestamps). Enforces the size caps defensively
// before touching SQL.
func (r *SkillRepo) Create(ctx context.Context, s *model.Skill) (*model.Skill, error) {
	if err := validateSkillCaps(s); err != nil {
		return nil, err
	}
	if s.Triggers == nil {
		s.Triggers = []string{}
	}
	const q = `
		INSERT INTO chat.user_skills (
			tenant_id, user_id,
			name, description, triggers, instructions, enabled
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		RETURNING ` + skillColumns
	out := &model.Skill{}
	err := scanSkill(r.db.QueryRow(ctx, q,
		s.TenantID, s.UserID,
		strings.TrimSpace(s.Name), s.Description, s.Triggers, s.Instructions, s.Enabled,
	), out)
	if err != nil {
		return nil, fmt.Errorf("create skill: %w", err)
	}
	return out, nil
}

// ListEnabledByUser returns the user's enabled skills, newest first.
// This is the turn-time read path (trigger matching + use_skill
// catalog). Hard-scoped by (tenant_id, user_id).
func (r *SkillRepo) ListEnabledByUser(
	ctx context.Context, tenantID, userID uuid.UUID,
) ([]model.Skill, error) {
	q := `SELECT ` + skillColumns + `
	      FROM chat.user_skills
	      WHERE tenant_id = $1 AND user_id = $2 AND enabled = true
	      ORDER BY updated_at DESC`
	return r.queryList(ctx, q, tenantID, userID)
}

// ListByUser returns ALL of the user's skills (enabled + disabled) for
// the management surface, newest first.
func (r *SkillRepo) ListByUser(
	ctx context.Context, tenantID, userID uuid.UUID,
) ([]model.Skill, error) {
	q := `SELECT ` + skillColumns + `
	      FROM chat.user_skills
	      WHERE tenant_id = $1 AND user_id = $2
	      ORDER BY updated_at DESC`
	return r.queryList(ctx, q, tenantID, userID)
}

func (r *SkillRepo) queryList(
	ctx context.Context, q string, tenantID, userID uuid.UUID,
) ([]model.Skill, error) {
	rows, err := r.db.Query(ctx, q, tenantID, userID)
	if err != nil {
		return nil, fmt.Errorf("list skills: %w", err)
	}
	defer rows.Close()
	out := make([]model.Skill, 0, 8)
	for rows.Next() {
		var s model.Skill
		if err := scanSkill(rows, &s); err != nil {
			return nil, fmt.Errorf("list skills scan: %w", err)
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

// GetByID returns a single skill row. Hard-scoped to (tenant_id,
// user_id) so a leaked id from another user returns ErrNotFound.
func (r *SkillRepo) GetByID(
	ctx context.Context, tenantID, userID, id uuid.UUID,
) (*model.Skill, error) {
	q := `SELECT ` + skillColumns + `
	      FROM chat.user_skills
	      WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	s := &model.Skill{}
	if err := scanSkill(r.db.QueryRow(ctx, q, id, tenantID, userID), s); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get skill: %w", err)
	}
	return s, nil
}

// GetByName resolves a skill by its (case-insensitive) name, scoped to
// the user. Backs the use_skill tool. Returns ErrNotFound when the user
// has no enabled skill by that name.
func (r *SkillRepo) GetByName(
	ctx context.Context, tenantID, userID uuid.UUID, name string,
) (*model.Skill, error) {
	q := `SELECT ` + skillColumns + `
	      FROM chat.user_skills
	      WHERE tenant_id = $1 AND user_id = $2
	        AND enabled = true
	        AND lower(name) = lower($3)`
	s := &model.Skill{}
	if err := scanSkill(r.db.QueryRow(ctx, q, tenantID, userID, strings.TrimSpace(name)), s); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get skill by name: %w", err)
	}
	return s, nil
}

// SetEnabled toggles a skill's enabled flag. Tenant + user scoped.
func (r *SkillRepo) SetEnabled(
	ctx context.Context, tenantID, userID, id uuid.UUID, enabled bool,
) error {
	const q = `
		UPDATE chat.user_skills
		SET enabled = $4
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3
	`
	tag, err := r.db.Exec(ctx, q, id, tenantID, userID, enabled)
	if err != nil {
		return fmt.Errorf("set skill enabled: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// Delete HARD-deletes a skill row (no soft-delete tombstone — a skill
// carries no audit-trail obligation like a memory does). Tenant + user
// scoped so a leaked id can't remove another user's skill.
func (r *SkillRepo) Delete(
	ctx context.Context, tenantID, userID, id uuid.UUID,
) error {
	const q = `DELETE FROM chat.user_skills
	           WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	tag, err := r.db.Exec(ctx, q, id, tenantID, userID)
	if err != nil {
		return fmt.Errorf("delete skill: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// HardDeleteAllByUser purges every skill for one user — the GDPR
// right-to-erasure path, mirroring MemoryRepo.HardDeleteAllByUser.
// Returns the affected row count for the audit log.
func (r *SkillRepo) HardDeleteAllByUser(
	ctx context.Context, tenantID, userID uuid.UUID,
) (int, error) {
	const q = `DELETE FROM chat.user_skills WHERE tenant_id = $1 AND user_id = $2`
	tag, err := r.db.Exec(ctx, q, tenantID, userID)
	if err != nil {
		return 0, fmt.Errorf("hard delete skills: %w", err)
	}
	return int(tag.RowsAffected()), nil
}
