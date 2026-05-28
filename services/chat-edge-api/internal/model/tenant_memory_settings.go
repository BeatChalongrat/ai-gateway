package model

// TenantMemorySettings maps to chat.tenant_memory_settings
// (migration 018 — S9 Enterprise Hardening).
//
// Per-tenant override row for the memory layer's behaviour. The
// repository's Get returns sensible defaults when no row exists so
// callers don't have to branch — tenants only need a row when
// something is being overridden.
//
// All retention math lives here; the RetentionEnforcer goroutine
// reads RetentionDays nightly.

import (
	"time"

	"github.com/google/uuid"
)

// Default values for tenants without a stored row. Mirror the
// column defaults in migration 018 so the repo's defaulting and
// the SQL DEFAULT clauses agree.
const (
	DefaultMaxMemoriesPerUser = 200
)

type TenantMemorySettings struct {
	TenantID uuid.UUID `json:"tenant_id"`

	MemoryEnabled              bool `json:"memory_enabled"`
	MaxMemoriesPerUser         int  `json:"max_memories_per_user"`
	RetentionDays              *int `json:"retention_days,omitempty"`
	PreferenceLearningEnabled  bool `json:"preference_learning_enabled"`
	AllowDataExport            bool `json:"allow_data_export"`
	AllowDataDeletion          bool `json:"allow_data_deletion"`

	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// DefaultTenantMemorySettings returns the in-memory "no override"
// shape used when no row exists. Matches the SQL DEFAULTs verbatim.
func DefaultTenantMemorySettings(tenantID uuid.UUID) *TenantMemorySettings {
	return &TenantMemorySettings{
		TenantID:                  tenantID,
		MemoryEnabled:             true,
		MaxMemoriesPerUser:        DefaultMaxMemoriesPerUser,
		RetentionDays:             nil,
		PreferenceLearningEnabled: true,
		AllowDataExport:           true,
		AllowDataDeletion:         true,
	}
}
