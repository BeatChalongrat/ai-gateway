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
	"time"

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

// ListByUserAndType — the user's newest exports of one type (FIX132:
// the Settings list of "export all my data" runs), capped at limit.
func (r *DataExportRepo) ListByUserAndType(
	ctx context.Context, userID, tenantID uuid.UUID, exportType string, limit int,
) ([]model.DataExportRequest, error) {
	if limit <= 0 || limit > 20 {
		limit = 5
	}
	q := `SELECT ` + exportColumns + `
	      FROM chat.data_export_requests
	      WHERE user_id = $1 AND tenant_id = $2 AND export_type = $3
	      ORDER BY requested_at DESC
	      LIMIT $4`
	rows, err := r.db.Query(ctx, q, userID, tenantID, exportType, limit)
	if err != nil {
		return nil, fmt.Errorf("list exports by type: %w", err)
	}
	defer rows.Close()
	out := make([]model.DataExportRequest, 0)
	for rows.Next() {
		var e model.DataExportRequest
		if err := scanExport(rows, &e); err != nil {
			return nil, fmt.Errorf("list exports by type scan: %w", err)
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// CountRequestedSince — how many exports of one type the user asked for
// since t (the daily cap on "export all my data").
func (r *DataExportRepo) CountRequestedSince(
	ctx context.Context, userID uuid.UUID, exportType string, since time.Time,
) (int, error) {
	var n int
	if err := r.db.QueryRow(ctx, `
		SELECT count(*) FROM chat.data_export_requests
		WHERE user_id = $1 AND export_type = $2 AND requested_at >= $3`,
		userID, exportType, since,
	).Scan(&n); err != nil {
		return 0, fmt.Errorf("count recent exports: %w", err)
	}
	return n, nil
}

// SetStatus moves an export along pending → processing → failed.
func (r *DataExportRepo) SetStatus(ctx context.Context, id uuid.UUID, status string) error {
	q := `UPDATE chat.data_export_requests SET status = $2 WHERE id = $1`
	if status == model.ExportStatusFailed {
		q = `UPDATE chat.data_export_requests SET status = $2, completed_at = NOW() WHERE id = $1`
	}
	if _, err := r.db.Exec(ctx, q, id, status); err != nil {
		return fmt.Errorf("set export status: %w", err)
	}
	return nil
}

// Complete records a finished export: its file, size and expiry.
func (r *DataExportRepo) Complete(
	ctx context.Context, id uuid.UUID, filePath string, size int64, expiresAt time.Time,
) error {
	if _, err := r.db.Exec(ctx, `
		UPDATE chat.data_export_requests
		SET status = 'completed', file_path = $2, file_size_bytes = $3,
		    expires_at = $4, completed_at = NOW()
		WHERE id = $1`, id, filePath, size, expiresAt); err != nil {
		return fmt.Errorf("complete export: %w", err)
	}
	return nil
}

// ExpiredFile — an export whose download window has closed but whose
// file is still on disk.
type ExpiredFile struct {
	ID       uuid.UUID
	FilePath string
}

// ListExpiredWithFiles returns exports past expires_at that still carry
// a file path (FIX132 — the nightly sweep removes the file, then
// MarkExpired clears the path). Bounded per pass.
func (r *DataExportRepo) ListExpiredWithFiles(
	ctx context.Context, now time.Time, limit int,
) ([]ExpiredFile, error) {
	if limit <= 0 {
		limit = 1000
	}
	rows, err := r.db.Query(ctx, `
		SELECT id, file_path FROM chat.data_export_requests
		WHERE file_path IS NOT NULL AND expires_at IS NOT NULL AND expires_at < $1
		ORDER BY expires_at ASC
		LIMIT $2`, now, limit)
	if err != nil {
		return nil, fmt.Errorf("list expired exports: %w", err)
	}
	defer rows.Close()
	out := make([]ExpiredFile, 0)
	for rows.Next() {
		var e ExpiredFile
		if err := rows.Scan(&e.ID, &e.FilePath); err != nil {
			return nil, fmt.Errorf("scan expired export: %w", err)
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// MarkExpired records that an export's file is gone: status 'expired',
// no file path. The row stays (ids, sizes and times only) so the
// Settings list can say "expired" instead of silently dropping it.
func (r *DataExportRepo) MarkExpired(ctx context.Context, id uuid.UUID) error {
	if _, err := r.db.Exec(ctx, `
		UPDATE chat.data_export_requests
		SET status = 'expired', file_path = NULL
		WHERE id = $1`, id); err != nil {
		return fmt.Errorf("mark export expired: %w", err)
	}
	return nil
}

// FailStale marks exports still pending/processing after the cutoff as
// failed — the process that was building them is gone (restart, crash),
// and a row left "pending" forever would block the user's next export.
func (r *DataExportRepo) FailStale(ctx context.Context, cutoff time.Time) (int, error) {
	tag, err := r.db.Exec(ctx, `
		UPDATE chat.data_export_requests
		SET status = 'failed', completed_at = NOW()
		WHERE status IN ('pending', 'processing') AND requested_at < $1`, cutoff)
	if err != nil {
		return 0, fmt.Errorf("fail stale exports: %w", err)
	}
	return int(tag.RowsAffected()), nil
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
