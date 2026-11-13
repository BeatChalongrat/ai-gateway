package handler

import (
	"encoding/json"
	"errors"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"net/http"
)

// One authenticated endpoint is shared by Web Chat, Electron and host tools.
// Inspect/history are read-only modes; edit/restore always create a revision.
func (h *FileHandler) Editor(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "fileID"))
	if err != nil {
		WriteError(w, 400, CodeBadRequest, "invalid file id")
		return
	}
	var req service.FileEditRequest
	r.Body = http.MaxBytesReader(w, r.Body, 2<<20)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err = dec.Decode(&req); err != nil {
		WriteError(w, 400, CodeBadRequest, "invalid editor request")
		return
	}
	result, err := h.files.EditFile(r.Context(), caller.TenantUUID, caller.UserID, id, req)
	if err != nil {
		status, code, message := 500, CodeInternalError, "file editor failed"
		switch {
		case errors.Is(err, service.ErrFileNotFound):
			status, code, message = 404, CodeNotFound, "file not found"
		case errors.Is(err, repository.ErrFileRevisionConflict):
			status, code, message = 409, CodeBadRequest, err.Error()
		case errors.Is(err, service.ErrEditorInput):
			status, code, message = 422, CodeBadRequest, err.Error()
		case errors.Is(err, service.ErrPreviewUnsupported):
			status, code, message = 415, CodeBadRequest, "unsupported editor format"
		case errors.Is(err, service.ErrFileSandboxUnavailable):
			status, message = 503, "file editor sandbox is unavailable"
		}
		WriteError(w, status, code, message)
		return
	}
	out := map[string]any{"inspection": result.Inspection, "quality": result.Quality, "visual": result.Visual, "review": result.Review, "work": result.Work}
	if result.File != nil {
		out["file"] = FileToWire(result.File)
	}
	if result.Versions != nil {
		versions := make([]any, 0, len(result.Versions))
		for i := range result.Versions {
			versions = append(versions, FileToWire(&result.Versions[i]))
		}
		out["versions"] = versions
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}
