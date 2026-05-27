// Share-link HTTP handlers.
//
//	POST   /api/v1/conversations/{id}/share   create / rotate link
//	DELETE /api/v1/conversations/{id}/share   revoke link
//	GET    /api/v1/shared/{token}             PUBLIC read-only view
//
// The first two routes live behind RequireAuth; the third must NOT
// — sharing's whole point is sending a link to people without an
// account.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type ShareHandler struct {
	shares *service.ShareService
}

func NewShareHandler(s *service.ShareService) *ShareHandler {
	return &ShareHandler{shares: s}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type createShareRequest struct {
	ExpiresIn string `json:"expires_in"` // "1h" | "24h" | "7d" | "never"
}

type shareResponse struct {
	ShareToken string `json:"share_token"`
	ShareURL   string `json:"share_url"`
	ExpiresAt  string `json:"expires_at,omitempty"`
	CreatedAt  string `json:"created_at"`
}

func shareToResponse(s *model.SharedConversation) shareResponse {
	out := shareResponse{
		ShareToken: s.ShareToken,
		ShareURL:   "/shared/" + s.ShareToken,
		CreatedAt:  s.CreatedAt.UTC().Format(time.RFC3339),
	}
	if s.ExpiresAt != nil {
		out.ExpiresAt = s.ExpiresAt.UTC().Format(time.RFC3339)
	}
	return out
}

// ---------------------------------------------------------------------------
// Create / rotate
// ---------------------------------------------------------------------------

func (h *ShareHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	var req createShareRequest
	// Empty body is fine — defaults to "never".
	_ = json.NewDecoder(r.Body).Decode(&req)

	row, err := h.shares.Create(r.Context(), caller.TenantUUID, caller.UserID, convID, req.ExpiresIn)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrShareConvNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
		case errors.Is(err, service.ErrShareBadExpiry):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
				"expires_in must be one of: 1h, 24h, 7d, never")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(shareToResponse(row))
}

// ---------------------------------------------------------------------------
// Revoke
// ---------------------------------------------------------------------------

func (h *ShareHandler) Revoke(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	if err := h.shares.Revoke(r.Context(), caller.TenantUUID, caller.UserID, convID); err != nil {
		switch {
		case errors.Is(err, service.ErrShareConvNotFound),
			errors.Is(err, service.ErrShareNotFound):
			// Either "no such conversation" or "no share to revoke" —
			// both legitimately 404 from the client's perspective.
			WriteError(w, http.StatusNotFound, CodeNotFound, "share link not found")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// ---------------------------------------------------------------------------
// Public GET
// ---------------------------------------------------------------------------

type sharedFileWire struct {
	ID            string `json:"id"`
	Filename      string `json:"filename"`
	ContentType   string `json:"content_type"`
	FileSizeBytes int64  `json:"file_size_bytes"`
}

type sharedMessageWire struct {
	ID        string           `json:"id"`
	Role      string           `json:"role"`
	Content   string           `json:"content"`
	ModelID   *string          `json:"model_id,omitempty"`
	CreatedAt string           `json:"created_at"`
	Files     []sharedFileWire `json:"files,omitempty"`
}

type sharedConversationWire struct {
	Title     string `json:"title"`
	ModelID   string `json:"model_id"`
	CreatedAt string `json:"created_at"`
	UpdatedAt string `json:"updated_at"`
}

// PublicGet returns the read-only conversation rendering. Bound to
// the share token; tenant scope is read off the share row, not the
// request — there's no authenticated caller here.
//
// Returns 404 for both expired and missing tokens; the response
// shape never leaks which case.
func (h *ShareHandler) PublicGet(w http.ResponseWriter, r *http.Request) {
	token := chi.URLParam(r, "token")
	if token == "" {
		WriteError(w, http.StatusNotFound, CodeNotFound, "share not found")
		return
	}
	view, err := h.shares.Resolve(r.Context(), token)
	if err != nil {
		if errors.Is(err, service.ErrShareNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "share not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}

	msgs := make([]sharedMessageWire, 0, len(view.Messages))
	for i := range view.Messages {
		m := &view.Messages[i]
		row := sharedMessageWire{
			ID:        m.ID.String(),
			Role:      m.Role,
			Content:   m.ContentMasked,
			ModelID:   m.ModelID,
			CreatedAt: m.CreatedAt.UTC().Format(time.RFC3339),
		}
		if attached := view.FilesByMsg[m.ID]; len(attached) > 0 {
			row.Files = make([]sharedFileWire, 0, len(attached))
			for _, f := range attached {
				row.Files = append(row.Files, sharedFileWire{
					ID:            f.ID.String(),
					Filename:      f.OriginalFilename,
					ContentType:   f.ContentType,
					FileSizeBytes: f.FileSizeBytes,
				})
			}
		}
		msgs = append(msgs, row)
	}
	convWire := sharedConversationWire{
		Title:     view.Conversation.Title,
		ModelID:   view.Conversation.ModelID,
		CreatedAt: view.Conversation.CreatedAt.UTC().Format(time.RFC3339),
		UpdatedAt: view.Conversation.UpdatedAt.UTC().Format(time.RFC3339),
	}
	out := map[string]any{
		"conversation": convWire,
		"messages":     msgs,
	}
	if view.ExpiresAt != nil {
		out["expires_at"] = view.ExpiresAt.UTC().Format(time.RFC3339)
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

// GetForOwner is the auth-gated read used by the share modal to
// pre-fill the existing link.
func (h *ShareHandler) GetForOwner(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	row, err := h.shares.GetForOwner(r.Context(), caller.TenantUUID, caller.UserID, convID)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrShareNotFound),
			errors.Is(err, service.ErrShareConvNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "no share link")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(shareToResponse(row))
}
