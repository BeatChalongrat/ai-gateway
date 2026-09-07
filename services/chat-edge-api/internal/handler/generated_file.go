// FIX81 — the ONE client door for persisting an assistant-produced file
// into a conversation:
//
//	POST /api/v1/conversations/{id}/files/generated   (RequireAuth)
//	{ "filename": "...", "content_type": "...", "data_base64": "...",
//	  "generated_by": "model-name (optional)" }
//
// Every producer that cannot call FileService.SaveGeneratedFile in-process
// (the desktop host's generate_image, the web media forms' finished jobs)
// posts bytes here; the server persists them through the SAME pipeline the
// web tools and /code/run use, so the result is a durable, previewable,
// downloadable ConversationFile with Made-by attribution — never a data
// URI that dies with the session. Ownership-gated exactly like the upload
// path (404 for absent-or-not-yours). MessageID stays nil: the desktop
// links files to its assistant row at import; a form-produced file lives
// at conversation level (the files panel).
package handler

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// generatedFileStore is the slice of *service.FileService this handler
// needs — an interface so the door is unit-testable without a DB.
type generatedFileStore interface {
	AssertConversationOwned(ctx context.Context, tenantID, userID, convID uuid.UUID) error
	SaveGeneratedFile(ctx context.Context, in service.GeneratedFileInput) (*model.ConversationFile, error)
}

type GeneratedFileHandler struct {
	files generatedFileStore
}

func NewGeneratedFileHandler(files generatedFileStore) *GeneratedFileHandler {
	return &GeneratedFileHandler{files: files}
}

type saveGeneratedRequest struct {
	Filename    string `json:"filename"`
	ContentType string `json:"content_type"`
	DataBase64  string `json:"data_base64"`
	GeneratedBy string `json:"generated_by"`
}

// maxGeneratedFileBytes caps the DECODED payload — matches the 32 MiB
// upload cap; base64 inflates 4/3, hence the request reader cap below.
const maxGeneratedFileBytes = 32 << 20

func (h *GeneratedFileHandler) Save(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, (maxGeneratedFileBytes/3)*4+4096)
	var req saveGeneratedRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if strings.TrimSpace(req.Filename) == "" {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "filename is required")
		return
	}
	data, err := base64.StdEncoding.DecodeString(req.DataBase64)
	if err != nil || len(data) == 0 {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "data_base64 must be non-empty base64")
		return
	}
	if len(data) > maxGeneratedFileBytes {
		WriteError(w, http.StatusRequestEntityTooLarge, CodeValidationError, "file exceeds the 32 MiB limit")
		return
	}
	if err := h.files.AssertConversationOwned(r.Context(), caller.TenantUUID, caller.UserID, convID); err != nil {
		if errors.Is(err, service.ErrFileConvNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "could not verify conversation")
		return
	}
	row, err := h.files.SaveGeneratedFile(r.Context(), service.GeneratedFileInput{
		TenantID:         caller.TenantUUID,
		UserID:           caller.UserID,
		ConversationID:   convID,
		Data:             data,
		ContentType:      req.ContentType,
		OriginalFilename: req.Filename,
		GeneratedBy:      strings.TrimSpace(req.GeneratedBy),
	})
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "could not save the file")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(FileToWire(row))
}
