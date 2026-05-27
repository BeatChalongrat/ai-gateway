// DocService — Phase 4 Session 2 — Word/PDF Smart Report Generation.
//
// Sibling of ExcelService. Three jobs:
//
//	Inspect  — runs scripts/docx_inspector.py or pdf_inspector.py
//	           depending on content type. Output JSON is cached in
//	           chat.conversation_files.inspection_json so the
//	           system prompt can include the document structure
//	           without re-shelling.
//	Execute  — takes a JSON plan + a desired format ("docx" |
//	           "pdf"), runs the matching generator on a temp dir,
//	           validates the output, uploads to storage, and writes
//	           a new conversation_files row as a generated child of
//	           the source (when source_file_id is provided) or as a
//	           standalone generated row (when from-scratch).
//	Validate — runs scripts/doc_validator.py against any docx/pdf,
//	           returns valid / errors / warnings / stats.
//
// Originals in storage are read-only; every generated file becomes
// a new versioned child so the user can step through iterations.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

const (
	docxContentType = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
	pdfContentType  = "application/pdf"

	docxInspectorScript = "scripts/docx_inspector.py"
	pdfInspectorScript  = "scripts/pdf_inspector.py"
	docxGeneratorScript = "scripts/docx_generator.py"
	pdfGeneratorScript  = "scripts/pdf_generator.py"
	docValidatorScript  = "scripts/doc_validator.py"

	docInspectorTimeout = 60 * time.Second
	docGeneratorTimeout = 120 * time.Second
	docValidatorTimeout = 60 * time.Second
)

// IsDocxContentType / IsPdfContentType / IsDocContentType report
// whether a content type belongs to a doc family the auto-inspect
// hook + system-prompt injector should treat specially. Exported so
// FileService.Upload + MessageService.docContext can branch without
// reaching into the const strings.
func IsDocxContentType(ct string) bool {
	return strings.EqualFold(strings.TrimSpace(ct), docxContentType)
}
func IsPdfContentType(ct string) bool {
	return strings.EqualFold(strings.TrimSpace(ct), pdfContentType)
}
func IsDocContentType(ct string) bool {
	return IsDocxContentType(ct) || IsPdfContentType(ct)
}

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

var (
	ErrDocInspectorFailed = errors.New("doc: inspector failed")
	ErrDocGeneratorFailed = errors.New("doc: generator failed")
	ErrDocValidatorFailed = errors.New("doc: validator failed")
	ErrDocSourceNotFound  = errors.New("doc: source file not found")
	ErrDocInvalidPlan     = errors.New("doc: invalid plan")
	ErrDocUnsupported     = errors.New("doc: unsupported file type")
	ErrDocInvalidFormat   = errors.New("doc: format must be 'docx' or 'pdf'")
)

// ---------------------------------------------------------------------------
// Wire types — mirror the Python script outputs 1:1.
// ---------------------------------------------------------------------------

// DocStructure is a permissive wrapper around either docx or pdf
// inspection output. Per-format detail lives in the embedded map so
// the frontend can render whichever subset it understands.
type DocStructure struct {
	FileType string         `json:"file_type"`
	Raw      map[string]any `json:"-"`
}

// DocGenerationResult mirrors the change_log + counters returned
// by docx_generator.py / pdf_generator.py.
type DocGenerationResult struct {
	Success         bool             `json:"success"`
	Mode            string           `json:"mode,omitempty"`
	OutputPath      string           `json:"output_path"`
	FileSizeBytes   int64            `json:"file_size_bytes"`
	PageCount       int              `json:"page_count,omitempty"`
	FontUsed        string           `json:"font_used,omitempty"`
	ActionsExecuted int              `json:"actions_executed"`
	ActionsFailed   int              `json:"actions_failed"`
	ChangeLog       []map[string]any `json:"change_log"`
}

// DocValidationResult mirrors doc_validator.py output.
type DocValidationResult struct {
	Valid         bool           `json:"valid"`
	FileType      string         `json:"file_type"`
	FileSizeBytes int64          `json:"file_size_bytes"`
	Errors        []string       `json:"errors"`
	Warnings      []string       `json:"warnings"`
	Stats         map[string]int `json:"stats"`
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

type DocService struct {
	files   *repository.FileRepo
	storage storage.Backend

	pythonBin  string
	scriptsDir string
}

func NewDocService(
	files *repository.FileRepo, store storage.Backend, pythonBin, scriptsDir string,
) *DocService {
	if pythonBin == "" {
		pythonBin = "python3"
	}
	if scriptsDir == "" {
		scriptsDir = "scripts"
	}
	return &DocService{
		files:      files,
		storage:    store,
		pythonBin:  pythonBin,
		scriptsDir: scriptsDir,
	}
}

// scriptPath mirrors ExcelService.scriptPath — resolves to abs so
// exec doesn't depend on cwd.
func (s *DocService) scriptPath(name string) (string, error) {
	candidate := name
	if !filepath.IsAbs(candidate) {
		if !strings.Contains(name, string(filepath.Separator)) && !strings.Contains(name, "/") {
			candidate = filepath.Join(s.scriptsDir, name)
		}
		abs, err := filepath.Abs(candidate)
		if err != nil {
			return "", err
		}
		candidate = abs
	}
	if _, err := os.Stat(candidate); err != nil {
		return "", fmt.Errorf("script not found at %s: %w", candidate, err)
	}
	return candidate, nil
}

func (s *DocService) runScript(
	parent context.Context, script string, timeout time.Duration, args ...string,
) ([]byte, error) {
	abs, err := s.scriptPath(script)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, s.pythonBin, append([]string{abs}, args...)...)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		slog.Warn("doc script failed",
			"script", script, "err", err, "stderr", stderr.String())
		return nil, err
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// Inspect
// ---------------------------------------------------------------------------

// InspectDocument runs the inspector matching the file's content
// type. Returns (parsed, raw bytes). The raw bytes are what the
// caller persists in inspection_json — preserving the exact JSON
// the LLM will see in the system prompt.
func (s *DocService) InspectDocument(
	ctx context.Context, storageKey, contentType string,
) (*DocStructure, []byte, error) {
	var script string
	switch {
	case IsDocxContentType(contentType):
		script = docxInspectorScript
	case IsPdfContentType(contentType):
		script = pdfInspectorScript
	default:
		return nil, nil, fmt.Errorf("%w: %s", ErrDocUnsupported, contentType)
	}

	path, cleanup, err := s.storage.OpenLocalCopy(ctx, storageKey)
	if err != nil {
		return nil, nil, fmt.Errorf("%w: %v", ErrDocSourceNotFound, err)
	}
	defer cleanup()

	out, err := s.runScript(ctx, script, docInspectorTimeout, path)
	if err != nil {
		return nil, nil, fmt.Errorf("%w: %v", ErrDocInspectorFailed, err)
	}
	var probe map[string]any
	if err := json.Unmarshal(out, &probe); err != nil {
		return nil, nil, fmt.Errorf("%w: decode: %v", ErrDocInspectorFailed, err)
	}
	if msg, ok := probe["error"].(string); ok && msg != "" {
		return nil, nil, fmt.Errorf("%w: %s", ErrDocInspectorFailed, msg)
	}
	ft, _ := probe["file_type"].(string)
	return &DocStructure{FileType: ft, Raw: probe}, out, nil
}

// ---------------------------------------------------------------------------
// Execute
// ---------------------------------------------------------------------------

// DocExecution is the input to ExecutePlan. SourceFileID is
// optional — when zero we're generating from scratch (e.g. the
// user asked "create a report" without uploading anything).
// Format must be "docx" or "pdf".
type DocExecution struct {
	TenantID       uuid.UUID
	ConversationID uuid.UUID
	UserID         uuid.UUID
	SourceFileID   uuid.UUID // uuid.Nil → from scratch
	Plan           any
	Format         string // "docx" | "pdf"
	OutputFilename string
}

// ExecutePlan runs the appropriate generator inside a temp dir,
// validates the output, uploads it, and creates a new
// conversation_files row.
func (s *DocService) ExecutePlan(
	ctx context.Context, in DocExecution,
) (*model.ConversationFile, *DocValidationResult, *DocGenerationResult, error) {
	format := strings.ToLower(strings.TrimSpace(in.Format))
	if format != "docx" && format != "pdf" {
		return nil, nil, nil, ErrDocInvalidFormat
	}

	var source *model.ConversationFile
	if in.SourceFileID != uuid.Nil {
		s2, err := s.files.GetByID(ctx, in.TenantID, in.SourceFileID)
		if err != nil {
			if errors.Is(err, repository.ErrNotFound) {
				return nil, nil, nil, ErrDocSourceNotFound
			}
			return nil, nil, nil, err
		}
		if s2.ConversationID != in.ConversationID {
			return nil, nil, nil, ErrDocSourceNotFound
		}
		source = s2
	}

	tmp, err := os.MkdirTemp("", "doc-exec-*")
	if err != nil {
		return nil, nil, nil, fmt.Errorf("temp dir: %w", err)
	}
	defer os.RemoveAll(tmp)

	planPath := filepath.Join(tmp, "plan.json")
	planBytes, err := json.Marshal(in.Plan)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("%w: marshal: %v", ErrDocInvalidPlan, err)
	}
	if err := os.WriteFile(planPath, planBytes, 0o600); err != nil {
		return nil, nil, nil, fmt.Errorf("write plan: %w", err)
	}

	outPath := filepath.Join(tmp, "output."+format)

	var script string
	switch format {
	case "docx":
		script = docxGeneratorScript
	case "pdf":
		script = pdfGeneratorScript
	}

	args := []string{planPath, outPath}
	// docx supports an optional source argument for the edit path;
	// pdf is always create-from-scratch (the user can re-feed an
	// existing PDF's inspection back to the LLM to regenerate).
	if format == "docx" && source != nil {
		srcPath, cleanup, err := s.storage.OpenLocalCopy(ctx, source.StoragePath)
		if err != nil {
			return nil, nil, nil, fmt.Errorf("source download: %w", err)
		}
		defer cleanup()
		args = append(args, srcPath)
	}

	out, err := s.runScript(ctx, script, docGeneratorTimeout, args...)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("%w: %v", ErrDocGeneratorFailed, err)
	}
	var genResult DocGenerationResult
	if err := json.Unmarshal(out, &genResult); err != nil {
		return nil, nil, nil, fmt.Errorf("%w: decode: %v", ErrDocGeneratorFailed, err)
	}
	if !genResult.Success {
		return nil, nil, nil, fmt.Errorf("%w: script reported failure", ErrDocGeneratorFailed)
	}
	if _, err := os.Stat(outPath); err != nil {
		return nil, nil, nil, fmt.Errorf("%w: output missing: %v", ErrDocGeneratorFailed, err)
	}

	validation, err := s.validatePath(ctx, outPath)
	if err != nil {
		return nil, nil, nil, err
	}
	if !validation.Valid {
		return nil, nil, nil, fmt.Errorf("%w: %s",
			ErrDocValidatorFailed, strings.Join(validation.Errors, "; "))
	}

	outFilename := strings.TrimSpace(in.OutputFilename)
	if outFilename == "" {
		if source != nil {
			outFilename = deriveGeneratedFilename(source.OriginalFilename, format)
		} else {
			outFilename = fmt.Sprintf("report_%s.%s", time.Now().UTC().Format("20060102_150405"), format)
		}
	}
	outFilename = sanitizeFilename(outFilename)

	newFileID := uuid.New()
	storageName := fmt.Sprintf("%s_%s", newFileID.String(), outFilename)
	newKey := storage.JoinKey(
		conversationFilesKeyPrefix,
		in.TenantID.String(),
		in.ConversationID.String(),
		storageName,
	)
	body, err := os.Open(outPath)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("open output: %w", err)
	}
	defer body.Close()
	info, err := body.Stat()
	if err != nil {
		return nil, nil, nil, fmt.Errorf("stat output: %w", err)
	}
	contentType := docxContentType
	if format == "pdf" {
		contentType = pdfContentType
	}
	if err := s.storage.Upload(ctx, newKey, body, contentType, info.Size()); err != nil {
		return nil, nil, nil, fmt.Errorf("upload output: %w", err)
	}

	// Run the inspector against the output so the next AI turn sees
	// the post-generation structure without an extra round-trip.
	var inspectionPtr *string
	if _, raw, ierr := s.InspectDocument(ctx, newKey, contentType); ierr == nil && len(raw) > 0 {
		sj := string(raw)
		inspectionPtr = &sj
	}

	var parentID *uuid.UUID
	version := 1
	if source != nil {
		pid := source.ID
		parentID = &pid
		version = source.Version + 1
	}

	// Flatten the per-action change_log into a newline-joined
	// list of "<status>: <type> — <detail>" lines so the
	// frontend can render it the same way the Excel result card
	// does (one bullet per change).
	flat := flattenDocChangeLog(genResult.ChangeLog)
	changeLog := strings.Join(flat, "\n")

	row, err := s.files.Create(ctx, &model.ConversationFile{
		ConversationID:   in.ConversationID,
		TenantID:         in.TenantID,
		Filename:         storageName,
		OriginalFilename: outFilename,
		ContentType:      contentType,
		FileSizeBytes:    info.Size(),
		StoragePath:      newKey,
		ExtractionStatus: "completed",
		IsGenerated:      true,
		ParentFileID:     parentID,
		Version:          version,
		ChangeLog:        &changeLog,
		InspectionJSON:   inspectionPtr,
	})
	if err != nil {
		_ = s.storage.Delete(ctx, newKey)
		return nil, nil, nil, fmt.Errorf("persist generated row: %w", err)
	}
	return row, validation, &genResult, nil
}

// ---------------------------------------------------------------------------
// Validate
// ---------------------------------------------------------------------------

// ValidateDocument is the HTTP-facing entry point — looks up the
// file by id, materialises it, runs the validator.
func (s *DocService) ValidateDocument(
	ctx context.Context, tenantID, fileID uuid.UUID,
) (*DocValidationResult, error) {
	f, err := s.files.GetByID(ctx, tenantID, fileID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrDocSourceNotFound
		}
		return nil, err
	}
	path, cleanup, err := s.storage.OpenLocalCopy(ctx, f.StoragePath)
	if err != nil {
		return nil, fmt.Errorf("download for validation: %w", err)
	}
	defer cleanup()
	return s.validatePath(ctx, path)
}

func (s *DocService) validatePath(ctx context.Context, path string) (*DocValidationResult, error) {
	out, err := s.runScript(ctx, docValidatorScript, docValidatorTimeout, path)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrDocValidatorFailed, err)
	}
	var v DocValidationResult
	if err := json.Unmarshal(out, &v); err != nil {
		return nil, fmt.Errorf("%w: decode: %v", ErrDocValidatorFailed, err)
	}
	return &v, nil
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// deriveGeneratedFilename suffixes the source with "_edited" before
// the extension and rewrites the extension to the new format. Used
// only when the plan doesn't supply output_filename.
func deriveGeneratedFilename(original, format string) string {
	name := strings.TrimSpace(original)
	if name == "" {
		return "report_edited." + format
	}
	ext := filepath.Ext(name)
	base := strings.TrimSuffix(name, ext)
	return base + "_edited." + format
}

// flattenDocChangeLog turns the python generator's per-action
// objects into a list of one-line summaries.
func flattenDocChangeLog(entries []map[string]any) []string {
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		status, _ := e["status"].(string)
		atype, _ := e["type"].(string)
		detail, _ := e["detail"].(string)
		prefix := "ok"
		switch status {
		case "error":
			prefix = "ERROR"
		case "skipped":
			prefix = "skipped"
		}
		if detail == "" {
			out = append(out, fmt.Sprintf("%s: %s", prefix, atype))
		} else {
			out = append(out, fmt.Sprintf("%s: %s — %s", prefix, atype, detail))
		}
	}
	return out
}
