// Folder handlers — CRUD over chat.folders + conversation→folder moves.
//
// All routes sit behind RequireAuth (see router.go) so each handler pulls
// the caller from the request context via middleware.CallerFrom and a user
// can only see / edit their own folders.
package handler

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type FolderHandler struct {
	folders *service.FolderService
}

func NewFolderHandler(f *service.FolderService) *FolderHandler {
	return &FolderHandler{folders: f}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type folderResponse struct {
	ID                string    `json:"id"`
	Name              string    `json:"name"`
	Position          int       `json:"position"`
	ConversationCount int       `json:"conversation_count"`
	CreatedAt         time.Time `json:"created_at"`
	UpdatedAt         time.Time `json:"updated_at"`
}

func toFolderResponse(f *model.Folder) folderResponse {
	return folderResponse{
		ID:                f.ID.String(),
		Name:              f.Name,
		Position:          f.Position,
		ConversationCount: f.ConversationCount,
		CreatedAt:         f.CreatedAt,
		UpdatedAt:         f.UpdatedAt,
	}
}

type folderNameRequest struct {
	Name string `json:"name"`
}

type reorderFoldersRequest struct {
	FolderIDs []string `json:"folder_ids"`
}

// moveConversationRequest carries folder_id as RawMessage so the handler can
// tell "null" (unfile) from a UUID string (file into that folder).
type moveConversationRequest struct {
	FolderID json.RawMessage `json:"folder_id"`
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

// List — GET /folders. Returns the caller's folders (with conversation
// counts) ordered by position.
func (h *FolderHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	folders, err := h.folders.List(r.Context(), caller.TenantUUID, caller.UserID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	items := make([]folderResponse, 0, len(folders))
	for i := range folders {
		items = append(items, toFolderResponse(&folders[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"folders": items})
}

// Create — POST /folders { "name": "งาน" }.
func (h *FolderHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	var req folderNameRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	f, err := h.folders.Create(r.Context(), caller.TenantUUID, caller.UserID, req.Name)
	if err != nil {
		h.writeFolderError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(toFolderResponse(f))
}

// Rename — PUT /folders/{id} { "name": "งานใหม่" }.
func (h *FolderHandler) Rename(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	folderID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid folder id")
		return
	}
	var req folderNameRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	f, err := h.folders.Rename(r.Context(), caller.TenantUUID, caller.UserID, folderID, req.Name)
	if err != nil {
		h.writeFolderError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(toFolderResponse(f))
}

// Delete — DELETE /folders/{id}. Conversations in the folder become unfiled.
func (h *FolderHandler) Delete(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	folderID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid folder id")
		return
	}
	if err := h.folders.Delete(r.Context(), caller.TenantUUID, caller.UserID, folderID); err != nil {
		h.writeFolderError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// Reorder — PUT /folders/reorder { "folder_ids": ["...","..."] }.
func (h *FolderHandler) Reorder(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	var req reorderFoldersRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	ids := make([]uuid.UUID, 0, len(req.FolderIDs))
	for _, s := range req.FolderIDs {
		id, perr := uuid.Parse(s)
		if perr != nil {
			WriteError(w, http.StatusBadRequest, CodeValidationError, "folder_ids must be uuid strings")
			return
		}
		ids = append(ids, id)
	}
	if err := h.folders.Reorder(r.Context(), caller.TenantUUID, caller.UserID, ids); err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	// Echo the reordered list so the client can re-render without a refetch.
	folders, err := h.folders.List(r.Context(), caller.TenantUUID, caller.UserID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	items := make([]folderResponse, 0, len(folders))
	for i := range folders {
		items = append(items, toFolderResponse(&folders[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"folders": items})
}

// MoveConversation — PUT /conversations/{id}/folder
//
//	{ "folder_id": "abc-123" }  → file into that folder
//	{ "folder_id": null }       → unfile (back to TODAY / YESTERDAY / …)
func (h *FolderHandler) MoveConversation(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	var req moveConversationRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	folderID, perr := parseFolderRef(req.FolderID)
	if perr != nil {
		WriteError(w, http.StatusBadRequest, CodeValidationError, perr.Error())
		return
	}
	if err := h.folders.MoveConversation(r.Context(), caller.TenantUUID, caller.UserID, convID, folderID); err != nil {
		h.writeFolderError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// writeFolderError maps service errors to HTTP responses (mirrors
// ConversationHandler.writeUpdateError).
func (h *FolderHandler) writeFolderError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrFolderNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "folder not found")
	case errors.Is(err, service.ErrConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrFolderNameRequired):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "folder name is required")
	case errors.Is(err, service.ErrFolderNameTooLong):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "folder name too long (max 255)")
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// parseFolderRef interprets the folder_id field from the move body. Three
// cases mirror parseKBRef:
//
//	missing / "null"  → nil (unfile)
//	"\"<uuid>\""      → &uuid (file into that folder)
//	anything else     → error → clean 400
func parseFolderRef(raw json.RawMessage) (*uuid.UUID, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return nil, fmt.Errorf("folder_id must be a uuid string or null")
	}
	if s == "" {
		return nil, nil
	}
	id, err := uuid.Parse(s)
	if err != nil {
		return nil, fmt.Errorf("folder_id is not a valid uuid")
	}
	return &id, nil
}
