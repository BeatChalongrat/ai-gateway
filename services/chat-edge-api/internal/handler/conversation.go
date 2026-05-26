// Conversation handlers — CRUD over chat.conversations.
//
// All routes are behind ``RequireAuth`` (see router.go) so every
// handler can pull the caller from the request context via
// ``middleware.CallerFrom`` without re-validating the JWT.
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
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type ConversationHandler struct {
	conversations *service.ConversationService
}

func NewConversationHandler(c *service.ConversationService) *ConversationHandler {
	return &ConversationHandler{conversations: c}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type createConversationRequest struct {
	ModelID string `json:"model_id"`
}

type conversationResponse struct {
	ID           string    `json:"id"`
	Title        string    `json:"title"`
	ModelID      string    `json:"model_id"`
	MessageCount int       `json:"message_count"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

func toResponse(c *model.Conversation) conversationResponse {
	return conversationResponse{
		ID:           c.ID.String(),
		Title:        c.Title,
		ModelID:      c.ModelID,
		MessageCount: c.MessageCount,
		CreatedAt:    c.CreatedAt,
		UpdatedAt:    c.UpdatedAt,
	}
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

func (h *ConversationHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	var req createConversationRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	c, err := h.conversations.Create(r.Context(), service.CreateInput{
		UserID:   caller.UserID,
		TenantID: caller.TenantUUID,
		ModelID:  req.ModelID,
	})
	if err != nil {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(toResponse(c))
}

// List supports query params ``limit`` (default 20, max 50) and
// ``cursor`` (UUID of the last-seen conversation from the previous
// page).
func (h *ConversationHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	cursor := parseUUID(r.URL.Query().Get("cursor"))

	convs, next, err := h.conversations.List(r.Context(), caller.TenantUUID, caller.UserID, limit, cursor)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}

	items := make([]conversationResponse, 0, len(convs))
	for i := range convs {
		items = append(items, toResponse(&convs[i]))
	}
	resp := map[string]any{
		"conversations": items,
		"has_more":      next != uuid.Nil,
	}
	if next != uuid.Nil {
		resp["next_cursor"] = next.String()
	} else {
		resp["next_cursor"] = nil
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

func (h *ConversationHandler) Get(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	c, err := h.conversations.Get(r.Context(), caller.TenantUUID, convID)
	if err != nil {
		if errors.Is(err, service.ErrConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	// Same-tenant but different-user → return 404 (don't leak existence).
	if c.UserID != caller.UserID {
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(toResponse(c))
}

// updateRequest covers every mutable field on chat.conversations.
// All fields are optional; the handler applies whichever ones are
// present. The route is still PATCH so the same endpoint serves
// title renames and model reassignment without a verb-explosion.
type updateRequest struct {
	Title   *string `json:"title,omitempty"`
	ModelID *string `json:"model_id,omitempty"`
}

func (h *ConversationHandler) Update(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	var req updateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Title == nil && req.ModelID == nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "no fields to update")
		return
	}

	if req.Title != nil {
		if err := h.conversations.Rename(r.Context(), caller.TenantUUID, convID, *req.Title); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}
	if req.ModelID != nil {
		if err := h.conversations.SetModel(r.Context(), caller.TenantUUID, convID, *req.ModelID); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}

	// Re-fetch + return so the client gets the updated timestamp.
	c, err := h.conversations.Get(r.Context(), caller.TenantUUID, convID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(toResponse(c))
}

func (h *ConversationHandler) writeUpdateError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrConversationBadModel):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "model not allowed")
	default:
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
	}
}

func (h *ConversationHandler) Delete(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	if err := h.conversations.Delete(r.Context(), caller.TenantUUID, convID); err != nil {
		if errors.Is(err, service.ErrConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// parseUUID is a small helper: returns uuid.Nil on empty / malformed
// input rather than erroring out so query-param parsing stays tidy.
func parseUUID(s string) uuid.UUID {
	if s == "" {
		return uuid.Nil
	}
	u, err := uuid.Parse(s)
	if err != nil {
		return uuid.Nil
	}
	return u
}
