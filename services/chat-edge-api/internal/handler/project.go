// Project handlers — CRUD over chat.projects + conversation→project moves
// + project KB binding (Phase 4).
//
// All routes sit behind RequireAuth (see router.go); every handler pulls
// the caller from the request context so a user only sees their own
// projects. Same shape as the folder handlers.
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

type ProjectHandler struct {
	projects *service.ProjectService
}

func NewProjectHandler(p *service.ProjectService) *ProjectHandler {
	return &ProjectHandler{projects: p}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type projectResponse struct {
	ID                string    `json:"id"`
	Name              string    `json:"name"`
	Instructions      string    `json:"instructions"`
	KnowledgeBaseID   *string   `json:"knowledge_base_id"`
	ConversationCount int       `json:"conversation_count"`
	CreatedAt         time.Time `json:"created_at"`
	UpdatedAt         time.Time `json:"updated_at"`
}

func toProjectResponse(p *model.Project) projectResponse {
	r := projectResponse{
		ID:                p.ID.String(),
		Name:              p.Name,
		Instructions:      p.Instructions,
		ConversationCount: p.ConversationCount,
		CreatedAt:         p.CreatedAt,
		UpdatedAt:         p.UpdatedAt,
	}
	if p.KnowledgeBaseID != nil {
		s := p.KnowledgeBaseID.String()
		r.KnowledgeBaseID = &s
	}
	return r
}

type createProjectRequest struct {
	Name         string `json:"name"`
	Instructions string `json:"instructions"`
}

// updateProjectRequest patches any subset; absent fields keep their value.
type updateProjectRequest struct {
	Name         *string `json:"name"`
	Instructions *string `json:"instructions"`
}

// setProjectKBRequest carries knowledge_base_id as RawMessage so "null"
// (detach) is distinguishable from a UUID string (attach).
type setProjectKBRequest struct {
	KnowledgeBaseID json.RawMessage `json:"knowledge_base_id"`
}

// moveConversationProjectRequest — same null-vs-uuid contract.
type moveConversationProjectRequest struct {
	ProjectID json.RawMessage `json:"project_id"`
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

// List — GET /projects.
func (h *ProjectHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	projects, err := h.projects.List(r.Context(), caller.TenantUUID, caller.UserID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	items := make([]projectResponse, 0, len(projects))
	for i := range projects {
		items = append(items, toProjectResponse(&projects[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"projects": items})
}

// Create — POST /projects { "name": "...", "instructions": "..." }.
func (h *ProjectHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	var req createProjectRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	p, err := h.projects.Create(r.Context(), caller.TenantUUID, caller.UserID, req.Name, req.Instructions)
	if err != nil {
		h.writeProjectError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]any{"project": toProjectResponse(p)})
}

// Get — GET /projects/{id}.
func (h *ProjectHandler) Get(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid project id")
		return
	}
	p, err := h.projects.Get(r.Context(), caller.TenantUUID, caller.UserID, id)
	if err != nil {
		h.writeProjectError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"project": toProjectResponse(p)})
}

// Update — PUT /projects/{id} { "name"?: "...", "instructions"?: "..." }.
func (h *ProjectHandler) Update(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid project id")
		return
	}
	var req updateProjectRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	p, err := h.projects.Update(r.Context(), caller.TenantUUID, caller.UserID, id, req.Name, req.Instructions)
	if err != nil {
		h.writeProjectError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"project": toProjectResponse(p)})
}

// SetKnowledgeBase — PUT /projects/{id}/knowledge-base
// { "knowledge_base_id": "<uuid>" | null }.
func (h *ProjectHandler) SetKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid project id")
		return
	}
	var req setProjectKBRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	kbID, perr := parseNullableUUID(req.KnowledgeBaseID)
	if perr != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid knowledge_base_id")
		return
	}
	p, err := h.projects.SetKnowledgeBase(r.Context(), caller.TenantUUID, caller.UserID, id, kbID)
	if err != nil {
		h.writeProjectError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"project": toProjectResponse(p)})
}

// Delete — DELETE /projects/{id}. Member conversations are un-assigned
// (ON DELETE SET NULL), never removed.
func (h *ProjectHandler) Delete(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid project id")
		return
	}
	if err := h.projects.Delete(r.Context(), caller.TenantUUID, caller.UserID, id); err != nil {
		h.writeProjectError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// MoveConversation — PUT /conversations/{id}/project
// { "project_id": "<uuid>" | null }.
func (h *ProjectHandler) MoveConversation(w http.ResponseWriter, r *http.Request) {
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
	var req moveConversationProjectRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	projectID, perr := parseNullableUUID(req.ProjectID)
	if perr != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid project_id")
		return
	}
	if err := h.projects.MoveConversation(r.Context(), caller.TenantUUID, caller.UserID, convID, projectID); err != nil {
		h.writeProjectError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// parseNullableUUID decodes a JSON field that is either null/absent (nil)
// or a UUID string.
func parseNullableUUID(raw json.RawMessage) (*uuid.UUID, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return nil, err
	}
	id, err := uuid.Parse(s)
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (h *ProjectHandler) writeProjectError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrProjectNotFound),
		errors.Is(err, service.ErrConversationNotFound),
		errors.Is(err, service.ErrProjectKBNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, err.Error())
	case errors.Is(err, service.ErrProjectNameInvalid),
		errors.Is(err, service.ErrProjectInstructionsBig),
		errors.Is(err, service.ErrPromptGuardrailBlocked): // FIX22 §1a
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}
