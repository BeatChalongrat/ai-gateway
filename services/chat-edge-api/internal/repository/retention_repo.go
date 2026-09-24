// Retention & deletion program repositories (migration 040):
//
//   - TenantRetentionSettingsRepo — chat.tenant_retention_settings.
//     Get returns defaults when no row exists (retention off, dry-run
//     on) so callers never branch on missing-row.
//   - DeletionAuditRepo — chat.deletion_audit, the durable evidence
//     row every destructive action and setting change writes. This
//     repo only INSERTs and reads; nothing in this service deletes
//     from the table (the audit must survive the deletions it
//     records).
package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// ---------------------------------------------------------------------------
// TenantRetentionSettingsRepo
// ---------------------------------------------------------------------------

type TenantRetentionSettingsRepo struct {
	db DB
}

func NewTenantRetentionSettingsRepo(db *pgxpool.Pool) *TenantRetentionSettingsRepo {
	return &TenantRetentionSettingsRepo{db: db}
}

const retentionSettingsColumns = `
	tenant_id,
	transcript_retention_enabled, transcript_retention_days,
	deleted_conversation_purge_days, dry_run,
	deleted_purge_enabled, deleted_purge_dry_run,
	created_at, updated_at
`

func scanRetentionSettings(row pgx.Row, s *model.TenantRetentionSettings) error {
	return row.Scan(
		&s.TenantID,
		&s.TranscriptRetentionEnabled, &s.TranscriptRetentionDays,
		&s.DeletedConversationPurgeDays, &s.DryRun,
		&s.DeletedPurgeEnabled, &s.DeletedPurgeDryRun,
		&s.CreatedAt, &s.UpdatedAt,
	)
}

// Get returns the row, or the defaults (inactive-transcript retention
// off and dry-run; deleted-data purge on, 30 days) when none exists.
// Never ErrNotFound.
func (r *TenantRetentionSettingsRepo) Get(
	ctx context.Context, tenantID uuid.UUID,
) (*model.TenantRetentionSettings, error) {
	q := `SELECT ` + retentionSettingsColumns + `
	      FROM chat.tenant_retention_settings
	      WHERE tenant_id = $1`
	s := &model.TenantRetentionSettings{}
	if err := scanRetentionSettings(r.db.QueryRow(ctx, q, tenantID), s); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return model.DefaultTenantRetentionSettings(tenantID), nil
		}
		return nil, fmt.Errorf("get retention settings: %w", err)
	}
	return s, nil
}

// ErrRetentionBelowFloor — transcript_retention_days below the
// 30-day floor. The API layer maps this to a 400.
var ErrRetentionBelowFloor = errors.New(
	"transcript_retention_days below the 30-day floor")

// Upsert writes the settings row. The 30-day floor is validated here
// (and again by the DB CHECK) so no caller can slip a shorter window
// through.
func (r *TenantRetentionSettingsRepo) Upsert(
	ctx context.Context, s *model.TenantRetentionSettings,
) error {
	if s.TranscriptRetentionDays != nil &&
		*s.TranscriptRetentionDays < model.TranscriptRetentionMinDays {
		return ErrRetentionBelowFloor
	}
	if s.DeletedConversationPurgeDays < 1 {
		return fmt.Errorf("deleted_conversation_purge_days must be >= 1")
	}
	const q = `
		INSERT INTO chat.tenant_retention_settings (
			tenant_id, transcript_retention_enabled, transcript_retention_days,
			deleted_conversation_purge_days, dry_run,
			deleted_purge_enabled, deleted_purge_dry_run
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (tenant_id) DO UPDATE SET
			transcript_retention_enabled    = EXCLUDED.transcript_retention_enabled,
			transcript_retention_days       = EXCLUDED.transcript_retention_days,
			deleted_conversation_purge_days = EXCLUDED.deleted_conversation_purge_days,
			dry_run                         = EXCLUDED.dry_run,
			deleted_purge_enabled           = EXCLUDED.deleted_purge_enabled,
			deleted_purge_dry_run           = EXCLUDED.deleted_purge_dry_run,
			updated_at                      = NOW()
	`
	if _, err := r.db.Exec(ctx, q,
		s.TenantID, s.TranscriptRetentionEnabled, s.TranscriptRetentionDays,
		s.DeletedConversationPurgeDays, s.DryRun,
		s.DeletedPurgeEnabled, s.DeletedPurgeDryRun,
	); err != nil {
		return fmt.Errorf("upsert retention settings: %w", err)
	}
	return nil
}

// ListEnabled returns every tenant whose inactive-transcript rule
// (retention_expired) is turned on, dry-run or live. The deleted-data
// purge does NOT use this list: it runs for every tenant (FIX132).
func (r *TenantRetentionSettingsRepo) ListEnabled(
	ctx context.Context,
) ([]model.TenantRetentionSettings, error) {
	q := `SELECT ` + retentionSettingsColumns + `
	      FROM chat.tenant_retention_settings
	      WHERE transcript_retention_enabled = true`
	rows, err := r.db.Query(ctx, q)
	if err != nil {
		return nil, fmt.Errorf("list enabled retention: %w", err)
	}
	defer rows.Close()
	out := make([]model.TenantRetentionSettings, 0)
	for rows.Next() {
		var s model.TenantRetentionSettings
		if err := scanRetentionSettings(rows, &s); err != nil {
			return nil, fmt.Errorf("list enabled retention scan: %w", err)
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

// ---------------------------------------------------------------------------
// DeletionAuditRepo
// ---------------------------------------------------------------------------

type DeletionAuditRepo struct {
	db DB
}

func NewDeletionAuditRepo(db *pgxpool.Pool) *DeletionAuditRepo {
	return &DeletionAuditRepo{db: db}
}

// Insert writes one audit entry. Counts and details are marshalled
// to jsonb; nil maps become empty objects. Failure is returned to
// the caller — destructive callers decide whether an audit-write
// failure aborts the action (the transcript enforcer aborts; see
// its doc comment).
func (r *DeletionAuditRepo) Insert(
	ctx context.Context, e *model.DeletionAuditEntry,
) error {
	counts := e.Counts
	if counts == nil {
		counts = map[string]int{}
	}
	details := e.Details
	if details == nil {
		details = map[string]any{}
	}
	countsJSON, err := json.Marshal(counts)
	if err != nil {
		return fmt.Errorf("marshal audit counts: %w", err)
	}
	detailsJSON, err := json.Marshal(details)
	if err != nil {
		return fmt.Errorf("marshal audit details: %w", err)
	}
	const q = `
		INSERT INTO chat.deletion_audit
			(tenant_id, user_id, actor, action, scope, resource_id, rule, counts, details)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
	`
	if _, err := r.db.Exec(ctx, q,
		e.TenantID, e.UserID, e.Actor, e.Action, e.Scope,
		e.ResourceID, e.Rule, countsJSON, detailsJSON,
	); err != nil {
		return fmt.Errorf("insert deletion audit: %w", err)
	}
	return nil
}

// ListByTenant returns the newest entries for a tenant, optionally
// filtered by action. Read path for the admin surface.
func (r *DeletionAuditRepo) ListByTenant(
	ctx context.Context, tenantID uuid.UUID, action string, limit int,
) ([]model.DeletionAuditEntry, error) {
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	q := `SELECT id, tenant_id, user_id, actor, action, scope, resource_id,
	             rule, counts, details, created_at
	      FROM chat.deletion_audit
	      WHERE tenant_id = $1 AND ($2 = '' OR action = $2)
	      ORDER BY created_at DESC
	      LIMIT $3`
	rows, err := r.db.Query(ctx, q, tenantID, action, limit)
	if err != nil {
		return nil, fmt.Errorf("list deletion audit: %w", err)
	}
	defer rows.Close()
	out := make([]model.DeletionAuditEntry, 0)
	for rows.Next() {
		var e model.DeletionAuditEntry
		var countsJSON, detailsJSON []byte
		if err := rows.Scan(
			&e.ID, &e.TenantID, &e.UserID, &e.Actor, &e.Action, &e.Scope,
			&e.ResourceID, &e.Rule, &countsJSON, &detailsJSON, &e.CreatedAt,
		); err != nil {
			return nil, fmt.Errorf("list deletion audit scan: %w", err)
		}
		_ = json.Unmarshal(countsJSON, &e.Counts)
		_ = json.Unmarshal(detailsJSON, &e.Details)
		out = append(out, e)
	}
	return out, rows.Err()
}
