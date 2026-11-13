package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
)

func workspaceJSON(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(value)
}

func (h *FileHandler) WorkspaceTemplate(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	id, e := uuid.Parse(chi.URLParam(r, "convID"))
	if e != nil {
		workspaceError(w, service.ErrEditorInput)
		return
	}
	var req service.WorkspaceTemplateRequest
	if !workspaceBody(w, r, &req) {
		return
	}
	result, e := h.files.CreateWorkspaceTemplate(r.Context(), caller.TenantUUID, caller.UserID, id, req)
	if e != nil {
		workspaceError(w, e)
		return
	}
	workspaceJSON(w, map[string]any{"file": FileToWire(result.File), "quality": result.Quality})
}
func workspaceError(w http.ResponseWriter, e error) {
	status := 500
	message := "workspace operation failed"
	var pg *pgconn.PgError
	switch {
	case errors.Is(e, service.ErrFileNotFound):
		status = 404
		message = "file or task not found"
	case errors.Is(e, repository.ErrDraftConflict), errors.Is(e, repository.ErrFileRevisionConflict):
		status = 409
		message = e.Error()
	case errors.As(e, &pg) && pg.Code == "23505":
		status = 409
		message = "a proposal already exists for this file; review or cancel it first"
	case errors.Is(e, service.ErrEditorInput):
		status = 422
		message = e.Error()
	}
	WriteError(w, status, CodeBadRequest, message)
}
func workspaceBody(w http.ResponseWriter, r *http.Request, value any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 2<<20)
	d := json.NewDecoder(r.Body)
	d.DisallowUnknownFields()
	if e := d.Decode(value); e != nil {
		WriteError(w, 400, CodeBadRequest, "invalid workspace request")
		return false
	}
	if d.Decode(new(any)) != io.EOF {
		WriteError(w, 400, CodeBadRequest, "extra request data")
		return false
	}
	return true
}
func (h *FileHandler) ArtifactDraft(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	id, e := uuid.Parse(chi.URLParam(r, "fileID"))
	if e != nil {
		workspaceError(w, service.ErrEditorInput)
		return
	}
	var d repository.FileDraft
	if r.Method != "GET" && !workspaceBody(w, r, &d) {
		return
	}
	result, e := h.files.ArtifactDraft(r.Context(), caller.TenantUUID, caller.UserID, id, r.Method, d)
	if e != nil {
		workspaceError(w, e)
		return
	}
	workspaceJSON(w, map[string]any{"draft": result})
}
func (h *FileHandler) ArtifactWork(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	id, e := uuid.Parse(chi.URLParam(r, "fileID"))
	if e != nil {
		workspaceError(w, service.ErrEditorInput)
		return
	}
	if r.Method == "GET" {
		result, e := h.files.LatestArtifactWork(r.Context(), caller.TenantUUID, caller.UserID, id)
		if e != nil {
			workspaceError(w, e)
			return
		}
		workspaceJSON(w, map[string]any{"work": result})
		return
	}
	var req service.ArtifactWorkRequest
	if !workspaceBody(w, r, &req) {
		return
	}
	result, e := h.files.StartArtifactWork(r.Context(), caller.TenantUUID, caller.UserID, id, req)
	if e != nil {
		workspaceError(w, e)
		return
	}
	workspaceJSON(w, map[string]any{"work": result})
}
func (h *FileHandler) ArtifactWorkStatus(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	id, e := uuid.Parse(chi.URLParam(r, "workID"))
	if e != nil {
		workspaceError(w, service.ErrEditorInput)
		return
	}
	if r.Method == "GET" {
		result, e := h.files.GetArtifactWork(r.Context(), caller.TenantUUID, caller.UserID, id)
		if e != nil {
			workspaceError(w, e)
			return
		}
		workspaceJSON(w, map[string]any{"work": result})
		return
	}
	var req struct {
		Action string   `json:"action"`
		IDs    []string `json:"ids"`
	}
	if !workspaceBody(w, r, &req) {
		return
	}
	result, e := h.files.ControlArtifactWork(r.Context(), caller.TenantUUID, caller.UserID, id, req.Action, req.IDs)
	if e != nil {
		workspaceError(w, e)
		return
	}
	out := map[string]any{}
	if result.File != nil {
		out["file"] = FileToWire(result.File)
	}
	out["quality"] = result.Quality
	out["review"] = result.Review
	workspaceJSON(w, out)
}
func (h *FileHandler) ArtifactLibrary(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	offset := 0
	if raw := r.URL.Query().Get("offset"); raw != "" {
		var e error
		offset, e = strconv.Atoi(raw)
		if e != nil {
			workspaceError(w, service.ErrEditorInput)
			return
		}
	}
	rows, e := h.files.ArtifactLibrary(r.Context(), caller.TenantUUID, caller.UserID, r.URL.Query().Get("q"), r.URL.Query().Get("kind"), offset)
	if e != nil {
		workspaceError(w, e)
		return
	}
	more := len(rows) > 40
	if more {
		rows = rows[:40]
	}
	files := make([]any, 0, len(rows))
	for i := range rows {
		files = append(files, map[string]any{"file": FileToWire(&rows[i]), "conversation_id": rows[i].ConversationID})
	}
	workspaceJSON(w, map[string]any{"items": files, "has_more": more})
}
