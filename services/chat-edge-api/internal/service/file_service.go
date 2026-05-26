// FileService — upload, list, download, delete + text extraction
// for conversation attachments.
//
// The blob itself lives on disk under FileStorageRoot. The metadata
// row in chat.conversation_files points at it. The service is the
// single source of truth for filesystem layout: handlers never read
// or write the disk directly so the path-traversal hardening lives
// in exactly one place.
//
// Extraction strategy:
//
//	txt / csv     → read directly, UTF-8.
//	images        → write a metadata placeholder (vision-capable
//	                models will get the file via the gateway in a
//	                future session; for now the AI just gets a hint).
//	pdf/docx/xlsx → shell out to scripts/extract_text.py. If the
//	                Python interpreter isn't available we mark the
//	                row as ``unsupported`` so the message handler
//	                still includes the filename in the prompt.
package service

import (
	"context"
	"errors"
	"fmt"
	"image"
	"io"
	"log/slog"
	"mime/multipart"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	// Side-effect imports so the stdlib image package decodes these
	// formats. Used by the image-metadata extractor for thumbnails /
	// dimensions in the AI context block.
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ---------------------------------------------------------------------------
// Typed errors. Handlers map these to HTTP statuses.
// ---------------------------------------------------------------------------

var (
	ErrFileNotFound          = errors.New("file: not found")
	ErrFileEmpty             = errors.New("file: empty upload")
	ErrFileTooLarge          = errors.New("file: too large")
	ErrFileTypeNotAllowed    = errors.New("file: type not allowed")
	ErrFileConvNotFound      = errors.New("file: conversation not found")
	ErrFileConvQuotaExceeded = errors.New("file: conversation quota exceeded")
	ErrFileTooManyAttached   = errors.New("file: too many attached")
)

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

// Knobs is the runtime config bag — kept as a small interface so
// tests can swap in a fake without dragging the full config.Config.
type Knobs struct {
	StorageRoot         string
	MaxFileSizeBytes    int64
	MaxConvFileTotal    int64
	MaxFilesPerMessage  int
	ExtractPythonBinary string
	// Path to extract_text.py relative to the service binary's cwd.
	// Defaults to ``scripts/extract_text.py`` when empty.
	ExtractScriptPath string
}

type FileService struct {
	files         *repository.FileRepo
	conversations *repository.ConversationRepo
	knobs         Knobs
}

func NewFileService(
	files *repository.FileRepo,
	conversations *repository.ConversationRepo,
	knobs Knobs,
) *FileService {
	if knobs.StorageRoot == "" {
		knobs.StorageRoot = "./data/uploads"
	}
	if knobs.MaxFileSizeBytes <= 0 {
		knobs.MaxFileSizeBytes = 25 * 1024 * 1024
	}
	if knobs.MaxConvFileTotal <= 0 {
		knobs.MaxConvFileTotal = 100 * 1024 * 1024
	}
	if knobs.MaxFilesPerMessage <= 0 {
		knobs.MaxFilesPerMessage = 5
	}
	if knobs.ExtractPythonBinary == "" {
		knobs.ExtractPythonBinary = "python3"
	}
	if knobs.ExtractScriptPath == "" {
		knobs.ExtractScriptPath = "scripts/extract_text.py"
	}
	return &FileService{
		files:         files,
		conversations: conversations,
		knobs:         knobs,
	}
}

// allowedContentTypes is the whitelist enforced on every upload.
// Anything outside the set is rejected at the service boundary.
var allowedContentTypes = map[string]bool{
	"application/pdf": true,
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document": true,
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":       true,
	"text/csv":         true,
	"text/plain":       true,
	"image/png":        true,
	"image/jpeg":       true,
	"image/gif":        true,
	"image/webp":       true,
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------

// UploadInput is the parsed multipart payload.
type UploadInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	OriginalName   string
	ContentType    string
	Size           int64
	Body           multipart.File
}

// Upload validates the request, persists the blob, creates the row,
// extracts text, and returns the completed file row to the handler.
func (s *FileService) Upload(ctx context.Context, in UploadInput) (*model.ConversationFile, error) {
	if in.Size <= 0 {
		return nil, ErrFileEmpty
	}
	if in.Size > s.knobs.MaxFileSizeBytes {
		return nil, fmt.Errorf("%w: %d > %d", ErrFileTooLarge, in.Size, s.knobs.MaxFileSizeBytes)
	}
	if !allowedContentTypes[strings.ToLower(in.ContentType)] {
		return nil, fmt.Errorf("%w: %s", ErrFileTypeNotAllowed, in.ContentType)
	}

	// Conversation ownership gate. Returns 404 from the handler if
	// the conversation isn't ours; the existence-leak is intentional
	// (matches the message handler's behaviour).
	conv, err := s.conversations.GetByID(ctx, in.TenantID, in.ConversationID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrFileConvNotFound
		}
		return nil, err
	}
	if conv.UserID != in.UserID {
		return nil, ErrFileConvNotFound
	}

	// Per-conversation cap. Sum existing rows + the incoming size.
	used, err := s.files.SumSizeForConversation(ctx, in.TenantID, in.ConversationID)
	if err != nil {
		return nil, err
	}
	if used+in.Size > s.knobs.MaxConvFileTotal {
		return nil, fmt.Errorf("%w: %d + %d > %d", ErrFileConvQuotaExceeded, used, in.Size, s.knobs.MaxConvFileTotal)
	}

	// Sanitize the user-supplied filename. UUID-prefixed on disk so
	// re-uploads with the same name don't collide.
	clean := sanitizeFilename(in.OriginalName)
	fileID := uuid.New()
	storageName := fmt.Sprintf("%s_%s", fileID.String(), clean)

	dir, err := s.tenantDir(in.TenantID, in.ConversationID)
	if err != nil {
		return nil, err
	}
	dst := filepath.Join(dir, storageName)

	// Verify the resolved absolute path is still under the storage
	// root. Defence-in-depth against UUIDs that somehow contain
	// traversal sequences (they don't, but the check is cheap).
	if err := s.assertInsideRoot(dst); err != nil {
		return nil, err
	}

	// Stream the upload to disk. The handler hands us an
	// already-opened multipart.File; we don't buffer in memory.
	out, err := os.Create(dst)
	if err != nil {
		return nil, fmt.Errorf("create file on disk: %w", err)
	}
	written, err := io.Copy(out, in.Body)
	closeErr := out.Close()
	if err != nil {
		_ = os.Remove(dst)
		return nil, fmt.Errorf("write file: %w", err)
	}
	if closeErr != nil {
		_ = os.Remove(dst)
		return nil, fmt.Errorf("close file: %w", closeErr)
	}
	if written > s.knobs.MaxFileSizeBytes {
		// Defense in depth: in.Size came from the multipart header
		// which the client could lie about. Re-check after the
		// stream is fully drained.
		_ = os.Remove(dst)
		return nil, fmt.Errorf("%w: %d > %d", ErrFileTooLarge, written, s.knobs.MaxFileSizeBytes)
	}

	row, err := s.files.Create(ctx, &model.ConversationFile{
		ConversationID:   in.ConversationID,
		TenantID:         in.TenantID,
		Filename:         storageName,
		OriginalFilename: clean,
		ContentType:      in.ContentType,
		FileSizeBytes:    written,
		StoragePath:      dst,
		ExtractionStatus: "processing",
	})
	if err != nil {
		_ = os.Remove(dst)
		return nil, err
	}

	// Run the extractor synchronously per the Phase 2 contract. For
	// big PDFs this can take a few seconds; that's acceptable for
	// the initial implementation. A background-queue refactor lives
	// in a future session.
	status, text := s.extract(row)
	if err := s.files.UpdateExtraction(ctx, row.ID, status, text); err != nil {
		// Extraction-record failure is non-fatal: the blob exists on
		// disk + the row exists in the DB, the user just won't get
		// AI context. Log and continue.
		slog.Warn("file extraction update failed",
			"file_id", row.ID.String(), "err", err)
	}
	row.ExtractionStatus = status
	row.ExtractedText = text
	return row, nil
}

// ---------------------------------------------------------------------------
// List / Download / Delete
// ---------------------------------------------------------------------------

func (s *FileService) List(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) ([]model.ConversationFile, error) {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrFileConvNotFound
		}
		return nil, err
	}
	if conv.UserID != userID {
		return nil, ErrFileConvNotFound
	}
	return s.files.ListByConversation(ctx, tenantID, convID)
}

// Get returns metadata for a single file. Tenant-scoped via the
// repo. Returns ErrFileNotFound when missing.
func (s *FileService) Get(
	ctx context.Context, tenantID, fileID uuid.UUID,
) (*model.ConversationFile, error) {
	f, err := s.files.GetByID(ctx, tenantID, fileID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrFileNotFound
		}
		return nil, err
	}
	return f, nil
}

// OpenForDownload re-verifies tenant scope and returns the file
// row plus an open *os.File ready for streaming. Caller MUST close
// the returned file.
func (s *FileService) OpenForDownload(
	ctx context.Context, tenantID, fileID uuid.UUID,
) (*model.ConversationFile, *os.File, error) {
	f, err := s.Get(ctx, tenantID, fileID)
	if err != nil {
		return nil, nil, err
	}
	if err := s.assertInsideRoot(f.StoragePath); err != nil {
		return nil, nil, err
	}
	fp, err := os.Open(f.StoragePath)
	if err != nil {
		return nil, nil, fmt.Errorf("open file: %w", err)
	}
	return f, fp, nil
}

// Delete removes the row and unlinks the blob. Best-effort on the
// filesystem side — a missing on-disk file isn't a user-visible
// error (the row deletion is what matters for tenancy isolation).
func (s *FileService) Delete(
	ctx context.Context, tenantID, fileID uuid.UUID,
) error {
	f, err := s.Get(ctx, tenantID, fileID)
	if err != nil {
		return err
	}
	if err := s.files.Delete(ctx, tenantID, fileID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrFileNotFound
		}
		return err
	}
	if err := s.assertInsideRoot(f.StoragePath); err == nil {
		_ = os.Remove(f.StoragePath)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Attach (called by the message handler on Send)
// ---------------------------------------------------------------------------

// AttachToMessage links pending file rows to a freshly-created
// message. Validates that the caller owns the files AND that they
// belong to the same conversation. Caller-supplied ids that don't
// match are silently ignored at the SQL layer (UPDATE 0 rows); the
// returned count lets the handler tell the user "X of Y attached".
func (s *FileService) AttachToMessage(
	ctx context.Context,
	tenantID, convID, messageID uuid.UUID,
	fileIDs []uuid.UUID,
) (int64, error) {
	if len(fileIDs) == 0 {
		return 0, nil
	}
	if len(fileIDs) > s.knobs.MaxFilesPerMessage {
		return 0, ErrFileTooManyAttached
	}
	return s.files.AttachToMessage(ctx, tenantID, convID, messageID, fileIDs)
}

// ---------------------------------------------------------------------------
// Context block (called by the message handler when assembling the
// gateway request).
// ---------------------------------------------------------------------------

// BuildContextBlock formats every successfully-extracted file in the
// conversation into a markdown block suitable for the system prompt.
// Returns the empty string when no files attach.
//
// Total budget: 30,000 tokens ~ 120,000 chars. We prioritise the
// MOST RECENT uploads — if older files don't fit in the budget they
// get a placeholder note instead.
const fileContextBudget = 120_000

func (s *FileService) BuildContextBlock(
	ctx context.Context, tenantID, convID uuid.UUID,
) (string, error) {
	files, err := s.files.ListByConversation(ctx, tenantID, convID)
	if err != nil {
		return "", err
	}
	if len(files) == 0 {
		return "", nil
	}

	// Walk newest-first; once the budget is gone, every remaining
	// file becomes a placeholder ("[content omitted]") so the AI at
	// least knows the file exists.
	var b strings.Builder
	b.WriteString("## Files in this conversation:\n\n")
	used := 0
	for i := len(files) - 1; i >= 0; i-- {
		f := files[i]
		header := fmt.Sprintf("### File: %s (%s, uploaded %s)\n",
			f.OriginalFilename,
			humanSize(f.FileSizeBytes),
			humanAgo(time.Since(f.CreatedAt)),
		)
		var body string
		switch f.ExtractionStatus {
		case "completed":
			if f.ExtractedText != nil {
				body = *f.ExtractedText
			}
		case "unsupported", "failed":
			body = "[Content extraction not available for this file type.]"
		default:
			body = "[Content extraction still in progress.]"
		}

		entry := header + body + "\n\n"
		remaining := fileContextBudget - used
		if remaining <= len(header)+len("[content omitted]\n\n") {
			break
		}
		if len(entry) > remaining {
			body = body[:remaining-len(header)-len("\n\n[content truncated]\n\n")]
			entry = header + body + "\n\n[content truncated]\n\n"
		}
		b.WriteString(entry)
		used += len(entry)
	}
	b.WriteString("When the user refers to 'the file', 'the document', " +
		"'the spreadsheet', 'the image', etc., use the file contents " +
		"provided above.\n")
	return b.String(), nil
}

// ---------------------------------------------------------------------------
// Extraction
// ---------------------------------------------------------------------------

const maxExtractedChars = 50_000

// extract dispatches on content type. Returns (status, text) where
// status is one of the values the DB CHECK constraint allows.
func (s *FileService) extract(f *model.ConversationFile) (string, *string) {
	ct := strings.ToLower(f.ContentType)
	switch {
	case ct == "text/plain", ct == "text/csv":
		text, ok := readTextFile(f.StoragePath)
		if !ok {
			return "failed", nil
		}
		text = truncateChars(text, maxExtractedChars)
		return "completed", &text
	case strings.HasPrefix(ct, "image/"):
		text := imageMetadata(f.StoragePath, f.OriginalFilename, f.FileSizeBytes, ct)
		return "completed", &text
	case ct == "application/pdf",
		ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
		ct == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
		text, ok := s.runPythonExtractor(f.StoragePath, ct)
		if !ok {
			placeholder := fmt.Sprintf("[File: %s, %s, %s. Content extraction not available.]",
				f.OriginalFilename, ct, humanSize(f.FileSizeBytes))
			return "unsupported", &placeholder
		}
		text = truncateChars(text, maxExtractedChars)
		return "completed", &text
	default:
		// Should never reach here — the allowlist gated us — but be
		// defensive so a future content-type slip doesn't crash.
		return "unsupported", nil
	}
}

func readTextFile(path string) (string, bool) {
	buf, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	return string(buf), true
}

func imageMetadata(path, original string, size int64, ct string) string {
	dims := ""
	if f, err := os.Open(path); err == nil {
		defer f.Close()
		if cfg, _, err := image.DecodeConfig(f); err == nil {
			dims = fmt.Sprintf(", %dx%d", cfg.Width, cfg.Height)
		}
	}
	return fmt.Sprintf("[Image: %s%s, %s, %s]", original, dims, humanSize(size), ct)
}

func (s *FileService) runPythonExtractor(path, ct string) (string, bool) {
	// Resolve the script path relative to the current working
	// directory. In dev this is the chat-edge-api repo root; in the
	// container it's /app (matches the Dockerfile WORKDIR).
	script := s.knobs.ExtractScriptPath
	if !filepath.IsAbs(script) {
		if abs, err := filepath.Abs(script); err == nil {
			script = abs
		}
	}
	if _, err := os.Stat(script); err != nil {
		slog.Warn("extract script not found", "path", script, "err", err)
		return "", false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, s.knobs.ExtractPythonBinary, script, path, ct)
	out, err := cmd.Output()
	if err != nil {
		slog.Warn("python extractor failed", "err", err)
		return "", false
	}
	return string(out), true
}

// ---------------------------------------------------------------------------
// Path / name helpers
// ---------------------------------------------------------------------------

func (s *FileService) tenantDir(tenantID, convID uuid.UUID) (string, error) {
	root, err := filepath.Abs(s.knobs.StorageRoot)
	if err != nil {
		return "", fmt.Errorf("resolve storage root: %w", err)
	}
	dir := filepath.Join(root, tenantID.String(), convID.String())
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return "", fmt.Errorf("mkdir upload dir: %w", err)
	}
	return dir, nil
}

func (s *FileService) assertInsideRoot(p string) error {
	root, err := filepath.Abs(s.knobs.StorageRoot)
	if err != nil {
		return err
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return err
	}
	// Ensure abs starts with root + separator (or equals root). Avoids
	// a prefix-match false positive on "/data/uploads2".
	rel, err := filepath.Rel(root, abs)
	if err != nil || strings.HasPrefix(rel, "..") || rel == ".." {
		return fmt.Errorf("path traversal blocked: %s", p)
	}
	return nil
}

// sanitizeFilename trims path separators, control chars, and caps
// the byte length to 200. UTF-8 friendly because rune-iteration
// counts codepoints, not bytes, so Thai / CJK filenames survive.
func sanitizeFilename(name string) string {
	// Reject anything path-like.
	name = filepath.Base(name)
	if name == "." || name == ".." {
		name = "untitled"
	}
	// Replace risky characters with underscore.
	var b strings.Builder
	for _, r := range name {
		switch {
		case r == 0:
			continue
		case r < 0x20:
			b.WriteByte('_')
		case r == '/' || r == '\\':
			b.WriteByte('_')
		default:
			b.WriteRune(r)
		}
	}
	out := strings.TrimSpace(b.String())
	if out == "" {
		out = "untitled"
	}
	// 200-rune cap.
	runes := []rune(out)
	if len(runes) > 200 {
		out = string(runes[:200])
	}
	return out
}

func truncateChars(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + fmt.Sprintf("\n\n[Content truncated — showing first %d characters of %d total]", n, len(s))
}

func humanSize(n int64) string {
	const (
		KB = int64(1024)
		MB = KB * 1024
	)
	switch {
	case n >= MB:
		return fmt.Sprintf("%.1f MB", float64(n)/float64(MB))
	case n >= KB:
		return fmt.Sprintf("%.0f KB", float64(n)/float64(KB))
	default:
		return fmt.Sprintf("%d B", n)
	}
}

func humanAgo(d time.Duration) string {
	switch {
	case d < time.Minute:
		return "just now"
	case d < time.Hour:
		return fmt.Sprintf("%d minutes ago", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%d hours ago", int(d.Hours()))
	default:
		return fmt.Sprintf("%d days ago", int(d.Hours()/24))
	}
}
