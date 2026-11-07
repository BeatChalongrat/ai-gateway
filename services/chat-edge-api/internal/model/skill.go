package model

// Skill maps to chat.user_skills (migration 034) — FIX12 Item 3.
//
// A skill is a named, reusable block of INSTRUCTION TEXT the user
// authors once. It is string-concatenated into the system prompt when
// one of its triggers matches the user's latest message, or returned
// verbatim as a use_skill tool result. That is the entire contract.
//
// HARD SECURITY INVARIANT — instruction-only, NEVER executable:
// there is deliberately no command / exec / entrypoint / script field
// on this struct, and no code path runs a skill's Instructions. The
// content is plain prose handed to the model, exactly like a saved
// memory or a project instruction block.
//
// The struct shape mirrors the column list exactly so the repo's
// hand-rolled INSERT/SELECT statements stay in 1:1 sync with the DB.

import (
	"time"

	"github.com/google/uuid"
)

// Skill size caps — mirrored from the migration's CHECK constraints so
// the repo + handler can reject oversize input before it reaches SQL.
const (
	SkillNameMaxLen         = 80
	SkillDescriptionMaxLen  = 500
	SkillInstructionsMaxLen = 10000
)

// Skill is one row from chat.user_skills.
type Skill struct {
	ID       uuid.UUID `json:"id"`
	TenantID uuid.UUID `json:"-"`
	UserID   uuid.UUID `json:"-"`

	Name         string   `json:"name"`
	Description  string   `json:"description"`
	Triggers     []string `json:"triggers"`
	Instructions string   `json:"instructions"`

	Enabled bool `json:"enabled"`

	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}
