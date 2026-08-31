// Skill HTTP handlers (FIX12 Item 3 — user SKILLS).
//
// Routes (all behind RequireAuth):
//
//	GET    /api/v1/skills            list the caller's skills
//	POST   /api/v1/skills            create a skill
//	PATCH  /api/v1/skills/{id}       enable / disable a skill
//	DELETE /api/v1/skills/{id}       hard-delete a skill
//
// A skill is INSTRUCTION TEXT ONLY — the handler stores + returns the
// text; nothing here (or downstream) ever executes it. Every operation
// is scoped to the authenticated caller's (tenant_id, user_id) via the
// SkillRepo, so a leaked id can never touch another user's skill.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type SkillHandler struct {
	skills *repository.SkillRepo
	// FIX22 §1a — optional write-time prompt guard: a skill's Instructions
	// is string-concatenated into the system prompt (layer 10b).
	guard service.PromptGuard
}

func NewSkillHandler(r *repository.SkillRepo) *SkillHandler {
	return &SkillHandler{skills: r}
}

// SetPromptGuard wires the FIX22 write-time guard (nil = unchecked).
func (h *SkillHandler) SetPromptGuard(g service.PromptGuard) { h.guard = g }

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type createSkillRequest struct {
	Name         string   `json:"name"`
	Description  string   `json:"description"`
	Triggers     []string `json:"triggers"`
	Instructions string   `json:"instructions"`
}

type setSkillEnabledRequest struct {
	Enabled bool `json:"enabled"`
}

// validateSkillInput enforces the size caps mirrored from migration 034
// so a bad request fails as a clean 400 rather than a raw constraint
// violation. Returns a user-facing message, or "" when valid.
func validateSkillInput(name, description, instructions string) string {
	if n := strings.TrimSpace(name); n == "" || len(n) > model.SkillNameMaxLen {
		return "name must be 1-80 characters"
	}
	if len(description) > model.SkillDescriptionMaxLen {
		return "description must be at most 500 characters"
	}
	if l := strings.TrimSpace(instructions); l == "" || len(instructions) > model.SkillInstructionsMaxLen {
		return "instructions must be 1-10000 characters"
	}
	return ""
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

func (h *SkillHandler) List(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	skills, err := h.skills.ListByUser(r.Context(), caller.TenantUUID, caller.UserID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	if skills == nil {
		skills = []model.Skill{}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"skills": skills,
		"total":  len(skills),
	})
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

func (h *SkillHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	var req createSkillRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if msg := validateSkillInput(req.Name, req.Description, req.Instructions); msg != "" {
		WriteError(w, http.StatusBadRequest, CodeValidationError, msg)
		return
	}
	// FIX22 §1a — refuse skill instructions that would 403 every future turn.
	if err := service.GuardPromptText(r.Context(), h.guard, caller.TenantUUID, caller.UserID,
		req.Instructions, "the skill instructions"); err != nil {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		return
	}
	triggers := req.Triggers
	if triggers == nil {
		triggers = []string{}
	}
	out, err := h.skills.Create(r.Context(), &model.Skill{
		TenantID:     caller.TenantUUID,
		UserID:       caller.UserID,
		Name:         strings.TrimSpace(req.Name),
		Description:  req.Description,
		Triggers:     triggers,
		Instructions: req.Instructions,
		Enabled:      true,
	})
	if err != nil {
		if errors.Is(err, repository.ErrInvalidSkill) {
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

func (h *SkillHandler) SetEnabled(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid skill id")
		return
	}
	var req setSkillEnabledRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if err := h.skills.SetEnabled(r.Context(), caller.TenantUUID, caller.UserID, id, req.Enabled); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "skill not found")
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

func (h *SkillHandler) Delete(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid skill id")
		return
	}
	if err := h.skills.Delete(r.Context(), caller.TenantUUID, caller.UserID, id); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "skill not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})
}
