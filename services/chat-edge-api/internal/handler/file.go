// File-attachment HTTP handlers.
//
// Routes (all behind RequireAuth):
//
//	POST   /api/v1/conversations/{id}/files   multipart upload
//	GET    /api/v1/conversations/{id}/files   list attachments
//	GET    /api/v1/files/{fileID}/download    stream original blob
//	DELETE /api/v1/files/{fileID}             remove blob + row
//
// All tenant scoping happens inside FileService — the handlers only
// do request parsing + status mapping.
package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type FileHandler struct {
	files *service.FileService
	// maxMemMultipartMB controls how much of an upload buffers in
	// memory before spilling to a temp file. 32MiB matches the
	// per-file cap with headroom for headers.
	maxMemMultipartMB int64
}

func NewFileHandler(f *service.FileService) *FileHandler {
	return &FileHandler{files: f, maxMemMultipartMB: 32}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type fileResponse struct {
	ID               string `json:"id"`
	ConversationID   string `json:"conversation_id"`
	MessageID        string `json:"message_id,omitempty"`
	OriginalFilename string `json:"filename"`
	ContentType      string `json:"content_type"`
	FileSizeBytes    int64  `json:"file_size_bytes"`
	ExtractionStatus string `json:"extraction_status"`
	CreatedAt        string `json:"created_at"`
	// Phase 4 — Excel Smart Workspace surface fields. Frontend
	// uses IsGenerated to flip the file card into the
	// "AI-generated" treatment; Version + ParentFileID drive the
	// version history view; ChangeLog is the list of executor
	// changes rendered inside the result card.
	IsGenerated  bool     `json:"is_generated,omitempty"`
	ParentFileID string   `json:"parent_file_id,omitempty"`
	Version      int      `json:"version,omitempty"`
	ChangeLog    []string `json:"change_log,omitempty"`
}

// FileToWire formats a model.ConversationFile for the wire. Exported
// so the message handler can include attachments in its responses.
func FileToWire(f *model.ConversationFile) fileResponse {
	r := fileResponse{
		ID:               f.ID.String(),
		ConversationID:   f.ConversationID.String(),
		OriginalFilename: f.OriginalFilename,
		ContentType:      f.ContentType,
		FileSizeBytes:    f.FileSizeBytes,
		ExtractionStatus: f.ExtractionStatus,
		CreatedAt:        f.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
		IsGenerated:      f.IsGenerated,
		Version:          f.Version,
	}
	if f.MessageID != nil {
		r.MessageID = f.MessageID.String()
	}
	if f.ParentFileID != nil {
		r.ParentFileID = f.ParentFileID.String()
	}
	if f.ChangeLog != nil && *f.ChangeLog != "" {
		// Persisted as newline-joined; split back into a list for
		// the frontend so the result card can render one bullet
		// per change without re-splitting.
		for _, line := range splitLines(*f.ChangeLog) {
			if line != "" {
				r.ChangeLog = append(r.ChangeLog, line)
			}
		}
	}
	return r
}

func splitLines(s string) []string {
	if s == "" {
		return nil
	}
	out := []string{}
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	if start <= len(s) {
		out = append(out, s[start:])
	}
	return out
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------

func (h *FileHandler) Upload(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}

	// Cap total request size at maxMemMultipartMB + headroom so a
	// hostile client can't exhaust disk by streaming forever. The
	// per-file 25MiB ceiling lives in FileService.Upload.
	if err := r.ParseMultipartForm(h.maxMemMultipartMB * 1024 * 1024); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid multipart body")
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "missing 'file' field")
		return
	}
	defer file.Close()

	ct := header.Header.Get("Content-Type")
	if ct == "" {
		ct = "application/octet-stream"
	}

	row, err := h.files.Upload(r.Context(), service.UploadInput{
		TenantID:       caller.TenantUUID,
		UserID:         caller.UserID,
		ConversationID: convID,
		OriginalName:   header.Filename,
		ContentType:    ct,
		Size:           header.Size,
		Body:           file,
	})
	if err != nil {
		h.writeUploadError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(FileToWire(row))
}

func (h *FileHandler) writeUploadError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrFileConvNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrFileEmpty):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "empty upload")
	case errors.Is(err, service.ErrFileTooLarge):
		WriteError(w, http.StatusRequestEntityTooLarge, CodeValidationError, "file too large (max 25 MB)")
	case errors.Is(err, service.ErrFileTypeNotAllowed):
		WriteError(w, http.StatusUnsupportedMediaType, CodeValidationError, "file type not allowed")
	case errors.Is(err, service.ErrFileConvQuotaExceeded):
		WriteError(w, http.StatusRequestEntityTooLarge, CodeValidationError,
			"conversation file quota exceeded (max 100 MB total)")
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

func (h *FileHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	files, err := h.files.List(r.Context(), caller.TenantUUID, caller.UserID, convID)
	if err != nil {
		if errors.Is(err, service.ErrFileConvNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	out := make([]fileResponse, 0, len(files))
	for i := range files {
		out = append(out, FileToWire(&files[i]))
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"files": out})
}

// ---------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------

func (h *FileHandler) Download(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	fileID, err := uuid.Parse(chi.URLParam(r, "fileID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id")
		return
	}
	handle, err := h.files.OpenForDownload(r.Context(), caller.TenantUUID, caller.UserID, fileID)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrFileBlobMissing):
			// Row exists, blob is gone. 410 Gone + a distinct code so
			// the client renders "file unavailable" rather than a
			// broken-image / generic 404. We deliberately do NOT delete
			// the row here.
			WriteError(w, http.StatusGone, CodeFileUnavailable,
				"file is no longer available")
		case errors.Is(err, service.ErrFileNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "file not found")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	// Presigned URL path: send the browser straight to the object
	// store. Saves a service-side proxy hop and removes us from the
	// hot byte path on large downloads. 307 keeps the method as GET.
	if handle.PresignedURL != "" {
		http.Redirect(w, r, handle.PresignedURL, http.StatusTemporaryRedirect)
		return
	}
	defer handle.Body.Close()

	w.Header().Set("Content-Type", handle.File.ContentType)
	w.Header().Set("Content-Length", strconv.FormatInt(handle.File.FileSizeBytes, 10))
	// RFC 6266 says percent-encode the filename for safety; we keep
	// it simple with quoted form + UTF-8 fallback. Browsers handle
	// the basic quoted form fine for ASCII; users uploading
	// non-ASCII filenames see the UUID-prefixed sanitized name.
	w.Header().Set("Content-Disposition",
		`attachment; filename="`+handle.File.OriginalFilename+`"`)
	w.WriteHeader(http.StatusOK)
	_, _ = io.Copy(w, handle.Body)
}

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

func (h *FileHandler) Delete(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	fileID, err := uuid.Parse(chi.URLParam(r, "fileID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id")
		return
	}
	if err := h.files.Delete(r.Context(), caller.TenantUUID, caller.UserID, fileID); err != nil {
		if errors.Is(err, service.ErrFileNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "file not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
