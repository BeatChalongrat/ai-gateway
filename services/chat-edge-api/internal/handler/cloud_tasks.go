// Cloud scheduled task HTTP handlers (FIX13).
//
// Routes (all behind RequireAuth):
//
//	GET    /api/v1/cloud-tasks             list the caller's tasks
//	POST   /api/v1/cloud-tasks             create a task
//	PATCH  /api/v1/cloud-tasks/{id}        enable / disable a task
//	DELETE /api/v1/cloud-tasks/{id}        delete a task
//	GET    /api/v1/cloud-tasks/{id}/runs   the task's audit timeline
//
// Every operation is scoped to the authenticated caller's (tenant_id,
// user_id) via the repo — the owning identity ALWAYS comes from the CALLER,
// never from the request body — so a leaked id can never touch another
// user's task. Create validates the 1-hour interval floor and prompt length
// (→ 400). The stored prompt is plain text; nothing here executes it. The
// actual firing (and its authorize-before-run gate) lives in the runner, not
// the handler.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

type CloudTaskHandler struct {
	tasks *repository.CloudTaskRepo
}

func NewCloudTaskHandler(r *repository.CloudTaskRepo) *CloudTaskHandler {
	return &CloudTaskHandler{tasks: r}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type createCloudTaskRequest struct {
	ConversationID  string `json:"conversation_id"`
	Name            string `json:"name"`
	Prompt          string `json:"prompt"`
	IntervalSeconds int    `json:"interval_seconds"`
	MaxRuns         *int   `json:"max_runs"`
}

type setCloudTaskEnabledRequest struct {
	Enabled bool `json:"enabled"`
}

// validateCloudTaskInput mirrors migration 035's caps + the 1-hour floor so
// a bad request fails as a clean 400. Returns a user-facing message, or "".
func validateCloudTaskInput(name, prompt string, intervalSeconds int, maxRuns *int) string {
	if n := strings.TrimSpace(name); n == "" || len(name) > model.CloudTaskNameMaxLen {
		return "name must be 1-120 characters"
	}
	if p := strings.TrimSpace(prompt); p == "" || len(prompt) > model.CloudTaskPromptMaxLen {
		return "prompt must be 1-4000 characters"
	}
	if intervalSeconds < model.CloudTaskMinIntervalSeconds {
		return "interval_seconds must be at least 3600 (a cloud task fires at most once per hour)"
	}
	if maxRuns != nil && *maxRuns <= 0 {
		return "max_runs must be greater than 0 when set"
	}
	return ""
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

func (h *CloudTaskHandler) List(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	tasks, err := h.tasks.ListByUser(r.Context(), caller.TenantUUID, caller.UserID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	if tasks == nil {
		tasks = []model.CloudTask{}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"tasks": tasks,
		"total": len(tasks),
	})
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

func (h *CloudTaskHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	var req createCloudTaskRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	convID, err := uuid.Parse(strings.TrimSpace(req.ConversationID))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeValidationError, "conversation_id must be a valid UUID")
		return
	}
	if msg := validateCloudTaskInput(req.Name, req.Prompt, req.IntervalSeconds, req.MaxRuns); msg != "" {
		WriteError(w, http.StatusBadRequest, CodeValidationError, msg)
		return
	}

	// First run fires one interval from now. Owner identity is taken from
	// the CALLER — never the body.
	nextRun := time.Now().UTC().Add(time.Duration(req.IntervalSeconds) * time.Second)
	out, err := h.tasks.Create(r.Context(), &model.CloudTask{
		TenantID:        caller.TenantUUID,
		UserID:          caller.UserID,
		ConversationID:  convID,
		Name:            strings.TrimSpace(req.Name),
		Prompt:          req.Prompt,
		ScheduleKind:    "interval",
		IntervalSeconds: req.IntervalSeconds,
		NextRunAt:       nextRun,
		Enabled:         true,
		MaxRuns:         req.MaxRuns,
	})
	if err != nil {
		if errors.Is(err, repository.ErrInvalidCloudTask) {
			WriteError(w, http.StatusBadRequest, CodeValidationError, err.Error())
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(out)
}

// ---------------------------------------------------------------------------
// SetEnabled
// ---------------------------------------------------------------------------

func (h *CloudTaskHandler) SetEnabled(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid task id")
		return
	}
	var req setCloudTaskEnabledRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if err := h.tasks.SetEnabled(r.Context(), caller.TenantUUID, caller.UserID, id, req.Enabled); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "task not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "enabled": req.Enabled})
}

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

func (h *CloudTaskHandler) Delete(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid task id")
		return
	}
	if err := h.tasks.Delete(r.Context(), caller.TenantUUID, caller.UserID, id); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "task not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})
}

// ---------------------------------------------------------------------------
// Runs (audit)
// ---------------------------------------------------------------------------

func (h *CloudTaskHandler) ListRuns(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid task id")
		return
	}
	// Hard-scope by (tenant, user): a leaked task id from another user
	// yields an empty timeline, not someone else's audit.
	runs, err := h.tasks.ListRunsByTask(r.Context(), caller.TenantUUID, caller.UserID, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	if runs == nil {
		runs = []model.CloudTaskRun{}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"runs":  runs,
		"total": len(runs),
	})
}
