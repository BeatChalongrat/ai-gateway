// User-preferences handlers.
//
// Two endpoints back the "Custom Instructions" modal:
//
//	GET /api/v1/user/preferences         — read current values
//	PUT /api/v1/user/preferences         — upsert
//
// Empty body fields are valid (clears the instruction). Length is
// capped at 1500 chars per field to match the front-end UI limit.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type PreferencesHandler struct {
	prefs *repository.PreferencesRepo
	// FIX22 §1a — optional write-time prompt guard: custom instructions are
	// folded into every turn's system prompt (prompt_compiler layer 10).
	guard service.PromptGuard
}

func NewPreferencesHandler(p *repository.PreferencesRepo) *PreferencesHandler {
	return &PreferencesHandler{prefs: p}
}

// SetPromptGuard wires the FIX22 write-time guard (nil = unchecked).
func (h *PreferencesHandler) SetPromptGuard(g service.PromptGuard) { h.guard = g }

type preferencesResponse struct {
	CustomInstructionsAbout   string `json:"custom_instructions_about"`
	CustomInstructionsRespond string `json:"custom_instructions_respond"`
	UpdatedAt                 string `json:"updated_at"`
}

type preferencesUpdateRequest struct {
	CustomInstructionsAbout   *string `json:"custom_instructions_about,omitempty"`
	CustomInstructionsRespond *string `json:"custom_instructions_respond,omitempty"`
}

const maxInstructionChars = 1500

func (h *PreferencesHandler) Get(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	p, err := h.prefs.Get(r.Context(), caller.TenantUUID, caller.UserID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			// First-time read — return canonical defaults so the
			// modal renders empty rather than 404.
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(preferencesResponse{})
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(preferencesResponse{
		CustomInstructionsAbout:   p.CustomInstructionsAbout,
		CustomInstructionsRespond: p.CustomInstructionsRespond,
		UpdatedAt:                 p.UpdatedAt.UTC().Format("2006-01-02T15:04:05Z"),
	})
}

func (h *PreferencesHandler) Put(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	var req preferencesUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}

	// Merge with existing row so a PUT that only sets one field
	// doesn't clobber the other.
	about := ""
	respond := ""
	if existing, err := h.prefs.Get(r.Context(), caller.TenantUUID, caller.UserID); err == nil {
		about = existing.CustomInstructionsAbout
		respond = existing.CustomInstructionsRespond
	}
	if req.CustomInstructionsAbout != nil {
		about = *req.CustomInstructionsAbout
	}
	if req.CustomInstructionsRespond != nil {
		respond = *req.CustomInstructionsRespond
	}
	if len([]rune(about)) > maxInstructionChars || len([]rune(respond)) > maxInstructionChars {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"each instruction field is limited to 1500 characters")
		return
	}
	// FIX22 §1a — refuse instructions that would 403 every future turn.
	if err := service.GuardPromptText(r.Context(), h.guard, caller.TenantUUID, caller.UserID,
		about+"\n"+respond, "your custom instructions"); err != nil {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		return
	}

	p, err := h.prefs.Upsert(r.Context(), caller.TenantUUID, caller.UserID, about, respond)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(preferencesResponse{
		CustomInstructionsAbout:   p.CustomInstructionsAbout,
		CustomInstructionsRespond: p.CustomInstructionsRespond,
		UpdatedAt:                 p.UpdatedAt.UTC().Format("2006-01-02T15:04:05Z"),
	})
}
