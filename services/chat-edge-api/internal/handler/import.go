// Import handlers — desktop agent-conversation backup mirror.
//
// POST /conversations/import              — create-or-get by client id
// POST /conversations/{id}/messages/import — append without generation
//
// Both are behind RequireAuth and deliberately NOT behind sendRateLimit:
// nothing here reaches an LLM, and a first sync of a long-lived desktop
// install legitimately fires many requests in a burst.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type ImportHandler struct {
	imports *service.ImportService
}

func NewImportHandler(s *service.ImportService) *ImportHandler {
	return &ImportHandler{imports: s}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type importConversationRequest struct {
	ClientID string `json:"client_id"`
	Title    string `json:"title"`
	ModelID  string `json:"model_id"`
	// CreatedAt is the desktop's historical timestamp (RFC3339, optional).
	CreatedAt string `json:"created_at"`
}

type importConversationResponse struct {
	Conversation conversationResponse `json:"conversation"`
	Created      bool                 `json:"created"`
}

type importMessageItem struct {
	ClientID string `json:"client_id"`
	Role     string `json:"role"`
	Content  string `json:"content"`
	ModelID  string `json:"model_id,omitempty"`
	// CreatedAt is required per message — historical data keeps its
	// original timestamp, never NOW().
	CreatedAt string `json:"created_at"`
}

type importMessagesRequest struct {
	Messages []importMessageItem `json:"messages"`
}

type importMessagesResponse struct {
	Imported int `json:"imported"`
	Skipped  int `json:"skipped"`
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

// ImportConversation — POST /conversations/import. Upsert by
// (tenant, user, client_id): 201 with created=true on insert, 200 with
// created=false when the mirror already exists. Replays are no-ops.
func (h *ImportHandler) ImportConversation(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	var req importConversationRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	var createdAt *time.Time
	if req.CreatedAt != "" {
		t, err := time.Parse(time.RFC3339, req.CreatedAt)
		if err != nil {
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "created_at must be RFC3339")
			return
		}
		createdAt = &t
	}

	c, created, err := h.imports.ImportConversation(r.Context(), service.ImportConversationInput{
		TenantID:  caller.TenantUUID,
		UserID:    caller.UserID,
		ClientID:  req.ClientID,
		Title:     req.Title,
		ModelID:   req.ModelID,
		CreatedAt: createdAt,
	})
	if err != nil {
		if errors.Is(err, service.ErrImportInvalid) {
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if created {
		w.WriteHeader(http.StatusCreated)
	}
	_ = json.NewEncoder(w).Encode(importConversationResponse{
		Conversation: toResponse(c),
		Created:      created,
	})
}

// ImportMessages — POST /conversations/{id}/messages/import. Appends
// historical messages in one transaction, deduped by client_id. Replaying
// the exact same request yields {"imported":0,"skipped":N}.
func (h *ImportHandler) ImportMessages(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	var req importMessagesRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}

	items := make([]service.ImportMessageInput, 0, len(req.Messages))
	for i, m := range req.Messages {
		ts, err := time.Parse(time.RFC3339, m.CreatedAt)
		if err != nil {
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
				"messages["+strconv.Itoa(i)+"].created_at must be RFC3339")
			return
		}
		items = append(items, service.ImportMessageInput{
			ClientID:  m.ClientID,
			Role:      m.Role,
			Content:   m.Content,
			ModelID:   m.ModelID,
			CreatedAt: ts,
		})
	}

	imported, skipped, err := h.imports.ImportMessages(
		r.Context(), caller.TenantUUID, caller.UserID, convID, items,
	)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrConversationNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
		case errors.Is(err, service.ErrImportInvalid):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(importMessagesResponse{
		Imported: imported,
		Skipped:  skipped,
	})
}
