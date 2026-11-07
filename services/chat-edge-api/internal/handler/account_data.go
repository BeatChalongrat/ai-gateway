// "Your data" handlers — FIX132 C (the user owns their data).
//
// Routes (behind RequireAuth):
//
//	POST /api/v1/account/exports       start "export all my data" (202; an
//	                                   export already running is returned)
//	GET  /api/v1/account/exports       the newest full exports and their status
//	GET  /api/v1/account/data-policy   what the Settings page tells the user:
//	                                   when deleted data is erased, how long
//	                                   backups keep it, how long a download
//	                                   link works, the export caps
//
// The file itself is served by GET /account/exports/{id}/download
// (account.go), owner-checked, for 24 hours.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// DataPolicySource is what the data-policy answer is built from.
type DataPolicySource struct {
	Retention *repository.TenantRetentionSettingsRepo
	// PurgeEnvMode — DELETED_DATA_PURGE_MODE.
	PurgeEnvMode string
	// BackupDays — BACKUP_RETENTION_DAYS.
	BackupDays int
}

// SetFullExport wires "export all my data".
func (h *AccountHandler) SetFullExport(svc *service.AccountExportService) { h.fullExport = svc }

// SetDataPolicy wires the data-policy answer.
func (h *AccountHandler) SetDataPolicy(src DataPolicySource) { h.policy = &src }

type fullExportWire struct {
	ID          string  `json:"id"`
	Status      string  `json:"status"`
	SizeBytes   int64   `json:"size_bytes"`
	RequestedAt string  `json:"requested_at"`
	CompletedAt *string `json:"completed_at,omitempty"`
	ExpiresAt   *string `json:"expires_at,omitempty"`
}

func toFullExportWire(e *model.DataExportRequest) fullExportWire {
	w := fullExportWire{
		ID: e.ID.String(), Status: e.Status, SizeBytes: e.FileSizeBytes,
		RequestedAt: e.RequestedAt.UTC().Format(time.RFC3339),
	}
	if e.CompletedAt != nil {
		s := e.CompletedAt.UTC().Format(time.RFC3339)
		w.CompletedAt = &s
	}
	if e.ExpiresAt != nil {
		s := e.ExpiresAt.UTC().Format(time.RFC3339)
		w.ExpiresAt = &s
		// A completed export past its window reads as expired even before
		// the hourly sweep has marked the row.
		if e.Status == model.ExportStatusCompleted && time.Now().After(*e.ExpiresAt) {
			w.Status = model.ExportStatusExpired
		}
	}
	return w
}

func (h *AccountHandler) StartFullExport(w http.ResponseWriter, r *http.Request) {
	if h.fullExport == nil {
		WriteError(w, http.StatusNotFound, CodeNotFound, "export not available")
		return
	}
	caller := middleware.MustCaller(r.Context())
	row, err := h.fullExport.Start(r.Context(), caller.UserID, caller.TenantUUID)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrExportDisabled):
			WriteError(w, http.StatusForbidden, "EXPORT_DISABLED", "data export is disabled for this organisation")
		case errors.Is(err, service.ErrExportDailyLimit):
			WriteError(w, http.StatusTooManyRequests, "EXPORT_DAILY_LIMIT", "export limit reached for today")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "export failed to start")
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(map[string]any{"export": toFullExportWire(row)})
}

func (h *AccountHandler) ListFullExports(w http.ResponseWriter, r *http.Request) {
	if h.fullExport == nil {
		WriteError(w, http.StatusNotFound, CodeNotFound, "export not available")
		return
	}
	caller := middleware.MustCaller(r.Context())
	rows, err := h.fullExport.List(r.Context(), caller.UserID, caller.TenantUUID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "could not list exports")
		return
	}
	out := make([]fullExportWire, 0, len(rows))
	for i := range rows {
		out = append(out, toFullExportWire(&rows[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"exports": out})
}

func (h *AccountHandler) DataPolicy(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	mode := model.NormalizePurgeMode("")
	days := model.DefaultDeletedPurgeDays
	backup := 14
	if h.policy != nil {
		if h.policy.BackupDays > 0 {
			backup = h.policy.BackupDays
		}
		settings := model.DefaultTenantRetentionSettings(caller.TenantUUID)
		if h.policy.Retention != nil {
			if s, err := h.policy.Retention.Get(r.Context(), caller.TenantUUID); err == nil {
				settings = s
			}
		}
		mode = model.EffectiveDeletedPurgeMode(h.policy.PurgeEnvMode, settings)
		if settings.DeletedConversationPurgeDays > 0 {
			days = settings.DeletedConversationPurgeDays
		}
	}
	var maxBytes int64
	var maxConvs int
	if h.fullExport != nil {
		maxBytes, maxConvs = h.fullExport.Limits()
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		// on | dry_run | off — only "on" erases anything.
		"deleted_data_purge":      mode,
		"deleted_data_purge_days": days,
		"backup_retention_days":   backup,
		"export_link_hours":       24,
		"export_max_file_mb":      maxBytes >> 20,
		"export_max_chats":        maxConvs,
	})
}
