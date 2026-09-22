package service

// FIX127 A1 — the repository rule and the delete-by-client-id path.

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// tmpUpload returns a multipart.File-shaped body (an *os.File) holding s.
func tmpUpload(t *testing.T, s string) *os.File {
	t.Helper()
	p := filepath.Join(t.TempDir(), "up.txt")
	if err := os.WriteFile(p, []byte(s), 0o600); err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(p)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = f.Close() })
	return f
}

func TestFIX127_ModelContextRuleIsTheRepositoryQuery(t *testing.T) {
	pool := testdb.Fresh(t)
	cr := repository.NewConversationRepo(pool)
	files := repository.NewFileRepo(pool)
	mr := repository.NewMessageRepo(pool)
	tenant, user := uuid.New(), uuid.New()
	ctx := context.Background()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "FIX127"})
	if err != nil {
		t.Fatal(err)
	}
	mk := func(name string, generated bool) *model.ConversationFile {
		row, err := files.Create(ctx, &model.ConversationFile{ConversationID: conv.ID, TenantID: tenant, UserID: user,
			Filename: name, OriginalFilename: name, ContentType: "text/plain", FileSizeBytes: 1, StoragePath: "k/" + name,
			ExtractionStatus: "completed", IsGenerated: generated})
		if err != nil {
			t.Fatal(err)
		}
		return row
	}
	unsent := mk("unsent.txt", false)
	attached := mk("attached.txt", false)
	generated := mk("made-by-chat.xlsx", true)
	msg, err := mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenant, UserID: user, Role: "user", ContentMasked: "hi", Status: "completed", IsActive: true})
	if err != nil {
		t.Fatal(err)
	}
	if n, err := files.AttachToMessage(ctx, tenant, conv.ID, msg.ID, []uuid.UUID{attached.ID}); err != nil || n != 1 {
		t.Fatalf("attach: n=%d err=%v", n, err)
	}

	got, err := files.ListForModelContext(ctx, tenant, conv.ID)
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]bool{}
	for _, g := range got {
		names[g.OriginalFilename] = true
	}
	if names["unsent.txt"] {
		t.Fatal("an unattached upload is in the model context list")
	}
	if !names["attached.txt"] || !names["made-by-chat.xlsx"] {
		t.Fatalf("attached or generated file missing: %v", names)
	}
	_ = unsent
	_ = generated
	// The user-facing file list is NOT the model rule: it still shows every row.
	all, _ := files.ListByConversation(ctx, tenant, conv.ID)
	if len(all) != 3 {
		t.Fatalf("ListByConversation = %d rows, want 3", len(all))
	}
}

func TestFIX127_DeleteByClientIDRemovesOnlyUnattachedUploads(t *testing.T) {
	pool := testdb.Fresh(t)
	cr := repository.NewConversationRepo(pool)
	files := repository.NewFileRepo(pool)
	mr := repository.NewMessageRepo(pool)
	be, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	fs := NewFileService(files, cr, be, Knobs{})
	tenant, user, stranger := uuid.New(), uuid.New(), uuid.New()
	ctx := context.Background()
	conv, _ := cr.Create(ctx, &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "FIX127"})

	up := func(clientID string) *model.ConversationFile {
		row, err := fs.Upload(ctx, UploadInput{TenantID: tenant, UserID: user, ConversationID: conv.ID,
			OriginalName: clientID + ".txt", ContentType: "text/plain", Size: 5, Body: tmpUpload(t, "hello"), ClientID: clientID})
		if err != nil {
			t.Fatalf("upload %s: %v", clientID, err)
		}
		return row
	}
	pending := up("chip-1")
	sent := up("chip-2")
	msg, _ := mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenant, UserID: user, Role: "user", ContentMasked: "hi", Status: "completed", IsActive: true})
	if _, err := files.AttachToMessage(ctx, tenant, conv.ID, msg.ID, []uuid.UUID{sent.ID}); err != nil {
		t.Fatal(err)
	}

	// Another user cannot delete it, even with the right client id.
	if err := fs.DeleteByClientID(ctx, tenant, stranger, conv.ID, "chip-1"); err != ErrFileNotFound {
		t.Fatalf("stranger delete = %v, want ErrFileNotFound", err)
	}
	// A sent attachment is not the composer's to take back.
	if err := fs.DeleteByClientID(ctx, tenant, user, conv.ID, "chip-2"); err != ErrFileNotFound {
		t.Fatalf("sent-file delete = %v, want ErrFileNotFound", err)
	}
	// The pending chip goes: row and blob.
	if err := fs.DeleteByClientID(ctx, tenant, user, conv.ID, "chip-1"); err != nil {
		t.Fatalf("delete pending: %v", err)
	}
	if _, err := files.GetByID(ctx, tenant, pending.ID); err != repository.ErrNotFound {
		t.Fatalf("row still there: %v", err)
	}
	if ok, _ := be.Exists(ctx, pending.StoragePath); ok {
		t.Fatal("blob still in storage after delete")
	}
	if ok, _ := be.Exists(ctx, sent.StoragePath); !ok {
		t.Fatal("the sent file's blob was deleted")
	}
	// Second delete is a 404, not a silent success.
	if err := fs.DeleteByClientID(ctx, tenant, user, conv.ID, "chip-1"); err != ErrFileNotFound {
		t.Fatalf("re-delete = %v, want ErrFileNotFound", err)
	}
}
