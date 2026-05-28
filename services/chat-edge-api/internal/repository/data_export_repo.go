// DataExportRepo persists chat.data_export_requests (S9).
//
// Audit + retrieval surface for user-initiated exports. The actual
// JSON file lives on local storage at FilePath; this repo only
// owns the metadata row.
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

type DataExportRepo struct {
	db DB
}

func NewDataExportRepo(db *pgxpool.Pool) *DataExportRepo {
	return &DataExportRepo{db: db}
}

const exportColumns = `
	id, tenant_id, user_id, export_type, status,
	file_path, file_size_bytes, expires_at,
	requested_at, completed_at
`

func scanExport(row pgx.Row, e *model.DataExportRequest) error {
	var filePath *string
	if err := row.Scan(
		&e.ID, &e.TenantID, &e.UserID, &e.ExportType, &e.Status,
		&filePath, &e.FileSizeBytes, &e.ExpiresAt,
		&e.RequestedAt, &e.CompletedAt,
	); err != nil {
		return err
	}
	if filePath != nil {
		e.FilePath = *filePath
	}
	return nil
}

// Create inserts a new export request. Caller pre-populates the
// identity + scope + file metadata fields; we persist + stamp the
// generated id/timestamps.
func (r *DataExportRepo) Create(
	ctx context.Context, e *model.DataExportRequest,
) error {
	const q = `
		INSERT INTO chat.data_export_requests
			(tenant_id, user_id, export_type, status, file_path,
			 file_size_bytes, expires_at, completed_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		RETURNING id, requested_at
	`
	var filePath any
	if e.FilePath != "" {
		filePath = e.FilePath
	}
	return r.db.QueryRow(ctx, q,
		e.TenantID, e.UserID, e.ExportType, e.Status, filePath,
		e.FileSizeBytes, e.ExpiresAt, e.CompletedAt,
	).Scan(&e.ID, &e.RequestedAt)
}

// GetByID returns one export row, scoped by user_id + tenant_id
// so a leaked id from another tenant returns ErrNotFound.
func (r *DataExportRepo) GetByID(
	ctx context.Context, id, userID, tenantID uuid.UUID,
) (*model.DataExportRequest, error) {
	q := `SELECT ` + exportColumns + `
	      FROM chat.data_export_requests
	      WHERE id = $1 AND user_id = $2 AND tenant_id = $3`
	e := &model.DataExportRequest{}
	if err := scanExport(r.db.QueryRow(ctx, q, id, userID, tenantID), e); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get export: %w", err)
	}
	return e, nil
}

// ListByUser returns the user's recent export history. Capped at
// 20 — the surface is a small "your past exports" list, not a
// data-browsing tool.
func (r *DataExportRepo) ListByUser(
	ctx context.Context, userID, tenantID uuid.UUID,
) ([]model.DataExportRequest, error) {
	q := `SELECT ` + exportColumns + `
	      FROM chat.data_export_requests
	      WHERE user_id = $1 AND tenant_id = $2
	      ORDER BY requested_at DESC
	      LIMIT 20`
	rows, err := r.db.Query(ctx, q, userID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list exports: %w", err)
	}
	defer rows.Close()
	out := make([]model.DataExportRequest, 0)
	for rows.Next() {
		var e model.DataExportRequest
		if err := scanExport(rows, &e); err != nil {
			return nil, fmt.Errorf("list exports scan: %w", err)
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// DeleteByUser hard-deletes every export row for the user. Called
// from the GDPR purge path — the related files are unlinked by
// the PurgeService before the rows go away.
func (r *DataExportRepo) DeleteByUser(
	ctx context.Context, userID, tenantID uuid.UUID,
) (int, error) {
	const q = `
		DELETE FROM chat.data_export_requests
		WHERE user_id = $1 AND tenant_id = $2
	`
	tag, err := r.db.Exec(ctx, q, userID, tenantID)
	if err != nil {
		return 0, fmt.Errorf("delete exports by user: %w", err)
	}
	return int(tag.RowsAffected()), nil
}
