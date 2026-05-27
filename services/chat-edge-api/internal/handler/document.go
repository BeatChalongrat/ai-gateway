// Document HTTP handlers (Phase 4 Session 2).
//
// Sibling of excel.go. Two responsibilities:
//
//	Inspect — POST /api/v1/files/{fileID}/inspect (registered in
//	          place of the Excel-only inspector). Dispatches by
//	          file content type: xlsx → ExcelService, docx/pdf →
//	          DocService. Caches the JSON in inspection_json.
//
//	Execute — POST /api/v1/conversations/{convID}/document/execute
//	          Body: { source_file_id?, plan, format, output_filename? }
//	          Runs the right Python generator on a temp copy,
//	          validates, persists as a generated versioned child.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// DocumentHandler owns the type-dispatching inspect route and the
// docx/pdf execute route. ExcelHandler keeps its Excel-only
// execute + versions routes (they touch Excel-specific fields like
// preserve+actions catalog the doc plan doesn't share).
type DocumentHandler struct {
	files *repository.FileRepo
	excel *service.ExcelService // for xlsx → InspectWorkbook
	docs  *service.DocService   // for docx/pdf → Inspect+Execute
}

func NewDocumentHandler(
	files *repository.FileRepo, excel *service.ExcelService, docs *service.DocService,
) *DocumentHandler {
	return &DocumentHandler{files: files, excel: excel, docs: docs}
}

// ---------------------------------------------------------------------------
// Inspect — dispatcher
// ---------------------------------------------------------------------------

// Inspect picks the right Python inspector based on content type
// and caches the raw JSON in chat.conversation_files.inspection_json.
// Response body is the parsed structure so callers don't need a
// follow-up GET. Unsupported types return 422.
func (h *DocumentHandler) Inspect(w http.ResponseWriter, r *http.Request) {
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

	switch {
	case service.IsExcelContentType(f.ContentType):
		structure, raw, err := h.excel.InspectWorkbook(r.Context(), f.StoragePath)
		if err != nil {
			WriteError(w, http.StatusBadGateway, CodeInternalError,
				"could not inspect workbook: "+err.Error())
			return
		}
		_ = h.files.UpdateInspectionJSON(r.Context(), f.ID, string(raw))
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(structure)
	case service.IsDocContentType(f.ContentType):
		structure, raw, err := h.docs.InspectDocument(r.Context(), f.StoragePath, f.ContentType)
		if err != nil {
			WriteError(w, http.StatusBadGateway, CodeInternalError,
				"could not inspect document: "+err.Error())
			return
		}
		_ = h.files.UpdateInspectionJSON(r.Context(), f.ID, string(raw))
		// Send the raw map so the frontend gets every per-format
		// field (page_count, sheets, etc.) without us re-shaping.
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(structure.Raw)
	default:
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"file type not supported for inspection")
	}
}

// ---------------------------------------------------------------------------
// Execute — docx/pdf generation
// ---------------------------------------------------------------------------

type executeDocRequest struct {
	// SourceFileID is optional. When omitted (or zero UUID), the
	// generator runs in create-from-scratch mode.
	SourceFileID string `json:"source_file_id"`
	// Format is required: "docx" or "pdf".
	Format string `json:"format"`
	// OutputFilename is optional; the service derives a
	// timestamped name when empty.
	OutputFilename string `json:"output_filename"`
	// Plan is the raw object the LLM emitted inside :::doc-plan.
	Plan any `json:"plan"`
}

type executeDocResponse struct {
	File       fileResponse                  `json:"file"`
	Validation *service.DocValidationResult  `json:"validation"`
	Generation *service.DocGenerationResult  `json:"generation"`
	Changes    []string                      `json:"changes"`
}

func (h *DocumentHandler) Execute(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "convID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	var req executeDocRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Plan == nil {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "plan is required")
		return
	}
	if req.Format == "" {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "format is required")
		return
	}
	var srcID uuid.UUID
	if req.SourceFileID != "" {
		parsed, err := uuid.Parse(req.SourceFileID)
		if err != nil {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid source_file_id")
			return
		}
		srcID = parsed
	}

	row, validation, generation, err := h.docs.ExecutePlan(r.Context(), service.DocExecution{
		TenantID:       caller.TenantUUID,
		ConversationID: convID,
		UserID:         caller.UserID,
		SourceFileID:   srcID,
		Plan:           req.Plan,
		Format:         req.Format,
		OutputFilename: req.OutputFilename,
	})
	if err != nil {
		switch {
		case errors.Is(err, service.ErrDocSourceNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "source file not found")
		case errors.Is(err, service.ErrDocInvalidPlan),
			errors.Is(err, service.ErrDocInvalidFormat),
			errors.Is(err, service.ErrDocUnsupported):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		case errors.Is(err, service.ErrDocValidatorFailed):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		case errors.Is(err, service.ErrDocGeneratorFailed),
			errors.Is(err, service.ErrDocInspectorFailed):
			WriteError(w, http.StatusBadGateway, CodeInternalError, err.Error())
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}

	wire := FileToWire(row)
	resp := executeDocResponse{
		File:       wire,
		Validation: validation,
		Generation: generation,
		Changes:    wire.ChangeLog,
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}
