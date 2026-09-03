package model

// Retention & deletion program (docs/DATA_RETENTION_PLAN.md).
//
// TenantRetentionSettings maps to chat.tenant_retention_settings
// (migration 040). DeletionAuditEntry maps to chat.deletion_audit —
// the durable evidence row every destructive action and every
// retention-setting change writes. Audit rows carry ids, kinds and
// COUNTS only, never deleted content.

import (
	"time"

	"github.com/google/uuid"
)

// TranscriptRetentionMinDays is the floor for
// transcript_retention_days. Enforced three times on purpose: here
// (API validation), in the DB CHECK constraint (migration 040), and
// in the enforcer (refuses to sweep with a shorter window even if a
// row somehow carries one). Matching the enterprise baseline: an
// admin may not configure transcript retention shorter than 30 days.
const TranscriptRetentionMinDays = 30

type TenantRetentionSettings struct {
	TenantID uuid.UUID `json:"tenant_id"`

	// TranscriptRetentionEnabled turns the nightly transcript sweep on
	// for this tenant. Default false.
	TranscriptRetentionEnabled bool `json:"transcript_retention_enabled"`
	// TranscriptRetentionDays — conversations with no activity for this
	// many days are hard-deleted by the sweep. NULL = keep forever.
	// Floor: TranscriptRetentionMinDays.
	TranscriptRetentionDays *int `json:"transcript_retention_days,omitempty"`
	// DeletedConversationPurgeDays — a user-deleted (soft-deleted)
	// conversation is hard-deleted this many days after the user
	// deleted it. The user-visible removal is immediate; this is the
	// back-end erasure window.
	DeletedConversationPurgeDays int `json:"deleted_conversation_purge_days"`
	// DryRun — when true (the default) the sweep only COUNTS what it
	// would delete and writes a dry_run audit entry; nothing is
	// removed. A human turns this off after reading the numbers.
	DryRun bool `json:"dry_run"`

	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// DefaultTenantRetentionSettings is the no-row shape: retention off,
// dry-run on, 30-day purge window for user-deleted conversations.
func DefaultTenantRetentionSettings(tenantID uuid.UUID) *TenantRetentionSettings {
	return &TenantRetentionSettings{
		TenantID:                     tenantID,
		TranscriptRetentionEnabled:   false,
		TranscriptRetentionDays:      nil,
		DeletedConversationPurgeDays: 30,
		DryRun:                       true,
	}
}

// Deletion-audit action names. Closed vocabulary — the admin surface
// filters on these.
const (
	AuditActionRetentionSettingsChanged = "retention_settings_changed"
	AuditActionConversationSoftDeleted  = "conversation_soft_deleted"
	AuditActionConversationHardDeleted  = "conversation_hard_deleted"
	AuditActionMemoryPurge              = "memory_purge"
	AuditActionFileDeleted              = "file_deleted"
	AuditActionRetentionDryRun          = "retention_dry_run"
)

// Deletion-audit rule names — why the action happened.
const (
	AuditRuleRetentionExpired = "retention_expired"
	AuditRuleUserRequest      = "user_request"
	AuditRuleAdminRequest     = "admin_request"
	AuditRuleSettingsChange   = "settings_change"
	AuditRuleDryRun           = "dry_run"
)

type DeletionAuditEntry struct {
	ID         uuid.UUID  `json:"id"`
	TenantID   uuid.UUID  `json:"tenant_id"`
	UserID     *uuid.UUID `json:"user_id,omitempty"`
	Actor      string     `json:"actor"`
	Action     string     `json:"action"`
	Scope      string     `json:"scope"`
	ResourceID *uuid.UUID `json:"resource_id,omitempty"`
	Rule       string     `json:"rule"`
	// Counts: {kind: n} — e.g. {"messages": 42, "files": 3}. Never
	// content.
	Counts map[string]int `json:"counts"`
	// Details: setting changes (old/new numbers and flags) — never
	// content.
	Details   map[string]any `json:"details,omitempty"`
	CreatedAt time.Time      `json:"created_at"`
}
