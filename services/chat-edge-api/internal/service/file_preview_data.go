// Structured preview data for one attachment — Phase 16.
//
// GET /files/{id}/text hands back the flat text the extraction pipeline
// produced. That is exactly what the MODEL reads, which makes it the
// right thing to show when the question is "what did the AI see" — and
// the wrong thing to show a person who opened a spreadsheet, because a
// tab-separated dump of 12 000 cells is not a spreadsheet.
//
// This runs scripts/preview_data.py over the SAME sandbox the text
// extractor uses and returns its JSON untouched: sheets with their
// names and a paginated window of cells, Word blocks in document order,
// one entry per slide. The client draws the grid.
//
// Scope: identical to /files/{id}/text — FileService.Get's tenant+user
// rule, someone else's file is ErrFileNotFound (404, never 403, so the
// response cannot be used to probe which file ids exist). Nothing about
// this route widens what a caller can reach; it re-encodes bytes the
// same caller can already download.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

const (
	previewDataScript  = "preview_data.py"
	previewDataTimeout = 45 * time.Second

	// Windows the endpoint will ask for. The script clamps these too —
	// it is the last word, since it is what actually reads the file —
	// but clamping here keeps an absurd query string from ever reaching
	// a container.
	PreviewMaxRows      = 500
	PreviewDefaultRows  = 200
	PreviewMaxBlocks    = 300
	PreviewMaxSheet     = 512
	PreviewMaxOffset    = 5_000_000
	previewMaxOutputMiB = 8
)

// ErrPreviewUnsupported means the file has no structured view — a PDF,
// an image, an archive. The caller falls back to /files/{id}/text.
var ErrPreviewUnsupported = errors.New("file: no structured preview for this type")

// previewDataTypes maps the content types that have a structured
// reader. Kept beside the extension list rather than derived from
// allowedContentTypes: a type being uploadable says nothing about
// whether preview_data.py can read it.
var previewDataTypes = map[string]string{
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":         "sheet",
	"application/vnd.ms-excel.sheet.macroEnabled.12":                            "sheet",
	"text/csv":                                                                  "sheet",
	"application/csv":                                                           "sheet",
	"text/tab-separated-values":                                                 "sheet",
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document":   "document",
	"application/vnd.openxmlformats-officedocument.presentationml.presentation": "slides",
}

var previewDataExtensions = map[string]string{
	".xlsx": "sheet",
	".xlsm": "sheet",
	".csv":  "sheet",
	".tsv":  "sheet",
	".docx": "document",
	".pptx": "slides",
}

// PreviewDataKind reports which structured view a file gets, or "" when
// it has none. Content type first (the server's own word), extension
// second, because uploads regularly arrive as application/octet-stream
// when the browser failed to sniff a type — the same precedence the
// preview routers on both clients use.
//
// MUST agree with kind_for() in preview_data.py: this decides whether
// to spend a container, that decides what the container reads.
func PreviewDataKind(contentType, filename string) string {
	ct := strings.ToLower(strings.TrimSpace(contentType))
	if i := strings.IndexByte(ct, ';'); i >= 0 {
		ct = strings.TrimSpace(ct[:i])
	}
	if kind, ok := previewDataTypes[ct]; ok {
		return kind
	}
	if kind, ok := previewDataExtensions[strings.ToLower(filepath.Ext(filename))]; ok {
		return kind
	}
	return ""
}

// PreviewDataRequest is one window of a document. Sheet only means
// anything for a spreadsheet; Offset/Limit count rows for a sheet,
// blocks for a Word document, slides for a deck.
type PreviewDataRequest struct {
	Sheet  int
	Offset int
	Limit  int
}

func (r PreviewDataRequest) clamp() PreviewDataRequest {
	return PreviewDataRequest{
		Sheet:  clampInt(r.Sheet, 0, PreviewMaxSheet, 0),
		Offset: clampInt(r.Offset, 0, PreviewMaxOffset, 0),
		Limit:  clampInt(r.Limit, 1, PreviewMaxRows, PreviewDefaultRows),
	}
}

// clampInt folds an unset or out-of-range value into [low, high].
// A non-positive value with a positive fallback means "not specified"
// (an absent ?limit= parses to 0), which is why it takes the default
// rather than the floor.
func clampInt(v, low, high, fallback int) int {
	if v <= 0 && fallback > 0 {
		return fallback
	}
	if v < low {
		return low
	}
	if v > high {
		return high
	}
	return v
}

// previewParams is the params.json the script reads. Scalars travel as
// an input FILE, not as argv: sandbox_runner.py resolves every argv
// entry to a path under /job/in, so "200" would arrive as the path
// /job/in/200.
type previewParams struct {
	ContentType string `json:"content_type"`
	Filename    string `json:"filename"`
	Sheet       int    `json:"sheet"`
	Offset      int    `json:"offset"`
	Limit       int    `json:"limit"`
}

// PreviewData returns the script's JSON verbatim for the caller's file.
//
// Verbatim on purpose: the shape is the script's contract with the two
// clients, and re-modelling it in Go would add a mapping layer that can
// only drift. The bytes ARE validated as a JSON object first, so a
// broken script can never turn into a broken response body.
func (s *FileService) PreviewData(
	ctx context.Context, tenantID, userID, fileID uuid.UUID, req PreviewDataRequest,
) (json.RawMessage, error) {
	f, err := s.Get(ctx, tenantID, userID, fileID) // tenant + user scoped
	if err != nil {
		return nil, err
	}
	if PreviewDataKind(f.ContentType, f.OriginalFilename) == "" {
		return nil, ErrPreviewUnsupported
	}
	if s.sandbox == nil {
		return nil, ErrFileSandboxUnavailable
	}

	srcPath, cleanup, err := s.storage.OpenLocalCopy(ctx, f.StoragePath)
	if err != nil {
		return nil, ErrFileNotFound
	}
	defer cleanup()

	req = req.clamp()
	params, err := json.Marshal(previewParams{
		ContentType: f.ContentType,
		Filename:    f.OriginalFilename,
		Sheet:       req.Sheet,
		Offset:      req.Offset,
		Limit:       req.Limit,
	})
	if err != nil {
		return nil, fmt.Errorf("preview params: %w", err)
	}

	// The input keeps the real extension: the script's fallback routing
	// reads it when the stored content type is generic.
	inputName := "source" + strings.ToLower(filepath.Ext(f.OriginalFilename))
	if inputName == "source" {
		inputName = "source.bin"
	}

	res, err := s.sandbox.Execute(ctx, sandbox.Spec{
		Mode:   sandbox.ModeScript,
		Script: previewDataScript,
		Args:   []string{inputName, "params.json"},
		Inputs: []sandbox.InputFile{
			{Name: inputName, LocalPath: srcPath},
			{Name: "params.json", Bytes: params},
		},
		Limits: sandbox.Limits{
			Wall:       previewDataTimeout,
			MaxOutputB: previewMaxOutputMiB << 20,
		},
	})
	if res != nil {
		defer res.Cleanup()
	}
	if err != nil || res == nil {
		if err == nil {
			err = ErrFileSandboxUnavailable
		}
		return nil, err
	}
	// Exit 3 is the script's "I don't read this type" — the same answer
	// PreviewDataKind gives, reached from the file's actual bytes.
	if res.Exit == 3 {
		return nil, ErrPreviewUnsupported
	}
	if res.Exit != 0 {
		return nil, fmt.Errorf("preview_data exited with code %d: %s",
			res.Exit, strings.TrimSpace(string(res.Stderr)))
	}
	// Truncated output is malformed JSON by definition — refuse it
	// rather than hand the client half an object.
	if res.Truncated {
		return nil, errors.New("preview_data output exceeded the size cap")
	}

	out := json.RawMessage(res.Stdout)
	var probe map[string]json.RawMessage
	if err := json.Unmarshal(out, &probe); err != nil {
		return nil, fmt.Errorf("preview_data output is not a JSON object: %w", err)
	}
	if _, ok := probe["kind"]; !ok {
		return nil, errors.New("preview_data output has no kind")
	}
	return out, nil
}
