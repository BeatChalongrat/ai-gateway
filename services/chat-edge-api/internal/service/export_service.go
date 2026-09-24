// ExportService — S9 GDPR-style data export.
//
// Generates a single JSON file containing every memory + memory
// event the user owns, writes it to local storage under
// {root}/exports/, and records the metadata in
// chat.data_export_requests so the download endpoint can serve it
// later. Files expire after exportExpiryWindow; SweepExpired (run
// every exportSweepInterval by StartSweep, FIX132) deletes the file of
// every expired export and marks its row 'expired'. Until FIX132 this
// comment said the RetentionEnforcer did that — it never did, and
// expired export files stayed on disk.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// Errors
var (
	ErrExportDisabled    = errors.New("export: data export is disabled for this tenant")
	ErrExportNotFound    = errors.New("export: not found")
	ErrExportExpired     = errors.New("export: file expired")
	ErrExportFileMissing = errors.New("export: file missing")
)

// exportExpiryWindow is how long an export file stays available
// for download. Long enough for a user to come back tomorrow and
// download it again from the past-exports list, short enough that
// stale files don't pile up.
const exportExpiryWindow = 24 * time.Hour

// exportSweepInterval — how often expired export files are removed, so a
// file outlives its 24-hour window by at most this much.
const exportSweepInterval = time.Hour

// exportStaleAfter — an export still pending/processing this long after
// it was asked for belongs to a process that is gone; it is failed so the
// user can start a new one.
const exportStaleAfter = 2 * time.Hour

type ExportService struct {
	memories    *repository.MemoryRepo
	exports     *repository.DataExportRepo
	settings    *repository.TenantMemorySettingsRepo
	storageRoot string // {root}/exports/ holds the generated files
}

func NewExportService(
	memories *repository.MemoryRepo,
	exports *repository.DataExportRepo,
	settings *repository.TenantMemorySettingsRepo,
	storageRoot string,
) *ExportService {
	if storageRoot == "" {
		storageRoot = "./data"
	}
	return &ExportService{
		memories:    memories,
		exports:     exports,
		settings:    settings,
		storageRoot: storageRoot,
	}
}

// ExportResult is what the handler returns to the client. The
// download URL is built by the handler from ExportID.
type ExportResult struct {
	ExportID  uuid.UUID
	Filename  string
	SizeBytes int64
	ExpiresAt time.Time
}

// ExportUserMemories assembles the JSON blob + persists the
// metadata row. Returns ErrExportDisabled when the tenant has
// turned off exports.
//
// File layout (one JSON object per export):
//
//	{
//	  "export_type": "memories",
//	  "user_id":     "...",
//	  "tenant_id":   "...",
//	  "exported_at": "RFC3339",
//	  "memory_count": N,
//	  "memories":     [...full Memory rows...]
//	}
//
// Memory events are intentionally NOT included in S9 — they're
// audit metadata the user can already see in the events endpoint
// and the cardinality is high; future iterations can add them
// behind an "include_events" flag.
func (s *ExportService) ExportUserMemories(
	ctx context.Context, userID, tenantID uuid.UUID,
) (*ExportResult, error) {
	settings, err := s.settings.Get(ctx, tenantID)
	if err != nil {
		return nil, err
	}
	if !settings.AllowDataExport {
		return nil, ErrExportDisabled
	}

	memories, err := s.memories.ListAllForExport(ctx, userID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list memories for export: %w", err)
	}

	exportedAt := time.Now().UTC()
	payload := map[string]any{
		"export_type":  model.ExportTypeMemories,
		"user_id":      userID.String(),
		"tenant_id":    tenantID.String(),
		"exported_at":  exportedAt.Format(time.RFC3339),
		"memory_count": len(memories),
		"memories":     memories,
	}
	body, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("marshal export payload: %w", err)
	}

	// Storage layout: {root}/exports/{tenant}/{user}/{filename}.
	// The per-tenant + per-user subpath makes it easy to bulk
	// delete during the GDPR purge cascade without parsing
	// filenames.
	dir := filepath.Join(s.storageRoot, "exports", tenantID.String(), userID.String())
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, fmt.Errorf("create export dir: %w", err)
	}
	filename := fmt.Sprintf("memory_export_%s.json", exportedAt.Format("20060102_150405"))
	fullPath := filepath.Join(dir, filename)
	if err := os.WriteFile(fullPath, body, 0o600); err != nil {
		return nil, fmt.Errorf("write export file: %w", err)
	}

	expires := exportedAt.Add(exportExpiryWindow)
	row := &model.DataExportRequest{
		TenantID:      tenantID,
		UserID:        userID,
		ExportType:    model.ExportTypeMemories,
		Status:        model.ExportStatusCompleted,
		FilePath:      fullPath,
		FileSizeBytes: int64(len(body)),
		ExpiresAt:     &expires,
		CompletedAt:   &exportedAt,
	}
	if err := s.exports.Create(ctx, row); err != nil {
		// Best-effort cleanup: the file is on disk but the row
		// failed — unlink so we don't leak storage.
		_ = os.Remove(fullPath)
		return nil, fmt.Errorf("persist export row: %w", err)
	}
	return &ExportResult{
		ExportID:  row.ID,
		Filename:  filename,
		SizeBytes: row.FileSizeBytes,
		ExpiresAt: expires,
	}, nil
}

// OpenForDownload returns the file metadata + an open *os.File
// the handler streams to the client. Verifies ownership +
// expiry; the caller MUST close the returned file.
func (s *ExportService) OpenForDownload(
	ctx context.Context, exportID, userID, tenantID uuid.UUID,
) (*model.DataExportRequest, *os.File, error) {
	row, err := s.exports.GetByID(ctx, exportID, userID, tenantID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, nil, ErrExportNotFound
		}
		return nil, nil, err
	}
	if row.ExpiresAt != nil && time.Now().After(*row.ExpiresAt) {
		return row, nil, ErrExportExpired
	}
	if row.FilePath == "" {
		return row, nil, ErrExportFileMissing
	}
	f, err := os.Open(row.FilePath)
	if err != nil {
		return row, nil, ErrExportFileMissing
	}
	return row, f, nil
}

// ListByUser is a thin pass-through used by the future
// "past exports" UI; today's handler doesn't expose it but the
// method is here so the service layer is complete.
func (s *ExportService) ListByUser(
	ctx context.Context, userID, tenantID uuid.UUID,
) ([]model.DataExportRequest, error) {
	return s.exports.ListByUser(ctx, userID, tenantID)
}

// ExportSweepStats — one sweep's outcome, counts only.
type ExportSweepStats struct {
	FilesRemoved int `json:"files_removed"`
	RowsExpired  int `json:"rows_expired"`
	StaleFailed  int `json:"stale_failed"`
	Errors       int `json:"errors"`
}

// SweepExpired deletes the file of every export past its expiry and
// marks the row expired (the row keeps ids, size and times only), and
// fails exports whose builder vanished. A file already gone counts as
// removed; a file that cannot be removed leaves its row as it is, so
// the next sweep tries again.
func (s *ExportService) SweepExpired(ctx context.Context, now time.Time) ExportSweepStats {
	var st ExportSweepStats
	if n, err := s.exports.FailStale(ctx, now.Add(-exportStaleAfter)); err != nil {
		slog.Warn("export sweep: fail stale failed", "err", err)
		st.Errors++
	} else {
		st.StaleFailed = n
	}
	expired, err := s.exports.ListExpiredWithFiles(ctx, now, 1000)
	if err != nil {
		slog.Warn("export sweep: list failed", "err", err)
		st.Errors++
		return st
	}
	for _, e := range expired {
		if err := os.Remove(e.FilePath); err != nil && !os.IsNotExist(err) {
			slog.Warn("export sweep: remove failed", "export", e.ID.String(), "err", err)
			st.Errors++
			continue
		}
		st.FilesRemoved++
		if err := s.exports.MarkExpired(ctx, e.ID); err != nil {
			slog.Warn("export sweep: mark expired failed", "export", e.ID.String(), "err", err)
			st.Errors++
			continue
		}
		st.RowsExpired++
	}
	if st.FilesRemoved > 0 || st.StaleFailed > 0 || st.Errors > 0 {
		slog.Info("export sweep: pass complete",
			"files_removed", st.FilesRemoved, "rows_expired", st.RowsExpired,
			"stale_failed", st.StaleFailed, "errors", st.Errors)
	}
	return st
}

// StartSweep runs SweepExpired at boot and every exportSweepInterval
// until ctx ends.
func (s *ExportService) StartSweep(ctx context.Context) {
	s.SweepExpired(ctx, time.Now().UTC())
	t := time.NewTicker(exportSweepInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.SweepExpired(ctx, time.Now().UTC())
		}
	}
}
