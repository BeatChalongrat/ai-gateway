package handler

// FIX132 C — what the Settings page reads: the data policy (so the UI
// states the real period) and the full export's download as a ZIP.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestFIX132_DataPolicyStatesThePeriodTheSweepUses(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	retention := repository.NewTenantRetentionSettingsRepo(pool)
	tenant := uuid.New()
	get := func(envMode string) map[string]any {
		t.Helper()
		h := NewAccountHandler(nil, nil)
		h.SetDataPolicy(DataPolicySource{Retention: retention, PurgeEnvMode: envMode, BackupDays: 14})
		req := httptest.NewRequest(http.MethodGet, "/account/data-policy", nil)
		req = req.WithContext(middleware.WithCaller(req.Context(), middleware.Caller{UserID: uuid.New(), TenantUUID: tenant}))
		rec := httptest.NewRecorder()
		h.DataPolicy(rec, req)
		var out map[string]any
		_ = json.Unmarshal(rec.Body.Bytes(), &out)
		return out
	}
	if p := get(""); p["deleted_data_purge"] != "on" || p["deleted_data_purge_days"] != float64(30) || p["backup_retention_days"] != float64(14) {
		t.Fatalf("default policy = %v, want on / 30 / 14", p)
	}
	if p := get("off"); p["deleted_data_purge"] != "off" {
		t.Fatalf("env off → %v", p["deleted_data_purge"])
	}
	if err := retention.Upsert(ctx, &model.TenantRetentionSettings{
		TenantID: tenant, DeletedConversationPurgeDays: 45, DryRun: true,
		DeletedPurgeEnabled: true, DeletedPurgeDryRun: true,
	}); err != nil {
		t.Fatal(err)
	}
	if p := get("on"); p["deleted_data_purge"] != "dry_run" || p["deleted_data_purge_days"] != float64(45) {
		t.Fatalf("tenant dry-run 45 days → %v", p)
	}
}

func TestFIX132_FullExportDownloadsAsAZip(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	root := t.TempDir()
	exports := repository.NewDataExportRepo(pool)
	svc := service.NewExportService(repository.NewMemoryRepo(pool), exports,
		repository.NewTenantMemorySettingsRepo(pool), root)
	h := NewAccountHandler(svc, nil)
	user, tenant := uuid.New(), uuid.New()
	path := filepath.Join(root, "x.zip")
	if err := os.WriteFile(path, []byte("PK\x03\x04zip"), 0o600); err != nil {
		t.Fatal(err)
	}
	done := time.Now().UTC()
	expires := done.Add(24 * time.Hour)
	row := &model.DataExportRequest{TenantID: tenant, UserID: user, ExportType: model.ExportTypeFullAccount,
		Status: model.ExportStatusCompleted, FilePath: path, FileSizeBytes: 7, ExpiresAt: &expires, CompletedAt: &done}
	if err := exports.Create(ctx, row); err != nil {
		t.Fatal(err)
	}
	r := chi.NewRouter()
	r.Get("/account/exports/{id}/download", h.DownloadExport)
	req := httptest.NewRequest(http.MethodGet, "/account/exports/"+row.ID.String()+"/download", nil)
	req = req.WithContext(middleware.WithCaller(req.Context(), middleware.Caller{UserID: user, TenantUUID: tenant}))
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK || rec.Header().Get("Content-Type") != "application/zip" {
		t.Fatalf("download = %d %q", rec.Code, rec.Header().Get("Content-Type"))
	}
	if cd := rec.Header().Get("Content-Disposition"); cd != `attachment; filename="sranchat-export-`+done.Format("2006-01-02")+`.zip"` {
		t.Fatalf("Content-Disposition = %q", cd)
	}

	// Another user asking for the same id gets nothing.
	req = httptest.NewRequest(http.MethodGet, "/account/exports/"+row.ID.String()+"/download", nil)
	req = req.WithContext(middleware.WithCaller(req.Context(), middleware.Caller{UserID: uuid.New(), TenantUUID: tenant}))
	rec = httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("another user's download = %d, want 404", rec.Code)
	}
}
