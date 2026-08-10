// Structured preview data for one attachment — Phase 16.
//
//	GET /api/v1/files/{fileID}/preview-data?sheet=&offset=&limit=
//
// Returns the JSON scripts/preview_data.py produced: a window of one
// sheet's cells plus every sheet name, Word blocks in document order, or
// one entry per slide. The client renders a real grid instead of the
// tab-separated dump /files/{id}/text gives it.
//
// Status mapping is what makes this safe to call speculatively — a
// client asks, and on 415/503 falls back to the flat text it used
// before:
//
//	404  not the caller's file, or no such id (never 403 — existence
//	     must not leak, same rule as /files/{id}/text)
//	415  a real file with no structured view (pdf, image, archive)
//	503  the sandbox is not configured/available on this deployment
package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// previewDataSource is the slice of *service.FileService this endpoint
// uses, as an interface so the handler is testable without a database,
// a storage backend or docker.
type previewDataSource interface {
	PreviewData(
		ctx context.Context, tenantID, userID, fileID uuid.UUID,
		req service.PreviewDataRequest,
	) (json.RawMessage, error)
}

func (h *FileHandler) PreviewData(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	writePreviewData(r.Context(), w, r, chi.URLParam(r, "fileID"),
		caller.TenantUUID, caller.UserID, h.files)
}

// writePreviewData holds the handler body with the caller passed in, so
// tests can exercise the owner and the not-the-owner path without
// standing up the auth middleware (same shape as writeFileText).
func writePreviewData(
	ctx context.Context, w http.ResponseWriter, r *http.Request, rawFileID string,
	tenantID, userID uuid.UUID, src previewDataSource,
) {
	fileID, err := uuid.Parse(rawFileID)
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id")
		return
	}

	out, err := src.PreviewData(ctx, tenantID, userID, fileID,
		service.PreviewDataRequest{
			Sheet:  queryInt(r, "sheet"),
			Offset: queryInt(r, "offset"),
			Limit:  queryInt(r, "limit"),
		})
	if err != nil {
		switch {
		case errors.Is(err, service.ErrFileNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "file not found")
		case errors.Is(err, service.ErrPreviewUnsupported):
			WriteError(w, http.StatusUnsupportedMediaType, CodeBadRequest,
				"no structured preview for this file type")
		case errors.Is(err, service.ErrFileSandboxUnavailable):
			WriteError(w, http.StatusServiceUnavailable, CodeInternalError,
				"structured preview is unavailable")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError,
				"internal error")
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(out)
}

// queryInt reads a bounded-ish integer query param. An absent or
// unparseable value is 0, which the service reads as "not specified"
// and replaces with its default — a preview must not 400 on a stray
// query string.
func queryInt(r *http.Request, name string) int {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return 0
	}
	n, err := strconv.Atoi(raw)
	if err != nil {
		return 0
	}
	return n
}
