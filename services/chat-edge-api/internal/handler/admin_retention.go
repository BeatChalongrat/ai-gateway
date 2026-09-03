// RetentionAdminHandler — the admin surface for the retention &
// deletion program (docs/DATA_RETENTION_PLAN.md).
//
// Mounted behind the same shared-secret gate as the rest of /admin
// (RequireAdmin), proxied server-side by the admin UI. Four
// operations:
//
//	GET  /admin/retention-settings?tenant_id=   current settings (or defaults)
//	PUT  /admin/retention-settings              update; floor-validated; AUDITED
//	GET  /admin/deletion-audit?tenant_id=       newest audit entries (counts only)
//	GET  /admin/retention-preview?tenant_id=    dry-run numbers, deletes nothing
//
// Setting changes take effect at the next scheduled run (00:00 UTC),
// never immediately — the response carries effective_at so the UI
// can say exactly when.
package handler

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type RetentionAdminHandler struct {
	settings *repository.TenantRetentionSettingsRepo
	audit    *repository.DeletionAuditRepo
	sweeper  *service.TranscriptRetentionService
}

func NewRetentionAdminHandler(
	settings *repository.TenantRetentionSettingsRepo,
	audit *repository.DeletionAuditRepo,
	sweeper *service.TranscriptRetentionService,
) *RetentionAdminHandler {
	return &RetentionAdminHandler{settings: settings, audit: audit, sweeper: sweeper}
}

func nextMidnightUTC() time.Time {
	now := time.Now().UTC()
	return time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC).
		AddDate(0, 0, 1)
}

func tenantIDParam(w http.ResponseWriter, r *http.Request) (uuid.UUID, bool) {
	raw := r.URL.Query().Get("tenant_id")
	if raw == "" {
		http.Error(w, `{"error":"tenant_id query param required"}`, http.StatusBadRequest)
		return uuid.Nil, false
	}
	tid, err := uuid.Parse(raw)
	if err != nil {
		http.Error(w, `{"error":"invalid tenant_id"}`, http.StatusBadRequest)
		return uuid.Nil, false
	}
	return tid, true
}

func (h *RetentionAdminHandler) GetSettings(w http.ResponseWriter, r *http.Request) {
	tid, ok := tenantIDParam(w, r)
	if !ok {
		return
	}
	s, err := h.settings.Get(r.Context(), tid)
	if err != nil {
		http.Error(w, `{"error":"settings lookup failed"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"settings":     s,
		"min_days":     model.TranscriptRetentionMinDays,
		"effective_at": nextMidnightUTC().Format(time.RFC3339),
	})
}

type putRetentionSettingsBody struct {
	TenantID                     string `json:"tenant_id"`
	TranscriptRetentionEnabled   bool   `json:"transcript_retention_enabled"`
	TranscriptRetentionDays      *int   `json:"transcript_retention_days"`
	DeletedConversationPurgeDays int    `json:"deleted_conversation_purge_days"`
	DryRun                       bool   `json:"dry_run"`
}

func (h *RetentionAdminHandler) PutSettings(w http.ResponseWriter, r *http.Request) {
	var body putRetentionSettingsBody
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	tid, err := uuid.Parse(body.TenantID)
	if err != nil {
		http.Error(w, `{"error":"invalid tenant_id"}`, http.StatusBadRequest)
		return
	}
	if body.DeletedConversationPurgeDays == 0 {
		body.DeletedConversationPurgeDays = 30
	}

	before, err := h.settings.Get(r.Context(), tid)
	if err != nil {
		http.Error(w, `{"error":"settings lookup failed"}`, http.StatusInternalServerError)
		return
	}

	next := &model.TenantRetentionSettings{
		TenantID:                     tid,
		TranscriptRetentionEnabled:   body.TranscriptRetentionEnabled,
		TranscriptRetentionDays:      body.TranscriptRetentionDays,
		DeletedConversationPurgeDays: body.DeletedConversationPurgeDays,
		DryRun:                       body.DryRun,
	}
	if err := h.settings.Upsert(r.Context(), next); err != nil {
		if err == repository.ErrRetentionBelowFloor {
			http.Error(w,
				`{"error":"transcript_retention_days below the 30-day floor"}`,
				http.StatusBadRequest)
			return
		}
		http.Error(w, `{"error":"settings update failed"}`, http.StatusInternalServerError)
		return
	}

	// Every retention-setting change is audited: old and new values
	// (numbers and flags only), who, when. A failed audit write is
	// surfaced as an error to the admin — the change stands, but the
	// admin must know the record is missing.
	auditErr := h.audit.Insert(r.Context(), &model.DeletionAuditEntry{
		TenantID: tid,
		Actor:    "admin",
		Action:   model.AuditActionRetentionSettingsChanged,
		Scope:    "tenant",
		Rule:     model.AuditRuleSettingsChange,
		Details: map[string]any{
			"before": map[string]any{
				"enabled":    before.TranscriptRetentionEnabled,
				"days":       before.TranscriptRetentionDays,
				"purge_days": before.DeletedConversationPurgeDays,
				"dry_run":    before.DryRun,
			},
			"after": map[string]any{
				"enabled":    next.TranscriptRetentionEnabled,
				"days":       next.TranscriptRetentionDays,
				"purge_days": next.DeletedConversationPurgeDays,
				"dry_run":    next.DryRun,
			},
		},
	})

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"settings":     next,
		"effective_at": nextMidnightUTC().Format(time.RFC3339),
		"audit_ok":     auditErr == nil,
	})
}

func (h *RetentionAdminHandler) ListAudit(w http.ResponseWriter, r *http.Request) {
	tid, ok := tenantIDParam(w, r)
	if !ok {
		return
	}
	entries, err := h.audit.ListByTenant(
		r.Context(), tid, r.URL.Query().Get("action"), 200)
	if err != nil {
		http.Error(w, `{"error":"audit lookup failed"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"entries": entries})
}

// Preview — the numbers a live pass would delete for this tenant
// RIGHT NOW. Deletes nothing, writes nothing.
func (h *RetentionAdminHandler) Preview(w http.ResponseWriter, r *http.Request) {
	tid, ok := tenantIDParam(w, r)
	if !ok {
		return
	}
	counts, err := h.sweeper.PreviewTenant(r.Context(), tid)
	if err != nil {
		http.Error(w, `{"error":"preview failed"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"tenant_id": tid.String(),
		"counts":    counts,
		"next_run":  nextMidnightUTC().Format(time.RFC3339),
	})
}
