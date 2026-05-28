// Account handlers — S9 Enterprise Hardening.
//
// Routes (all behind RequireAuth):
//
//	POST   /api/v1/account/export-memories
//	    Generates a fresh JSON export of every memory the caller
//	    owns. Returns the export id + filename + expiry; the file
//	    sits on local storage and is fetched via the download
//	    endpoint below.
//
//	GET    /api/v1/account/exports/{id}/download
//	    Streams the export JSON file back to the user. Ownership
//	    verified via the export row's user_id + tenant_id.
//	    Returns 404 once the file expires (24h post-generation).
//
//	DELETE /api/v1/account/purge-memories
//	    GDPR right-to-erasure. HARD deletes every memory-related
//	    row the user owns plus any export files. Requires the
//	    literal confirmation string in the request body as a
//	    second factor against accidental cascades.
package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// PurgeConfirmationPhrase is the literal string the user must
// echo in the purge request body. Acts as a second factor — a
// typo in the front-end "are you sure?" dialog should NOT
// cascade-delete a user's entire memory history.
const PurgeConfirmationPhrase = "DELETE ALL MY MEMORIES"

type AccountHandler struct {
	exports *service.ExportService
	purge   *service.PurgeService
}

func NewAccountHandler(exp *service.ExportService, pur *service.PurgeService) *AccountHandler {
	return &AccountHandler{exports: exp, purge: pur}
}

// ---------------------------------------------------------------------------
// POST /account/export-memories
// ---------------------------------------------------------------------------

type exportResponse struct {
	ExportID  string `json:"export_id"`
	Filename  string `json:"filename"`
	SizeBytes int64  `json:"size_bytes"`
	ExpiresAt string `json:"expires_at"`
}

func (h *AccountHandler) ExportMemories(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	res, err := h.exports.ExportUserMemories(r.Context(), caller.UserID, caller.TenantUUID)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrExportDisabled):
			WriteError(w, http.StatusForbidden, CodeValidationError,
				"data export is disabled for this tenant")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "export failed")
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(exportResponse{
		ExportID:  res.ExportID.String(),
		Filename:  res.Filename,
		SizeBytes: res.SizeBytes,
		ExpiresAt: res.ExpiresAt.UTC().Format("2006-01-02T15:04:05Z"),
	})
}

// ---------------------------------------------------------------------------
// GET /account/exports/{id}/download
// ---------------------------------------------------------------------------

func (h *AccountHandler) DownloadExport(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid export id")
		return
	}
	row, file, err := h.exports.OpenForDownload(r.Context(), id, caller.UserID, caller.TenantUUID)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrExportNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "export not found")
		case errors.Is(err, service.ErrExportExpired),
			errors.Is(err, service.ErrExportFileMissing):
			WriteError(w, http.StatusGone, CodeNotFound, "export file no longer available")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "download failed")
		}
		return
	}
	defer file.Close()
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Disposition", `attachment; filename="`+sanitiseFilename(row.ID.String())+`.json"`)
	_, _ = io.Copy(w, file)
}

// sanitiseFilename is a tiny ASCII-only cleaner so a download
// header never carries a stray quote / control char that would
// break Content-Disposition parsing in older browsers.
func sanitiseFilename(s string) string {
	out := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		c := s[i]
		if (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') ||
			(c >= 'A' && c <= 'Z') || c == '-' || c == '_' || c == '.' {
			out = append(out, c)
		}
	}
	if len(out) == 0 {
		return "export"
	}
	return string(out)
}

// ---------------------------------------------------------------------------
// DELETE /account/purge-memories
// ---------------------------------------------------------------------------

type purgeRequest struct {
	Confirm string `json:"confirm"`
}

func (h *AccountHandler) PurgeMemories(w http.ResponseWriter, r *http.Request) {
	caller := middleware.MustCaller(r.Context())
	var req purgeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Confirm != PurgeConfirmationPhrase {
		WriteError(w, http.StatusBadRequest, CodeValidationError,
			"confirmation string must be exactly: "+PurgeConfirmationPhrase)
		return
	}
	res, err := h.purge.PurgeAllUserMemories(r.Context(), caller.UserID, caller.TenantUUID)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrDeletionDisabled):
			WriteError(w, http.StatusForbidden, CodeValidationError,
				"data deletion is disabled for this tenant")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError,
				"purge failed (partial deletion may have occurred)")
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(res)
}
