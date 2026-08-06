package service

// Phase 7 — the client/server upload contract. The frontend accepts a
// file when EITHER its MIME type or its EXTENSION is on the allow-list
// (fileUtil.ts); the server used to fall back to the extension only for
// TEXT-like names, so an Office/PDF/image file whose browser-reported
// type was empty or application/octet-stream passed client validation
// and then died with a 415. These tests pin the two lists together.

import (
	"context"
	"errors"
	"strings"
	"testing"
)

// Extensions the WEB CLIENT accepts (fileUtil.ts ALLOWED_FILE_EXTENSIONS).
// Every one of them must be acceptable to the server too.
var frontendAcceptedExtensions = []string{
	"pdf", "docx", "xlsx", "pptx", "csv", "txt",
	"png", "jpg", "jpeg", "gif", "webp",
	"json", "md", "markdown", "html", "htm", "xml", "yaml", "yml",
	"toml", "ini", "conf", "cfg", "env", "log",
	"sql", "sh", "bash", "css", "scss", "py", "go", "js", "ts", "tsx",
	"jsx", "java", "rs", "rb", "php", "c", "cpp", "h", "swift", "kt",
	"r", "lua", "vue", "svelte",
}

// passesTypeGate mirrors the exact condition in FileService.Upload.
// Asserted directly because an ACCEPTED upload continues into the repo
// + storage layers, which a knobs-only service doesn't have.
func passesTypeGate(contentType, name string) bool {
	return allowedContentTypes[strings.ToLower(contentType)] || isAllowedUploadName(name)
}

func TestTypeGate_AcceptsEveryExtensionTheClientAccepts_WithUselessMIME(t *testing.T) {
	for _, ext := range frontendAcceptedExtensions {
		for _, ct := range []string{"", "application/octet-stream"} {
			if !passesTypeGate(ct, "report."+ext) {
				t.Errorf(".%s with content-type %q is rejected by the server but accepted by the client", ext, ct)
			}
		}
	}
}

func TestTypeGate_DocxWithEmptyMIMEIsAccepted(t *testing.T) {
	if !passesTypeGate("", "สัญญาลูกค้า.docx") {
		t.Fatal("a .docx with an empty content type must be accepted (the client accepts it)")
	}
	if !passesTypeGate("application/octet-stream", "Q3 report.XLSX") {
		t.Fatal("extension matching must be case-insensitive")
	}
}

func TestUpload_StillRejectsGenuinelyUnsupportedTypes(t *testing.T) {
	s := newSizedService()
	for _, name := range []string{"payload.exe", "archive.zip", "lib.dll", "noext"} {
		_, err := s.Upload(context.Background(), UploadInput{
			ContentType:  "application/octet-stream",
			OriginalName: name,
			Size:         1024,
		})
		if !errors.Is(err, ErrFileTypeNotAllowed) {
			t.Errorf("%s should still be rejected, got %v", name, err)
		}
	}
}

func TestBinaryDocExtensions_AreNotTreatedAsText(t *testing.T) {
	// The new extension allow-list must NOT leak into the text
	// extractor path — a .docx read as UTF-8 would emit binary noise
	// into the model's context.
	for _, name := range []string{"a.docx", "a.xlsx", "a.pptx", "a.pdf", "a.png"} {
		if isTextLikeName(name) {
			t.Errorf("%s must not be classified as text-like", name)
		}
		if !isAllowedUploadName(name) {
			t.Errorf("%s must still be an allowed upload name", name)
		}
	}
}

func TestMaxUploadBytes_ReportsConfiguredCap(t *testing.T) {
	s := newSizedService()
	if got := s.MaxUploadBytes(); got != 200*1024*1024 {
		t.Fatalf("MaxUploadBytes() = %d, want the configured 200 MiB", got)
	}
	if got := s.MaxConversationBytes(); got != 1024*1024*1024 {
		t.Fatalf("MaxConversationBytes() = %d, want the configured 1 GiB", got)
	}
}
