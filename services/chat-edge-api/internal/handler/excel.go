// Excel Smart Workspace HTTP handlers (Phase 4 Session 1).
//
// Routes (all behind RequireAuth):
//
//	POST /api/v1/files/{fileID}/inspect
//	    Run the deep inspector against the uploaded xlsx and cache
//	    the structure JSON in the file row. Idempotent — re-running
//	    overwrites the cached JSON.
//
//	POST /api/v1/conversations/{convID}/excel/execute
//	    Apply an AI-generated edit plan to a source file. Produces
//	    a new generated child row; returns the file metadata + the
//	    validator report + the list of changes the executor made.
//
//	GET  /api/v1/conversations/{convID}/files/{fileID}/versions
//	    Walk the parent_file_id chain and list every iteration of a
//	    workbook. Tenant-scoped via the underlying repo.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type ExcelHandler struct {
	excel *service.ExcelService
	files *repository.FileRepo
}

func NewExcelHandler(excel *service.ExcelService, files *repository.FileRepo) *ExcelHandler {
	return &ExcelHandler{excel: excel, files: files}
}

// ---------------------------------------------------------------------------
// Inspect
// ---------------------------------------------------------------------------

// Inspect runs the deep inspector against the file and persists the
// structure JSON in the row. Response is the structure itself so the
// caller doesn't need a follow-up GET.
func (h *ExcelHandler) Inspect(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	fileID, err := uuid.Parse(chi.URLParam(r, "fileID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id")
		return
	}
	f, err := h.files.GetByID(r.Context(), caller.TenantUUID, fileID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "file not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	if f.UserID != caller.UserID {
		// Same-tenant-different-user — never expose existence.
		WriteError(w, http.StatusNotFound, CodeNotFound, "file not found")
		return
	}
	if !service.IsExcelContentType(f.ContentType) {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"file is not an Excel workbook")
		return
	}

	structure, raw, err := h.excel.InspectWorkbook(r.Context(), f.StoragePath)
	if err != nil {
		WriteError(w, http.StatusBadGateway, CodeInternalError,
			"could not inspect workbook: "+err.Error())
		return
	}
	// Cache for the next system-prompt build. Persist failures are
	// non-fatal — the structure response still goes out.
	_ = h.files.UpdateInspectionJSON(r.Context(), f.ID, string(raw))

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(structure)
}

// ---------------------------------------------------------------------------
// Execute plan
// ---------------------------------------------------------------------------

type executePlanRequest struct {
	// SourceFileID identifies the workbook the plan modifies. When
	// present it must belong to the conversation in the URL. OPTIONAL
	// since Phase 8: omitting it (or sending "") runs the executor in
	// create-from-scratch mode, the same contract /document/execute
	// already had for docx/pptx/pdf.
	SourceFileID string `json:"source_file_id"`
	// OutputFilename is optional; when empty the service derives
	// "<original>_edited.xlsx".
	OutputFilename string `json:"output_filename"`
	// Plan is the raw object the LLM emitted inside :::excel-plan.
	// Forwarded verbatim to excel_executor.py.
	Plan any `json:"plan"`
}

type executePlanResponse struct {
	File       fileResponse              `json:"file"`
	Validation *service.ValidationResult `json:"validation"`
	Changes    []string                  `json:"changes"`
}

func (h *ExcelHandler) Execute(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "convID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	var req executePlanRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Plan == nil {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"plan is required")
		return
	}
	// Empty / absent source_file_id → create-from-scratch. A NON-empty
	// but malformed id is still a 400: silently treating it as
	// "create new" would hide a client bug and produce a file the user
	// didn't ask for.
	var srcID uuid.UUID
	if strings.TrimSpace(req.SourceFileID) != "" {
		parsed, err := uuid.Parse(strings.TrimSpace(req.SourceFileID))
		if err != nil {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid source_file_id")
			return
		}
		srcID = parsed
	}

	row, validation, err := h.excel.ExecutePlan(r.Context(), service.PlannedExecution{
		TenantID:       caller.TenantUUID,
		ConversationID: convID,
		UserID:         caller.UserID,
		SourceFileID:   srcID,
		Plan:           req.Plan,
		OutputFilename: req.OutputFilename,
	})
	if err != nil {
		switch {
		case errors.Is(err, service.ErrExcelSourceNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "source file not found")
		case errors.Is(err, service.ErrExcelInvalidPlan):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		case errors.Is(err, service.ErrExcelValidatorFailed):
			// The output file failed validation. Surface as 422 so
			// the frontend can show the message without redirecting
			// to a generic 500 page.
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		case errors.Is(err, service.ErrExcelExecutorFailed),
			errors.Is(err, service.ErrExcelInspectorFailed):
			WriteError(w, http.StatusBadGateway, CodeInternalError, err.Error())
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}

	wire := FileToWire(row)
	resp := executePlanResponse{
		File:       wire,
		Validation: validation,
		Changes:    wire.ChangeLog,
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

// ---------------------------------------------------------------------------
// Version history
// ---------------------------------------------------------------------------

func (h *ExcelHandler) Versions(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "convID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	fileID, err := uuid.Parse(chi.URLParam(r, "fileID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id")
		return
	}
	versions, err := h.files.ListVersions(r.Context(), caller.TenantUUID, fileID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	// Filter to the requested conversation AND to the caller's user_id
	// so a leaked version id from another thread / another user in the
	// same tenant can't cross-link. Then compute is_current = the
	// highest-versioned generated row in the surviving slice (Session
	// 5: lets the timeline UI mark the leaf without requiring the
	// client to sort + pick).
	filtered := versions[:0]
	var currentID uuid.UUID
	currentVersion := -1
	for i := range versions {
		if versions[i].ConversationID != convID {
			continue
		}
		if versions[i].UserID != caller.UserID {
			continue
		}
		filtered = append(filtered, versions[i])
		if versions[i].IsGenerated && versions[i].Version >= currentVersion {
			currentVersion = versions[i].Version
			currentID = versions[i].ID
		}
	}

	type versionResponse struct {
		fileResponse
		IsCurrent bool `json:"is_current"`
	}
	out := make([]versionResponse, 0, len(filtered))
	for i := range filtered {
		f := &filtered[i]
		out = append(out, versionResponse{
			fileResponse: FileToWire(f),
			IsCurrent:    f.ID == currentID,
		})
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"versions": out})
}
