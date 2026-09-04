package model

// Memory + MemoryEvent map to chat.user_memories and
// chat.memory_events (migration 014).
//
// The struct shape mirrors the column list exactly so the repo's
// hand-rolled INSERT/SELECT statements stay in 1:1 sync with the DB.
// Field names use Go conventions; JSON tags match the OpenAPI
// surface the frontend consumes.

import (
	"time"

	"github.com/google/uuid"
)

// Scope, type, source, and status enums. Mirrored from the DB CHECK
// constraints / documentation; exported as consts so the service +
// handler layers don't sprinkle string literals.
const (
	MemoryScopeGlobalUser   = "global_user"
	MemoryScopeConversation = "conversation"
	MemoryScopeArtifact     = "artifact"
	MemoryScopeTemporary    = "temporary"

	// MemoryScopeProject — the memory belongs to one project and
	// ScopeID is that project's id. Added in FIX58.
	//
	// The separation is two-way on purpose: a project chat sees only
	// its own project's memories, and a general chat sees only
	// global_user ones. Projects are where people keep one client's
	// work apart from another's, so a memory from project A appearing
	// in project B is not an inconvenience, it is one customer's data
	// surfacing in another customer's work.
	//
	// The read side enforces this in the SQL (GetActiveForInjection),
	// not by filtering after the fetch: a row that is never selected
	// cannot be leaked by a later code path that forgets to filter.
	MemoryScopeProject = "project"

	MemoryTypeExplicitFact         = "explicit_fact"
	MemoryTypeResponsePreference   = "response_preference"
	MemoryTypeOngoingInterest      = "ongoing_interest"
	MemoryTypeConfirmedInstruction = "confirmed_instruction"
	MemoryTypeInferredPreference   = "inferred_preference" // Phase 4
	MemoryTypeArtifactReference    = "artifact_reference"

	MemorySourceUserExplicit        = "user_explicit"
	MemorySourceAssistantInferred   = "assistant_inferred"
	MemorySourceConversationSummary = "conversation_summary"
	MemorySourceArtifactEvent       = "artifact_event"

	MemoryStatusActive              = "active"
	MemoryStatusPendingConfirmation = "pending_confirmation"
	MemoryStatusSuperseded          = "superseded"
	MemoryStatusDeleted             = "deleted"

	MemorySensitivityNormal     = "normal"
	MemorySensitivityRestricted = "restricted"
	MemorySensitivityProhibited = "prohibited"
)

// Memory is one row from chat.user_memories.
//
// `Content` carries the actual remembered text. `NormalizedKey` is
// a lowercased / trimmed / truncated copy used by the service's
// dedup check (ExistsByNormalizedKey). Both are written by the
// service layer — handlers never touch NormalizedKey directly.
type Memory struct {
	ID       uuid.UUID `json:"id"`
	TenantID uuid.UUID `json:"-"`
	UserID   uuid.UUID `json:"-"`

	ScopeType string     `json:"scope_type"`
	ScopeID   *uuid.UUID `json:"scope_id,omitempty"`

	MemoryType string `json:"memory_type"`
	Category   string `json:"category,omitempty"`

	Content       string `json:"content"`
	NormalizedKey string `json:"-"`

	SourceType           string     `json:"source_type"`
	SourceConversationID *uuid.UUID `json:"source_conversation_id,omitempty"`
	SourceMessageID      *uuid.UUID `json:"source_message_id,omitempty"`
	EvidenceExcerpt      string     `json:"evidence_excerpt,omitempty"`

	Status string `json:"status"`

	ConfidenceScore float64 `json:"confidence_score"`
	ImportanceScore float64 `json:"importance_score"`
	IsUserConfirmed bool    `json:"is_user_confirmed"`
	IsPinned        bool    `json:"is_pinned"`

	SensitivityLevel string `json:"sensitivity_level"`

	LastUsedAt *time.Time `json:"last_used_at,omitempty"`
	UseCount   int        `json:"use_count"`

	ValidUntil *time.Time `json:"valid_until,omitempty"`

	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// MemoryEvent is one row from chat.memory_events.
//
// Memory mutations never go straight to the DB — they pair with a
// log entry so the audit endpoint + future support workflows can
// reconstruct why a fact ended up in (or out of) memory.
type MemoryEvent struct {
	ID       uuid.UUID  `json:"id"`
	MemoryID *uuid.UUID `json:"memory_id,omitempty"`
	TenantID uuid.UUID  `json:"-"`
	UserID   uuid.UUID  `json:"-"`

	Action    string `json:"action"`
	ActorType string `json:"actor_type"`

	Reason   string         `json:"reason,omitempty"`
	Metadata map[string]any `json:"metadata,omitempty"`

	CreatedAt time.Time `json:"created_at"`
}
