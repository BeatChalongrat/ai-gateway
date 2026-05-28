// Memory HTTP handlers (S1 — Personal Memory Core).
//
// Routes (all behind RequireAuth):
//
//   GET    /api/v1/memories                 list (paginated + filtered)
//   POST   /api/v1/memories                 create (user_explicit)
//   PUT    /api/v1/memories/{id}            update
//   DELETE /api/v1/memories/{id}            soft delete
//   POST   /api/v1/memories/{id}/pin        pin / unpin
//   POST   /api/v1/memories/{id}/confirm    confirm / reject
//   DELETE /api/v1/memories/all             bulk clear (optional ?scope_type=)
//   GET    /api/v1/memories/{id}/events     audit trail
//
// Tenant + user scoping happens inside MemoryService — the handlers
// only parse request bodies, dispatch, and map typed errors to HTTP
// status codes. Mirrors the existing handler pattern (file.go,
// memory only diverges where the response shape demands).
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type MemoryHandler struct {
	memories *service.MemoryService
}

func NewMemoryHandler(m *service.MemoryService) *MemoryHandler {
	return &MemoryHandler{memories: m}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type createMemoryRequest struct {
	ScopeType  string `json:"scope_type"`
	ScopeID    string `json:"scope_id,omitempty"`
	MemoryType string `json:"memory_type"`
	Category   string `json:"category,omitempty"`
	Content    string `json:"content"`
	// SourceType from the public API is forced to user_explicit —
	// the AI-tool path bypasses this handler entirely.
	SourceType string `json:"source_type,omitempty"`
}

type updateMemoryRequest struct {
	Content         *string  `json:"content,omitempty"`
	Category        *string  `json:"category,omitempty"`
	MemoryType      *string  `json:"memory_type,omitempty"`
	ImportanceScore *float64 `json:"importance_score,omitempty"`
}

type pinRequest struct {
	Pinned bool `json:"pinned"`
}

type confirmRequest struct {
	Confirmed bool `json:"confirmed"`
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

func (h *MemoryHandler) List(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())

	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))
	filters := repository.MemoryListFilters{
		ScopeType:  r.URL.Query().Get("scope_type"),
		MemoryType: r.URL.Query().Get("memory_type"),
		Status:     r.URL.Query().Get("status"),
		Limit:      limit,
		Offset:     offset,
	}

	rows, total, err := h.memories.ListByUser(r.Context(), caller.UserID, caller.TenantUUID, filters)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"memories": rows,
		"total":    total,
	})
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

func (h *MemoryHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	var req createMemoryRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	var scopeID *uuid.UUID
	if req.ScopeID != "" {
		id, err := uuid.Parse(req.ScopeID)
		if err != nil {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid scope_id")
			return
		}
		scopeID = &id
	}
	out, err := h.memories.CreateMemory(r.Context(), service.CreateMemoryInput{
		TenantID:   caller.TenantUUID,
		UserID:     caller.UserID,
		ScopeType:  req.ScopeType,
		ScopeID:    scopeID,
		MemoryType: req.MemoryType,
		Category:   req.Category,
		Content:    req.Content,
		// Public API can only create user-explicit memories. The AI
		// tool path supplies its own SourceType.
		SourceType: model.MemorySourceUserExplicit,
		ActorType:  "user",
	})
	if err != nil {
		h.writeCreateError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(out)
}

func (h *MemoryHandler) writeCreateError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrMemoryEmpty),
		errors.Is(err, service.ErrMemoryInvalidScope),
		errors.Is(err, service.ErrMemoryInvalidType),
		errors.Is(err, service.ErrMemoryInvalidSource):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
	case errors.Is(err, service.ErrMemoryProhibitedContent):
		WriteError(w, http.StatusBadRequest, CodeValidationError,
			"content appears to contain sensitive data (API keys, IDs, financial info) and cannot be saved")
	case errors.Is(err, service.ErrMemoryDuplicate):
		WriteError(w, http.StatusConflict, CodeConflict, "an identical memory already exists")
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// ---------------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------------

func (h *MemoryHandler) Update(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid memory id")
		return
	}
	var req updateMemoryRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Content == nil && req.Category == nil && req.MemoryType == nil && req.ImportanceScore == nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "no fields to update")
		return
	}
	out, err := h.memories.UpdateMemory(r.Context(), id, caller.UserID, caller.TenantUUID, service.UpdateMemoryInput{
		Content:         req.Content,
		Category:        req.Category,
		MemoryType:      req.MemoryType,
		ImportanceScore: req.ImportanceScore,
	})
	if err != nil {
		switch {
		case errors.Is(err, service.ErrMemoryNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "memory not found")
		case errors.Is(err, service.ErrMemoryEmpty),
			errors.Is(err, service.ErrMemoryInvalidType):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		case errors.Is(err, service.ErrMemoryProhibitedContent):
			WriteError(w, http.StatusBadRequest, CodeValidationError,
				"content appears to contain sensitive data and cannot be saved")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

// ---------------------------------------------------------------------------
// Delete (single)
// ---------------------------------------------------------------------------

func (h *MemoryHandler) Delete(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid memory id")
		return
	}
	if err := h.memories.DeleteMemory(r.Context(), id, caller.UserID, caller.TenantUUID); err != nil {
		if errors.Is(err, service.ErrMemoryNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "memory not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// ---------------------------------------------------------------------------
// Bulk clear
// ---------------------------------------------------------------------------

func (h *MemoryHandler) ClearAll(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	var scopeFilter *string
	if s := r.URL.Query().Get("scope_type"); s != "" {
		scopeFilter = &s
	}
	n, err := h.memories.ClearAll(r.Context(), caller.UserID, caller.TenantUUID, scopeFilter)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"deleted_count": n})
}

// ---------------------------------------------------------------------------
// Pin / Confirm
// ---------------------------------------------------------------------------

func (h *MemoryHandler) Pin(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid memory id")
		return
	}
	var req pinRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	out, err := h.memories.SetPinned(r.Context(), id, caller.UserID, caller.TenantUUID, req.Pinned)
	if err != nil {
		if errors.Is(err, service.ErrMemoryNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "memory not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

func (h *MemoryHandler) Confirm(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid memory id")
		return
	}
	var req confirmRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	out, err := h.memories.SetConfirmed(r.Context(), id, caller.UserID, caller.TenantUUID, req.Confirmed)
	if err != nil {
		if errors.Is(err, service.ErrMemoryNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "memory not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

// ---------------------------------------------------------------------------
// Events (audit trail)
// ---------------------------------------------------------------------------

func (h *MemoryHandler) Events(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid memory id")
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	events, err := h.memories.GetEvents(r.Context(), id, caller.UserID, caller.TenantUUID, limit)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"events": events})
}
