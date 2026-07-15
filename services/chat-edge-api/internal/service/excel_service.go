// ExcelService — Phase 4 Session 1 — Excel Smart Workspace.
//
// Three jobs:
//
//	Inspect  — runs scripts/excel_inspector.py against an uploaded
//	           xlsx, returns the deep structure JSON. Cached in
//	           chat.conversation_files.inspection_json by the caller
//	           so re-prompts don't re-shell.
//	Execute  — takes an edit plan (JSON the LLM emitted), runs
//	           scripts/excel_executor.py against a COPY of the source
//	           file, uploads the output to storage as a new
//	           conversation_files row (is_generated=true,
//	           parent_file_id=source, version=parent.version+1).
//	Validate — runs scripts/excel_validator.py against any xlsx,
//	           returns a structured report (valid / issues /
//	           warnings / stats).
//
// All three commands operate on a temp dir provisioned per-call and
// cleaned up via defer. The originals in storage are read-only — every
// edit lands as a brand-new row so the user can step through versions.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

const (
	xlsxContentType  = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
	inspectorScript  = "scripts/excel_inspector.py"
	executorScript   = "scripts/excel_executor.py"
	validatorScript  = "scripts/excel_validator.py"
	inspectorTimeout = 60 * time.Second
	executorTimeout  = 90 * time.Second
	validatorTimeout = 60 * time.Second
)

// IsExcelContentType reports whether a content type corresponds to an
// .xlsx workbook. Exported so the upload hook in file_service can
// trigger auto-inspection without re-knowing the MIME string.
func IsExcelContentType(ct string) bool {
	return strings.EqualFold(strings.TrimSpace(ct), xlsxContentType)
}

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

var (
	ErrExcelInspectorFailed = errors.New("excel: inspector failed")
	ErrExcelExecutorFailed  = errors.New("excel: executor failed")
	ErrExcelValidatorFailed = errors.New("excel: validator failed")
	ErrExcelSourceNotFound  = errors.New("excel: source file not found")
	ErrExcelInvalidPlan     = errors.New("excel: invalid plan")
)

// ---------------------------------------------------------------------------
// Wire types — mirror the Python script outputs 1:1 so the handler can
// JSON-encode them straight to the client without a second mapping
// layer. Fields stay map[string]any where the structure is too rich
// to spell out in Go and the frontend only renders specific keys.
// ---------------------------------------------------------------------------

type WorkbookStructure struct {
	Filename    string            `json:"filename"`
	SheetCount  int               `json:"sheet_count"`
	Sheets      []map[string]any  `json:"sheets"`
	NamedRanges []string          `json:"named_ranges"`
	HasMacros   bool              `json:"has_macros"`
	Properties  map[string]string `json:"properties"`
}

type ExecutionResult struct {
	Success     bool     `json:"success"`
	OutputPath  string   `json:"output_file"`
	Changes     []string `json:"changes"`
	ActionCount int      `json:"action_count"`
}

type ValidationResult struct {
	Valid    bool           `json:"valid"`
	Issues   []string       `json:"issues"`
	Warnings []string       `json:"warnings"`
	Stats    map[string]int `json:"stats"`
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

type ExcelService struct {
	files   *repository.FileRepo
	storage storage.Backend

	pythonBin  string
	scriptsDir string // dir containing excel_*.py; falls back to "scripts"

	// sandbox, when non-nil, routes the plan-executor (model-authored
	// plan) through the hardened Phase 13 sandbox instead of running
	// python in-process-with-secrets. Injected via SetSandbox from
	// main.go when SANDBOX_ENABLED. nil → legacy in-process path.
	sandbox *sandbox.Runner
}

// SetSandbox migrates the plan-executor into the Phase 13 hardened
// sandbox. Nil-safe: leaving it unset keeps the legacy in-process
// behaviour (dev without docker-in-docker wiring).
func (s *ExcelService) SetSandbox(r *sandbox.Runner) { s.sandbox = r }

func NewExcelService(
	files *repository.FileRepo, store storage.Backend, pythonBin, scriptsDir string,
) *ExcelService {
	if pythonBin == "" {
		pythonBin = "python3"
	}
	if scriptsDir == "" {
		scriptsDir = "scripts"
	}
	return &ExcelService{
		files:      files,
		storage:    store,
		pythonBin:  pythonBin,
		scriptsDir: scriptsDir,
	}
}

// scriptPath resolves to an absolute path so exec.CommandContext
// doesn't depend on the cwd of the running process — works the same
// in tests, `go run ./cmd/server`, and the prod container.
func (s *ExcelService) scriptPath(name string) (string, error) {
	candidate := name
	if !filepath.IsAbs(candidate) {
		// scriptsDir + base name; if scriptsDir is already a path
		// (e.g. "scripts") prefix it, otherwise treat the supplied
		// "scripts/excel_inspector.py" verbatim.
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

// runScript executes a Python script with the provided args, enforces
// a timeout, and returns trimmed stdout. Stderr is captured so the
// log entry on failure has the Python traceback.
func (s *ExcelService) runScript(
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
		slog.Warn("excel script failed",
			"script", script, "err", err, "stderr", stderr.String())
		return nil, err
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// Inspect
// ---------------------------------------------------------------------------

// InspectWorkbook downloads the file at storageKey to a temp path,
// runs the inspector, and returns the parsed structure JSON. Used by
// FileService.Upload (auto-inspect hook) and by the HTTP
// /files/{id}/inspect endpoint.
//
// The returned []byte is the raw JSON the script printed — callers
// store it verbatim in inspection_json so the structure can be
// re-injected into system prompts without re-encoding.
func (s *ExcelService) InspectWorkbook(
	ctx context.Context, storageKey string,
) (*WorkbookStructure, []byte, error) {
	path, cleanup, err := s.storage.OpenLocalCopy(ctx, storageKey)
	if err != nil {
		return nil, nil, fmt.Errorf("%w: %v", ErrExcelSourceNotFound, err)
	}
	defer cleanup()

	out, err := s.runScript(ctx, inspectorScript, inspectorTimeout, path)
	if err != nil {
		return nil, nil, fmt.Errorf("%w: %v", ErrExcelInspectorFailed, err)
	}
	// The script writes a `{"error": "..."}` envelope on internal
	// failure with exit code 1; exec catches the exit code already,
	// but a probe here protects against a future script that exits
	// 0 with an error envelope.
	var probe map[string]any
	if err := json.Unmarshal(out, &probe); err != nil {
		return nil, nil, fmt.Errorf("%w: decode: %v", ErrExcelInspectorFailed, err)
	}
	if msg, ok := probe["error"].(string); ok && msg != "" {
		return nil, nil, fmt.Errorf("%w: %s", ErrExcelInspectorFailed, msg)
	}
	var ws WorkbookStructure
	if err := json.Unmarshal(out, &ws); err != nil {
		return nil, nil, fmt.Errorf("%w: structure decode: %v", ErrExcelInspectorFailed, err)
	}
	return &ws, out, nil
}

// ---------------------------------------------------------------------------
// Execute
// ---------------------------------------------------------------------------

// PlannedExecution is the input to ExecutePlan — the plan JSON the
// LLM produced inside the :::excel-plan fence, plus the source file
// it should mutate. SourceFileID and TenantID let the service look
// up the storage key + clone safety metadata; the handler resolves
// the tenant from the caller and passes both verbatim.
type PlannedExecution struct {
	TenantID       uuid.UUID
	ConversationID uuid.UUID
	UserID         uuid.UUID
	SourceFileID   uuid.UUID
	// Plan is the parsed JSON object — `any` because the schema is
	// open-ended (see excel_executor.py for the recognised types).
	Plan any
	// OutputFilename is the user-friendly filename for the new
	// file. When empty we derive "<original>_edited.xlsx".
	OutputFilename string
}

// ExecutePlan runs the executor against a fresh copy of the source,
// validates the result, persists the output to storage, and creates a
// new conversation_files row marked as a generated child of the
// source. Returns the populated child row + the validation report so
// the handler can surface both to the client in one response.
func (s *ExcelService) ExecutePlan(
	ctx context.Context, in PlannedExecution,
) (*model.ConversationFile, *ValidationResult, error) {
	source, err := s.files.GetByID(ctx, in.TenantID, in.SourceFileID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, nil, ErrExcelSourceNotFound
		}
		return nil, nil, err
	}
	if source.ConversationID != in.ConversationID {
		// Don't leak cross-conversation existence.
		return nil, nil, ErrExcelSourceNotFound
	}
	if source.UserID != in.UserID {
		// Same-tenant-different-user — never returns 403, always 404
		// shape so the existence of the file isn't probeable.
		return nil, nil, ErrExcelSourceNotFound
	}

	tmp, err := os.MkdirTemp("", "excel-exec-*")
	if err != nil {
		return nil, nil, fmt.Errorf("temp dir: %w", err)
	}
	defer os.RemoveAll(tmp)

	srcPath, cleanup, err := s.storage.OpenLocalCopy(ctx, source.StoragePath)
	if err != nil {
		return nil, nil, fmt.Errorf("source download: %w", err)
	}
	defer cleanup()

	planPath := filepath.Join(tmp, "plan.json")
	planBytes, err := json.Marshal(in.Plan)
	if err != nil {
		return nil, nil, fmt.Errorf("%w: marshal: %v", ErrExcelInvalidPlan, err)
	}
	if err := os.WriteFile(planPath, planBytes, 0o600); err != nil {
		return nil, nil, fmt.Errorf("write plan: %w", err)
	}

	outPath := filepath.Join(tmp, "output.xlsx")
	out, err := s.runExecutor(ctx, planBytes, planPath, srcPath, outPath)
	if err != nil {
		return nil, nil, fmt.Errorf("%w: %v", ErrExcelExecutorFailed, err)
	}
	var execResult ExecutionResult
	if err := json.Unmarshal(out, &execResult); err != nil {
		return nil, nil, fmt.Errorf("%w: decode: %v", ErrExcelExecutorFailed, err)
	}
	if !execResult.Success {
		return nil, nil, fmt.Errorf("%w: script reported failure", ErrExcelExecutorFailed)
	}
	if _, err := os.Stat(outPath); err != nil {
		return nil, nil, fmt.Errorf("%w: output missing: %v", ErrExcelExecutorFailed, err)
	}

	// Validate before upload — if the file's broken we abort and
	// never expose a download URL for a workbook the user can't open.
	validation, err := s.validatePath(ctx, outPath)
	if err != nil {
		return nil, nil, err
	}
	if !validation.Valid {
		return nil, nil, fmt.Errorf("%w: %s",
			ErrExcelValidatorFailed, strings.Join(validation.Issues, "; "))
	}

	// Determine the output filename (user-supplied wins).
	outFilename := strings.TrimSpace(in.OutputFilename)
	if outFilename == "" {
		outFilename = deriveEditedFilename(source.OriginalFilename)
	}
	outFilename = sanitizeFilename(outFilename)

	// Upload to storage under a new key in the same conversation
	// hierarchy.
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
		return nil, nil, fmt.Errorf("open output: %w", err)
	}
	defer body.Close()
	info, err := body.Stat()
	if err != nil {
		return nil, nil, fmt.Errorf("stat output: %w", err)
	}
	if err := s.storage.Upload(ctx, newKey, body, xlsxContentType, info.Size()); err != nil {
		return nil, nil, fmt.Errorf("upload output: %w", err)
	}

	// Run the inspector on the output so the next AI turn sees the
	// post-edit structure without an extra round-trip.
	var inspectionPtr *string
	if _, raw, ierr := s.InspectWorkbook(ctx, newKey); ierr == nil && len(raw) > 0 {
		s := string(raw)
		inspectionPtr = &s
	}

	parentID := source.ID
	changeLog := strings.Join(execResult.Changes, "\n")
	row, err := s.files.Create(ctx, &model.ConversationFile{
		ConversationID:   in.ConversationID,
		TenantID:         in.TenantID,
		UserID:           in.UserID,
		Filename:         storageName,
		OriginalFilename: outFilename,
		ContentType:      xlsxContentType,
		FileSizeBytes:    info.Size(),
		StoragePath:      newKey,
		ExtractionStatus: "completed",
		IsGenerated:      true,
		ParentFileID:     &parentID,
		Version:          source.Version + 1,
		ChangeLog:        &changeLog,
		InspectionJSON:   inspectionPtr,
	})
	if err != nil {
		_ = s.storage.Delete(ctx, newKey)
		return nil, nil, fmt.Errorf("persist generated row: %w", err)
	}
	return row, validation, nil
}

// runExecutor runs the excel plan-executor. When a sandbox is wired it
// runs inside the Phase 13 hardened container (no network, non-root,
// seccomp, read-only rootfs, stripped env) with the plan + source mounted
// read-only and the output collected from the per-run scratch. Otherwise
// it falls back to the legacy in-process python exec. Either way it
// returns the executor's stdout (the ExecutionResult JSON) and leaves the
// generated workbook at outPath, so the surrounding ExecutePlan
// validate/upload/versioning flow is UNCHANGED.
func (s *ExcelService) runExecutor(
	ctx context.Context, planBytes []byte, planPath, srcPath, outPath string,
) ([]byte, error) {
	if s.sandbox == nil {
		return s.runScript(ctx, executorScript, executorTimeout, planPath, srcPath, outPath)
	}
	res, err := s.sandbox.Execute(ctx, sandbox.Spec{
		Mode:   sandbox.ModeScript,
		Script: "excel_executor.py",
		Args:   []string{"plan.json", "source.xlsx", "out/output.xlsx"},
		Inputs: []sandbox.InputFile{
			{Name: "plan.json", Bytes: planBytes},
			{Name: "source.xlsx", LocalPath: srcPath},
		},
		Limits: sandbox.Limits{Wall: executorTimeout},
	})
	if err != nil {
		return nil, err
	}
	defer res.Cleanup()
	art := res.Artifact("output.xlsx")
	if art == nil {
		return nil, fmt.Errorf("sandbox produced no output.xlsx (stderr: %s)", strings.TrimSpace(string(res.Stderr)))
	}
	if err := copyFile(art.Path, outPath); err != nil {
		return nil, err
	}
	return res.Stdout, nil
}

// copyFile copies src→dst, creating dst. Used to lift a sandbox artifact
// out of the per-run scratch (cleaned up on Result.Cleanup) into the
// caller's temp dir where the existing upload flow expects it.
func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return fmt.Errorf("copy artifact open: %w", err)
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return fmt.Errorf("copy artifact create: %w", err)
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return fmt.Errorf("copy artifact write: %w", err)
	}
	return out.Close()
}

// ---------------------------------------------------------------------------
// Validate
// ---------------------------------------------------------------------------

// ValidateWorkbook is the HTTP-side entry point — pull the file's
// storage key, materialise locally, run validator. Wraps
// validatePath which runs on an already-local path (used by
// ExecutePlan to avoid a wasted upload+download cycle).
func (s *ExcelService) ValidateWorkbook(
	ctx context.Context, tenantID, fileID uuid.UUID,
) (*ValidationResult, error) {
	f, err := s.files.GetByID(ctx, tenantID, fileID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrExcelSourceNotFound
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

func (s *ExcelService) validatePath(ctx context.Context, path string) (*ValidationResult, error) {
	out, err := s.runScript(ctx, validatorScript, validatorTimeout, path)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrExcelValidatorFailed, err)
	}
	var v ValidationResult
	if err := json.Unmarshal(out, &v); err != nil {
		return nil, fmt.Errorf("%w: decode: %v", ErrExcelValidatorFailed, err)
	}
	return &v, nil
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// deriveEditedFilename appends "_edited" before the extension so
// "report.xlsx" becomes "report_edited.xlsx". Used when the plan
// doesn't ship an output_filename. Caller still runs the result
// through sanitizeFilename.
func deriveEditedFilename(original string) string {
	name := strings.TrimSpace(original)
	if name == "" {
		return "workbook_edited.xlsx"
	}
	ext := filepath.Ext(name)
	if ext == "" {
		return name + "_edited.xlsx"
	}
	base := strings.TrimSuffix(name, ext)
	return base + "_edited" + ext
}
