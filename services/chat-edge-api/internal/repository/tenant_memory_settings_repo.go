// TenantMemorySettingsRepo persists chat.tenant_memory_settings
// (S9 — Enterprise Hardening).
//
// Get returns model.DefaultTenantMemorySettings when no row exists
// so callers don't have to branch on missing-row. Upsert writes
// the override row.
package repository

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type TenantMemorySettingsRepo struct {
	db DB
}

func NewTenantMemorySettingsRepo(db *pgxpool.Pool) *TenantMemorySettingsRepo {
	return &TenantMemorySettingsRepo{db: db}
}

func (r *TenantMemorySettingsRepo) WithTx(tx pgx.Tx) *TenantMemorySettingsRepo {
	return &TenantMemorySettingsRepo{db: tx}
}

const tenantSettingsColumns = `
	tenant_id,
	memory_enabled, max_memories_per_user, retention_days,
	preference_learning_enabled, allow_data_export, allow_data_deletion,
	created_at, updated_at
`

func scanTenantSettings(row pgx.Row, s *model.TenantMemorySettings) error {
	return row.Scan(
		&s.TenantID,
		&s.MemoryEnabled, &s.MaxMemoriesPerUser, &s.RetentionDays,
		&s.PreferenceLearningEnabled, &s.AllowDataExport, &s.AllowDataDeletion,
		&s.CreatedAt, &s.UpdatedAt,
	)
}

// Get returns the row, or model.DefaultTenantMemorySettings when
// none exists. NEVER returns ErrNotFound — defaults are the
// contract. Callers can therefore use the result unconditionally.
func (r *TenantMemorySettingsRepo) Get(
	ctx context.Context, tenantID uuid.UUID,
) (*model.TenantMemorySettings, error) {
	q := `SELECT ` + tenantSettingsColumns + `
	      FROM chat.tenant_memory_settings
	      WHERE tenant_id = $1`
	s := &model.TenantMemorySettings{}
	if err := scanTenantSettings(r.db.QueryRow(ctx, q, tenantID), s); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return model.DefaultTenantMemorySettings(tenantID), nil
		}
		return nil, fmt.Errorf("get tenant settings: %w", err)
	}
	return s, nil
}

// Upsert writes the override row.
func (r *TenantMemorySettingsRepo) Upsert(
	ctx context.Context, s *model.TenantMemorySettings,
) error {
	const q = `
		INSERT INTO chat.tenant_memory_settings (
			tenant_id, memory_enabled, max_memories_per_user, retention_days,
			preference_learning_enabled, allow_data_export, allow_data_deletion
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (tenant_id) DO UPDATE SET
			memory_enabled              = EXCLUDED.memory_enabled,
			max_memories_per_user       = EXCLUDED.max_memories_per_user,
			retention_days              = EXCLUDED.retention_days,
			preference_learning_enabled = EXCLUDED.preference_learning_enabled,
			allow_data_export           = EXCLUDED.allow_data_export,
			allow_data_deletion         = EXCLUDED.allow_data_deletion,
			updated_at                  = NOW()
	`
	if _, err := r.db.Exec(ctx, q,
		s.TenantID, s.MemoryEnabled, s.MaxMemoriesPerUser, s.RetentionDays,
		s.PreferenceLearningEnabled, s.AllowDataExport, s.AllowDataDeletion,
	); err != nil {
		return fmt.Errorf("upsert tenant settings: %w", err)
	}
	return nil
}

// ListWithRetention returns tenants whose retention_days is set to
// a positive integer — the RetentionEnforcer's nightly scan target.
// Tenants without an override row don't appear here (defaults have
// retention_days=NULL → no expiry).
func (r *TenantMemorySettingsRepo) ListWithRetention(
	ctx context.Context,
) ([]model.TenantMemorySettings, error) {
	q := `SELECT ` + tenantSettingsColumns + `
	      FROM chat.tenant_memory_settings
	      WHERE retention_days IS NOT NULL AND retention_days > 0
	        AND memory_enabled = true`
	rows, err := r.db.Query(ctx, q)
	if err != nil {
		return nil, fmt.Errorf("list with retention: %w", err)
	}
	defer rows.Close()
	out := make([]model.TenantMemorySettings, 0)
	for rows.Next() {
		var s model.TenantMemorySettings
		if err := scanTenantSettings(rows, &s); err != nil {
			return nil, fmt.Errorf("list with retention scan: %w", err)
		}
		out = append(out, s)
	}
	return out, rows.Err()
}
