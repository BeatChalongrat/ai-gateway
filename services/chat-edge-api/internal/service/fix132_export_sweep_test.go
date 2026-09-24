package service

// FIX132 C — an export file lives for its 24-hour download window and is
// then removed. export_service.go said the RetentionEnforcer swept them;
// nothing did, so every export file stayed on disk for good.

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func TestExportSweep_RemovesExpiredFilesAndFailsStaleBuilds(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()
	root := t.TempDir()
	exports := repository.NewDataExportRepo(pool)
	svc := NewExportService(repository.NewMemoryRepo(pool), exports,
		repository.NewTenantMemorySettingsRepo(pool), root)

	tenant, user := uuid.New(), uuid.New()
	now := time.Now().UTC()
	mk := func(name string, expires time.Time) (*model.DataExportRequest, string) {
		t.Helper()
		path := filepath.Join(root, "exports", tenant.String(), user.String(), name)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("{}"), 0o600); err != nil {
			t.Fatal(err)
		}
		done := expires.Add(-exportExpiryWindow)
		row := &model.DataExportRequest{
			TenantID: tenant, UserID: user, ExportType: model.ExportTypeMemories,
			Status: model.ExportStatusCompleted, FilePath: path, FileSizeBytes: 2,
			ExpiresAt: &expires, CompletedAt: &done,
		}
		if err := exports.Create(ctx, row); err != nil {
			t.Fatalf("seed export: %v", err)
		}
		return row, path
	}
	old, oldPath := mk("old.json", now.Add(-time.Hour))     // window closed an hour ago
	fresh, freshPath := mk("fresh.json", now.Add(time.Hour)) // still downloadable

	// A build that started three hours ago and never finished.
	stale := &model.DataExportRequest{TenantID: tenant, UserID: user,
		ExportType: model.ExportTypeFullAccount, Status: model.ExportStatusPending}
	if err := exports.Create(ctx, stale); err != nil {
		t.Fatalf("seed stale: %v", err)
	}
	if _, err := pool.Exec(ctx,
		`UPDATE chat.data_export_requests SET requested_at = NOW() - interval '3 hours' WHERE id = $1`,
		stale.ID); err != nil {
		t.Fatal(err)
	}

	st := svc.SweepExpired(ctx, now)

	if _, err := os.Stat(oldPath); !os.IsNotExist(err) {
		t.Errorf("expired export file still on disk (stat err=%v)", err)
	}
	if _, err := os.Stat(freshPath); err != nil {
		t.Errorf("an export inside its window was removed: %v", err)
	}
	got, err := exports.GetByID(ctx, old.ID, user, tenant)
	if err != nil || got.Status != model.ExportStatusExpired || got.FilePath != "" {
		t.Errorf("expired row = %+v (err=%v), want status expired and no path", got, err)
	}
	if got, _ := exports.GetByID(ctx, fresh.ID, user, tenant); got.Status != model.ExportStatusCompleted {
		t.Errorf("fresh row status = %q, want completed", got.Status)
	}
	if got, _ := exports.GetByID(ctx, stale.ID, user, tenant); got.Status != model.ExportStatusFailed {
		t.Errorf("stale build status = %q, want failed", got.Status)
	}
	if st.FilesRemoved != 1 || st.StaleFailed != 1 {
		t.Errorf("stats = %+v, want 1 removed, 1 stale failed", st)
	}
}
