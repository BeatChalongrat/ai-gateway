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
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// Storage key prefix for conversation-file blobs. Lives at the top
// of the bucket / filesystem root, peer with “knowledge/“.
const conversationFilesKeyPrefix = "conversation-files"

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
	// StorageRoot is retained for backwards-compat tests + as the
	// local-fs base path; production code reads/writes only via the
	// Backend. Keeping the value lets the local backend agree with
	// callers that still derive paths from ``StorageRoot``.
	StorageRoot         string
	MaxFileSizeBytes    int64
	MaxConvFileTotal    int64
	MaxFilesPerMessage  int
	ExtractPythonBinary string
	// Path to extract_text.py relative to the service binary's cwd.
	// Defaults to ``scripts/extract_text.py`` when empty.
	ExtractScriptPath string
	// How long presigned download URLs stay valid when the storage
	// backend supports them. Zero → use the backend default.
	PresignedURLExpiry time.Duration
}

type FileService struct {
	files         *repository.FileRepo
	conversations *repository.ConversationRepo
	storage       storage.Backend
	knobs         Knobs
	// Phase 4 — set via SetExcelService so the upload path can run
	// the deep inspector on every xlsx upload and cache the
	// structure JSON for system-prompt injection. Optional: nil
	// means "don't auto-inspect" (tests, builds without the
	// scripts).
	excel *ExcelService
	// Phase 4 Session 2 — same hook for docx + pdf uploads.
	docs *DocService
}

// SetExcelService wires the Phase-4 Excel inspector into the upload
// pipeline. Called once at boot in main.go after both services exist.
func (s *FileService) SetExcelService(e *ExcelService) {
	s.excel = e
}

// SetDocService wires the Phase-4 Session 2 doc inspector (docx +
// pdf) into the upload pipeline. Same lazy-wire pattern as
// SetExcelService — nil-safe.
func (s *FileService) SetDocService(d *DocService) {
	s.docs = d
}

func NewFileService(
	files *repository.FileRepo,
	conversations *repository.ConversationRepo,
	store storage.Backend,
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
	if knobs.PresignedURLExpiry <= 0 {
		knobs.PresignedURLExpiry = 15 * time.Minute
	}
	return &FileService{
		files:         files,
		conversations: conversations,
		storage:       store,
		knobs:         knobs,
	}
}

// allowedContentTypes is the whitelist enforced on every upload.
// Anything outside the set is rejected at the service boundary.
var allowedContentTypes = map[string]bool{
	"application/pdf": true,
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document": true,
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":       true,
	"application/vnd.openxmlformats-officedocument.presentationml.presentation": true,
	"text/csv":   true,
	"text/plain": true,
	"image/png":  true,
	"image/jpeg": true,
	"image/gif":  true,
	"image/webp": true,
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

	// Sanitize the user-supplied filename. UUID-prefixed so re-uploads
	// with the same name don't collide.
	clean := sanitizeFilename(in.OriginalName)
	fileID := uuid.New()
	storageName := fmt.Sprintf("%s_%s", fileID.String(), clean)

	// Storage key — forward-slash joined, same shape on disk and in
	// S3 so the migration script can copy bytes 1:1. ``StoragePath``
	// in the DB stores the key (NOT an absolute filesystem path
	// anymore); the storage backend resolves it to wherever it lives.
	key := storage.JoinKey(
		conversationFilesKeyPrefix,
		in.TenantID.String(),
		in.ConversationID.String(),
		storageName,
	)

	// Stream the upload through the storage backend. Local backend
	// writes to disk; S3 backend PUTs an object.
	if err := s.storage.Upload(ctx, key, in.Body, in.ContentType, in.Size); err != nil {
		return nil, fmt.Errorf("storage upload: %w", err)
	}
	// We trust ``in.Size`` for the row but re-check would require a
	// second HEAD round-trip on S3. The handler already enforced the
	// per-request multipart cap; an over-by-one-byte client bug is
	// not worth a HEAD per upload.
	written := in.Size

	row, err := s.files.Create(ctx, &model.ConversationFile{
		ConversationID:   in.ConversationID,
		TenantID:         in.TenantID,
		Filename:         storageName,
		OriginalFilename: clean,
		ContentType:      in.ContentType,
		FileSizeBytes:    written,
		StoragePath:      key,
		ExtractionStatus: "processing",
	})
	if err != nil {
		_ = s.storage.Delete(ctx, key)
		return nil, err
	}

	// Run the extractor synchronously per the Phase 2 contract. For
	// big PDFs this can take a few seconds; that's acceptable for
	// the initial implementation. A background-queue refactor lives
	// in a future session.
	status, text := s.extract(ctx, row)
	if err := s.files.UpdateExtraction(ctx, row.ID, status, text); err != nil {
		// Extraction-record failure is non-fatal: the blob exists in
		// storage + the row exists in the DB, the user just won't get
		// AI context. Log and continue.
		slog.Warn("file extraction update failed",
			"file_id", row.ID.String(), "err", err)
	}
	row.ExtractionStatus = status
	row.ExtractedText = text

	// Phase 4 hook — auto-run the deep inspector on every Office /
	// PDF upload so the structure is cached for the very next
	// message turn. Non-fatal: a failed inspection just leaves
	// inspection_json NULL and the system prompt falls back to the
	// extracted text body.
	switch {
	case s.excel != nil && IsExcelContentType(row.ContentType):
		if _, raw, err := s.excel.InspectWorkbook(ctx, row.StoragePath); err == nil && len(raw) > 0 {
			if uerr := s.files.UpdateInspectionJSON(ctx, row.ID, string(raw)); uerr != nil {
				slog.Warn("excel inspection cache write failed",
					"file_id", row.ID.String(), "err", uerr)
			} else {
				sj := string(raw)
				row.InspectionJSON = &sj
			}
		} else if err != nil {
			slog.Warn("excel auto-inspect failed",
				"file_id", row.ID.String(), "err", err)
		}
	case s.docs != nil && IsDocContentType(row.ContentType):
		if _, raw, err := s.docs.InspectDocument(ctx, row.StoragePath, row.ContentType); err == nil && len(raw) > 0 {
			if uerr := s.files.UpdateInspectionJSON(ctx, row.ID, string(raw)); uerr != nil {
				slog.Warn("doc inspection cache write failed",
					"file_id", row.ID.String(), "err", uerr)
			} else {
				sj := string(raw)
				row.InspectionJSON = &sj
			}
		} else if err != nil {
			slog.Warn("doc auto-inspect failed",
				"file_id", row.ID.String(), "ct", row.ContentType, "err", err)
		}
	}
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

// OpenForDownload re-verifies tenant scope and returns the file row
// plus a reader. The handler is responsible for closing the reader.
//
// Backends that issue presigned URLs (S3) also return one via
// PresignedURL so the handler can redirect the browser straight to
// the object — avoids streaming GB-sized files through the service.
// For backends that don't issue URLs (local fs) PresignedURL is "".
type DownloadHandle struct {
	File         *model.ConversationFile
	Body         io.ReadCloser
	PresignedURL string
}

func (s *FileService) OpenForDownload(
	ctx context.Context, tenantID, fileID uuid.UUID,
) (*DownloadHandle, error) {
	f, err := s.Get(ctx, tenantID, fileID)
	if err != nil {
		return nil, err
	}
	// Best-effort presigned URL — empty + nil error means "this
	// backend doesn't support it, stream instead".
	url, _ := s.storage.GeneratePresignedURL(ctx, f.StoragePath, s.knobs.PresignedURLExpiry)
	if url != "" {
		return &DownloadHandle{File: f, PresignedURL: url}, nil
	}
	body, _, _, err := s.storage.Download(ctx, f.StoragePath)
	if err != nil {
		if errors.Is(err, storage.ErrNotFound) {
			return nil, ErrFileNotFound
		}
		return nil, fmt.Errorf("open file: %w", err)
	}
	return &DownloadHandle{File: f, Body: body}, nil
}

// Delete removes the row and unlinks the blob. Best-effort on the
// storage side — a missing object isn't a user-visible error (the
// row deletion is what matters for tenancy isolation).
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
	_ = s.storage.Delete(ctx, f.StoragePath)
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

// ExtractTextForStorageKey is the storage-backed equivalent of the
// old ExtractTextForPath. The knowledge-base indexer hands us a
// storage key + content type; we materialise a local copy when the
// content type needs an external tool (PDF/DOCX/XLSX) and stream-read
// when plain text is enough. Returns the extracted text + a soft
// "is extraction supported" boolean.
func (s *FileService) ExtractTextForStorageKey(
	ctx context.Context, key, contentType string,
) (string, bool) {
	ct := strings.ToLower(contentType)
	switch {
	case ct == "text/plain", ct == "text/csv":
		return s.readTextFromStorage(ctx, key)
	case ct == "application/pdf",
		ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
		ct == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
		return s.runPythonExtractorOnKey(ctx, key, ct)
	default:
		return "", false
	}
}

const maxExtractedChars = 50_000

// extract dispatches on content type. Returns (status, text) where
// status is one of the values the DB CHECK constraint allows.
//
// The row's StoragePath is a storage-backend key, not a filesystem
// path; each branch goes through the backend either by streaming
// (text/csv, image dimensions) or by materialising a temp copy
// (Python extractor) so the same code works for local + S3.
func (s *FileService) extract(ctx context.Context, f *model.ConversationFile) (string, *string) {
	ct := strings.ToLower(f.ContentType)
	switch {
	case ct == "text/plain", ct == "text/csv":
		text, ok := s.readTextFromStorage(ctx, f.StoragePath)
		if !ok {
			return "failed", nil
		}
		text = truncateChars(text, maxExtractedChars)
		return "completed", &text
	case strings.HasPrefix(ct, "image/"):
		text := s.imageMetadataFromStorage(ctx, f.StoragePath, f.OriginalFilename, f.FileSizeBytes, ct)
		return "completed", &text
	case ct == "application/pdf",
		ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
		ct == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
		text, ok := s.runPythonExtractorOnKey(ctx, f.StoragePath, ct)
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

// readTextFromStorage streams the object body and decodes as UTF-8.
// Used for text/plain + text/csv where there's no external tool.
func (s *FileService) readTextFromStorage(ctx context.Context, key string) (string, bool) {
	body, _, _, err := s.storage.Download(ctx, key)
	if err != nil {
		return "", false
	}
	defer body.Close()
	// Cap the read at twice the truncation budget — once we're past
	// 2x ``maxExtractedChars`` of bytes, truncateChars will trim the
	// rest anyway and there's no point spending memory.
	const readCap = int64(2 * maxExtractedChars)
	limited := io.LimitReader(body, readCap)
	buf, err := io.ReadAll(limited)
	if err != nil {
		return "", false
	}
	return string(buf), true
}

// imageMetadataFromStorage reads the image header from storage to
// produce the dimensions string. We avoid downloading the full body —
// stdlib image.DecodeConfig only needs the leading bytes of the file.
func (s *FileService) imageMetadataFromStorage(
	ctx context.Context, key, original string, size int64, ct string,
) string {
	dims := ""
	body, _, _, err := s.storage.Download(ctx, key)
	if err == nil {
		defer body.Close()
		if cfg, _, err := image.DecodeConfig(body); err == nil {
			dims = fmt.Sprintf(", %dx%d", cfg.Width, cfg.Height)
		}
	}
	return fmt.Sprintf("[Image: %s%s, %s, %s]", original, dims, humanSize(size), ct)
}

// runPythonExtractorOnKey materialises the object at “key“ to a
// local temp file (no-op for the local backend, download for S3) and
// hands the path to the Python extractor. The temp file is cleaned
// up regardless of how the extractor finishes.
func (s *FileService) runPythonExtractorOnKey(
	parent context.Context, key, ct string,
) (string, bool) {
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
	path, cleanup, err := s.storage.OpenLocalCopy(parent, key)
	if err != nil {
		slog.Warn("storage local copy failed", "key", key, "err", err)
		return "", false
	}
	defer cleanup()

	ctx, cancel := context.WithTimeout(parent, 30*time.Second)
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
