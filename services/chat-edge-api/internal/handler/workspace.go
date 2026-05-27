// Workspace HTTP handlers (Phase 4 Session 5).
//
// Routes (all behind RequireAuth):
//
//	GET  /api/v1/conversations/{convID}/generated-files
//	    Latest version of every generated-file lineage in the
//	    conversation, with version_count + parent_filename.
//	    Populates the workspace panel's file list.
//
//	POST /api/v1/conversations/{convID}/files/{fileID}/rollback
//	    Body: { "target_version_id": "uuid" }
//	    Creates a new version (vN+1) carrying the target's content.
//
//	GET  /api/v1/conversations/{convID}/file-compare?v1=X&v2=Y
//	    Structural diff between two generated versions. Lives at
//	    /file-compare (not /files/compare) so chi's segment matcher
//	    doesn't fight /files/{fileID}/... patterns.
//
// The pre-existing /files/{fileID}/versions handler (ExcelHandler.
// Versions) is extended via a small helper here so its response
// now carries an is_current flag without changing the route.
package handler

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type WorkspaceHandler struct {
	files     *repository.FileRepo
	workspace *service.WorkspaceService
}

func NewWorkspaceHandler(
	files *repository.FileRepo, ws *service.WorkspaceService,
) *WorkspaceHandler {
	return &WorkspaceHandler{files: files, workspace: ws}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type generatedFileResponse struct {
	fileResponse
	VersionCount   int    `json:"version_count"`
	ParentFilename string `json:"parent_filename,omitempty"`
}

type rollbackRequest struct {
	TargetVersionID string `json:"target_version_id"`
}

// ---------------------------------------------------------------------------
// GET /conversations/{convID}/generated-files
// ---------------------------------------------------------------------------

func (h *WorkspaceHandler) ListGenerated(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "convID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	leaves, err := h.files.ListGeneratedLeaves(r.Context(), caller.TenantUUID, convID)
	if err != nil {
		slog.Warn("workspace: list generated failed",
			"conv", convID.String(), "err", err)
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	out := make([]generatedFileResponse, 0, len(leaves))
	for i := range leaves {
		leaf := &leaves[i]
		out = append(out, generatedFileResponse{
			fileResponse:   FileToWire(&leaf.File),
			VersionCount:   leaf.VersionCount,
			ParentFilename: leaf.ParentFilename,
		})
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"files": out})
}

// ---------------------------------------------------------------------------
// POST /conversations/{convID}/files/{fileID}/rollback
// ---------------------------------------------------------------------------

func (h *WorkspaceHandler) Rollback(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "convID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	lineageID, err := uuid.Parse(chi.URLParam(r, "fileID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id")
		return
	}
	var req rollbackRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	targetID, err := uuid.Parse(req.TargetVersionID)
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid target_version_id")
		return
	}
	row, err := h.workspace.Rollback(r.Context(), service.RollbackInput{
		TenantID:        caller.TenantUUID,
		ConversationID:  convID,
		LineageFileID:   lineageID,
		TargetVersionID: targetID,
	})
	if err != nil {
		switch {
		case errors.Is(err, service.ErrWorkspaceTargetNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "target version not found")
		case errors.Is(err, service.ErrWorkspaceTargetMismatch):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		case errors.Is(err, service.ErrWorkspaceRollbackFailed):
			WriteError(w, http.StatusBadGateway, CodeInternalError, err.Error())
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"file":    FileToWire(row),
		"message": "Rollback created a new version.",
	})
}

// ---------------------------------------------------------------------------
// GET /conversations/{convID}/file-compare?v1=X&v2=Y
// ---------------------------------------------------------------------------

func (h *WorkspaceHandler) Compare(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "convID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	v1, err := uuid.Parse(r.URL.Query().Get("v1"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "v1 query param required (uuid)")
		return
	}
	v2, err := uuid.Parse(r.URL.Query().Get("v2"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "v2 query param required (uuid)")
		return
	}
	out, err := h.workspace.Compare(r.Context(), service.CompareInput{
		TenantID:       caller.TenantUUID,
		ConversationID: convID,
		V1FileID:       v1,
		V2FileID:       v2,
	})
	if err != nil {
		switch {
		case errors.Is(err, service.ErrWorkspaceTargetNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "version not found")
		case errors.Is(err, service.ErrWorkspaceCompareTypes),
			errors.Is(err, service.ErrWorkspaceCompareNotGen):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		case errors.Is(err, service.ErrWorkspaceCompareFailed):
			WriteError(w, http.StatusBadGateway, CodeInternalError, err.Error())
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"v1":         summarizeFileForCompare(out.V1),
		"v2":         summarizeFileForCompare(out.V2),
		"comparison": out.Report,
	})
}

func summarizeFileForCompare(f *model.ConversationFile) map[string]any {
	return map[string]any{
		"id":              f.ID.String(),
		"filename":        f.OriginalFilename,
		"version":         f.Version,
		"file_size_bytes": f.FileSizeBytes,
		"created_at":      f.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
	}
}
