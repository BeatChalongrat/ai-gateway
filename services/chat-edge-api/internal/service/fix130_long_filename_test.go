package service

// FIX130 C3.4 — a long Thai file name uploads. NIGHT2 F4 / the FIX130 test
// stack: a 107-character (313-byte) Thai name → 500 in 2 ms, and the chip's
// "ลองใหม่" failed again forever. The storage object was named
// "<uuid>_<the whole display name>", and ext4 allows 255 BYTES per name —
// a Thai character is three. The display name is not the storage name.

import (
	"context"
	"fmt"
	"io"
	"path"
	"strings"
	"syscall"
	"testing"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// ext4Backend refuses a key with a component over 255 bytes, as the live
// volume does (the Windows test host's NTFS counts UTF-16 units and would
// not).
type ext4Backend struct{ storage.Backend }

func (b ext4Backend) Upload(ctx context.Context, key string, body io.Reader, ct string, size int64) error {
	for _, part := range strings.Split(key, "/") {
		if len(part) > 255 {
			return fmt.Errorf("local storage rename: %w", syscall.ENAMETOOLONG)
		}
	}
	return b.Backend.Upload(ctx, key, body, ct, size)
}

func TestFIX130_LongThaiFilenameUploadsWithItsNameIntact(t *testing.T) {
	pool := testdb.Fresh(t)
	cr := repository.NewConversationRepo(pool)
	files := repository.NewFileRepo(pool)
	local, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	fs := NewFileService(files, cr, ext4Backend{local}, Knobs{})
	tenant, user := uuid.New(), uuid.New()
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "FIX130"})
	if err != nil {
		t.Fatal(err)
	}

	// The coordinator's measured case: 107 characters, 313 bytes.
	stem := []rune(strings.Repeat("รายงานการประชุมคณะกรรมการฉบับสมบูรณ์", 4))[:103]
	name := string(stem) + ".txt"
	if n := utf8.RuneCountInString(name); n != 107 || len(name) != 313 {
		t.Fatalf("fixture: %d runes / %d bytes", n, len(name))
	}
	row, err := fs.Upload(context.Background(), UploadInput{
		TenantID: tenant, UserID: user, ConversationID: conv.ID,
		OriginalName: name, ContentType: "text/plain", Size: 5, Body: tmpUpload(t, "hello"),
	})
	if err != nil {
		t.Fatalf("a long Thai name must upload, got %v", err)
	}
	if row.OriginalFilename != name {
		t.Fatalf("the name the person sees must be theirs, got %q", row.OriginalFilename)
	}
	obj := path.Base(row.StoragePath)
	if len(obj) > 255 || !utf8.ValidString(obj) || !strings.HasSuffix(obj, ".txt") {
		t.Fatalf("storage name must fit 255 bytes, be valid UTF-8 and keep the extension: %d bytes %q", len(obj), obj)
	}
}

func TestFIX130_StorageObjectNameAndSanitizeKeepTheExtension(t *testing.T) {
	id := uuid.New()
	for _, name := range []string{
		strings.Repeat("ก", 300) + ".pdf",
		strings.Repeat("a", 400) + ".docx",
		strings.Repeat("中", 90) + ".xlsx",
		"short.txt",
	} {
		clean := sanitizeFilename(name)
		if utf8.RuneCountInString(clean) > 200 || path.Ext(clean) != path.Ext(name) {
			t.Fatalf("sanitize must cap at 200 runes and keep %q, got %q", path.Ext(name), clean)
		}
		obj := storageObjectName(id, clean)
		if len(obj) > 255 || !utf8.ValidString(obj) || path.Ext(obj) != path.Ext(name) || !strings.HasPrefix(obj, id.String()+"_") {
			t.Fatalf("bad storage name for %q: %d bytes %q", name[:12], len(obj), obj)
		}
	}
	if got := storageObjectName(id, "short.txt"); got != id.String()+"_short.txt" {
		t.Fatalf("a short name is kept as is, got %q", got)
	}
}
