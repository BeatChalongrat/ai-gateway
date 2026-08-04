// Extracted-text endpoint for one attachment.
//
//	GET /api/v1/files/{fileID}/text   →  {extraction_status, text, truncated, ocr}
//
// Why this exists at all: the web client never needed it. It attaches
// file_ids to a turn and the server folds the extracted text into the
// prompt itself (FileService.BuildContextBlock), so the text had no way
// out of the service. The DESKTOP agent runs its tool loop in-process —
// read_document has to RETURN the text as a value — and a scanned PDF
// can only be read by the server-side OCR pipeline. This route is the
// one piece that was missing.
//
// Deliberately NOT folded into GET /conversations/{id}/files: the web
// client polls that route every 1.5s while an upload extracts, and a
// 50 000-character document body in every poll response would make the
// progress spinner cost more bandwidth than the upload did.
//
// Egress class: a caller reads the text of a file the caller owns —
// the same bytes GET /files/{id}/download already hands the same user,
// in a different encoding. Ownership is FileService.Get's existing rule
// (someone else's file → ErrFileNotFound → 404 rather than 403, so the
// response can't be used to probe which file ids exist).
package handler

import (
	"context"
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

type fileTextResponse struct {
	// ExtractionStatus is the row's real status: pending | processing |
	// completed | failed | unsupported. Non-terminal means "poll again".
	ExtractionStatus string `json:"extraction_status"`
	Text             string `json:"text"`
	// Truncated is true when the stored text was capped by the extractor
	// (maxExtractedChars) — the client must not present it as the whole
	// document.
	Truncated bool `json:"truncated"`
	// OCR is true when at least one page came from OCR rather than a PDF
	// text layer, so the client can label the provenance.
	OCR bool `json:"ocr"`
}

// Provenance markers mirrored from the extraction pipeline
// (service.ocrPageMarker, and the suffix truncateChars appends).
// Mirrored rather than exported from service: drift here degrades two
// booleans to false and never touches the text itself, which is a
// cheaper failure than widening the service's API for two literals.
const (
	ocrTextMarker       = "[OCR] "
	truncatedTextMarker = "[Content truncated — showing first "
)

// fileTextSource is the slice of *service.FileService this endpoint
// uses. An interface so the handler is testable without a database.
type fileTextSource interface {
	Get(ctx context.Context, tenantID, userID, fileID uuid.UUID) (*model.ConversationFile, error)
}

func (h *FileHandler) Text(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	writeFileText(r.Context(), w, chi.URLParam(r, "fileID"),
		caller.TenantUUID, caller.UserID, h.files)
}

// writeFileText holds the whole handler body with the caller passed in,
// so tests can exercise both the owner and the not-the-owner path
// without standing up the auth middleware.
func writeFileText(
	ctx context.Context, w http.ResponseWriter, rawFileID string,
	tenantID, userID uuid.UUID, src fileTextSource,
) {
	fileID, err := uuid.Parse(rawFileID)
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id")
		return
	}

	f, err := src.Get(ctx, tenantID, userID, fileID)
	if err != nil {
		if errors.Is(err, service.ErrFileNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "file not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(fileTextFor(f))
}

// fileTextFor shapes one row for the wire.
//
// Text is sent ONLY for a completed extraction. While the row is
// pending/processing the field stays empty and the client polls
// extraction_status again; for failed/unsupported there is nothing to
// send. Pairing an empty string with a non-terminal status is what
// makes "not finished yet" distinguishable from "this document is
// blank" — a client that cannot tell those apart will summarise a
// scanned PDF as empty.
func fileTextFor(f *model.ConversationFile) fileTextResponse {
	out := fileTextResponse{ExtractionStatus: f.ExtractionStatus}
	if f.ExtractionStatus != "completed" || f.ExtractedText == nil {
		return out
	}
	out.Text = *f.ExtractedText
	out.OCR = strings.Contains(out.Text, ocrTextMarker)
	out.Truncated = strings.Contains(out.Text, truncatedTextMarker)
	return out
}
