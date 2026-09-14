package service

// Desktop-import E2E (DB-backed, migration 026). Proves the idempotency
// contract end to end: replaying the exact same requests produces
// created=false / imported=0 with no duplicate rows, message_count only
// counts rows actually inserted, and ownership failures surface as the
// non-leaking not-found error. DB-gated: skips when Postgres is unreachable.

import (
	"bytes"
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// memFile adapts a bytes.Reader to multipart.File for upload tests.
type memFile struct{ *bytes.Reader }

func (memFile) Close() error { return nil }

// importPool hands each test a private database cloned from the test
// template (internal/testdb). FIX118: this used to open the LIVE database
// by default (no test DSN set), apply migrations 026/027
// to it from inside the test, and rely on a by-tenant DELETE for cleanup —
// which is how repository tests left 175 fixture rows behind in the
// production table. The template carries every migration on disk, so the
// schema checks below are no longer needed.
func importPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	return testdb.Fresh(t)
}

// importTestModels satisfies ModelChecker for the ownership service —
// import never validates models, so allow-all keeps the fixture minimal.
type importTestModels struct{}

func (importTestModels) ModelAllowed(string) bool { return true }
func (importTestModels) ModelIDs() []string       { return nil }

func TestImportServiceE2E(t *testing.T) {
	pool := importPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()

	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	fr := repository.NewFileRepo(pool)
	svc := NewImportService(pool, cr, mr, fr, NewConversationService(cr, importTestModels{}))

	tenantID, userID, otherUser := uuid.New(), uuid.New(), uuid.New()
	t.Cleanup(func() {
		_, _ = pool.Exec(ctx, "DELETE FROM chat.messages_masked WHERE tenant_id = $1", tenantID)
		_, _ = pool.Exec(ctx, "DELETE FROM chat.conversations WHERE tenant_id = $1", tenantID)
	})

	clientConvID := "desktop-" + uuid.NewString()
	createdAt := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)

	conv, created, err := svc.ImportConversation(ctx, ImportConversationInput{
		TenantID: tenantID, UserID: userID,
		ClientID: clientConvID, Title: "Agent chat", ModelID: "gpt-4o-mini",
		CreatedAt: &createdAt,
	})
	if err != nil {
		t.Fatalf("import conversation: %v", err)
	}
	if !created || conv.ID == uuid.Nil || conv.Title != "Agent chat" {
		t.Fatalf("want created new conversation, got created=%v conv=%+v", created, conv)
	}
	if !conv.CreatedAt.Equal(createdAt) {
		t.Fatalf("created_at not preserved: want %v got %v", createdAt, conv.CreatedAt)
	}

	t.Run("OriginAndClientIDPersisted", func(t *testing.T) {
		var origin, gotClientID string
		if err := pool.QueryRow(ctx,
			"SELECT origin, client_id FROM chat.conversations WHERE id = $1", conv.ID,
		).Scan(&origin, &gotClientID); err != nil {
			t.Fatalf("row lookup: %v", err)
		}
		if origin != "desktop_agent" || gotClientID != clientConvID {
			t.Fatalf("want origin=desktop_agent client_id=%s, got %s / %s", clientConvID, origin, gotClientID)
		}
	})

	t.Run("SecondImportReturnsExisting", func(t *testing.T) {
		again, created, err := svc.ImportConversation(ctx, ImportConversationInput{
			TenantID: tenantID, UserID: userID,
			ClientID: clientConvID, Title: "Different title", ModelID: "gpt-4o",
		})
		if err != nil {
			t.Fatalf("re-import: %v", err)
		}
		if created || again.ID != conv.ID {
			t.Fatalf("want existing conversation (created=false), got created=%v id=%s", created, again.ID)
		}
	})

	t.Run("EmptyClientIDRejected", func(t *testing.T) {
		_, _, err := svc.ImportConversation(ctx, ImportConversationInput{
			TenantID: tenantID, UserID: userID, ClientID: "  ",
		})
		if !errors.Is(err, ErrImportInvalid) {
			t.Fatalf("want ErrImportInvalid, got %v", err)
		}
	})

	ts1 := time.Date(2026, 1, 2, 3, 10, 0, 0, time.UTC)
	ts2 := time.Date(2026, 1, 2, 3, 11, 0, 0, time.UTC)
	batch := []ImportMessageInput{
		{ClientID: "m1", Role: "user", Content: "hello", CreatedAt: ts1},
		{ClientID: "m2", Role: "assistant", Content: "hi there", ModelID: "gpt-4o-mini", CreatedAt: ts2},
	}

	t.Run("MessagesImportPreservesTimestamps", func(t *testing.T) {
		imported, skipped, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, batch)
		if err != nil {
			t.Fatalf("import messages: %v", err)
		}
		if imported != 2 || skipped != 0 {
			t.Fatalf("want 2/0, got %d/%d", imported, skipped)
		}
		var gotTs time.Time
		if err := pool.QueryRow(ctx,
			"SELECT created_at FROM chat.messages_masked WHERE conversation_id = $1 AND client_id = 'm1'", conv.ID,
		).Scan(&gotTs); err != nil {
			t.Fatalf("m1 lookup: %v", err)
		}
		if !gotTs.Equal(ts1) {
			t.Fatalf("created_at not preserved: want %v got %v", ts1, gotTs)
		}
		var count int
		_ = pool.QueryRow(ctx,
			"SELECT message_count FROM chat.conversations WHERE id = $1", conv.ID,
		).Scan(&count)
		if count != 2 {
			t.Fatalf("want message_count 2, got %d", count)
		}
	})

	t.Run("ExactReplayImportsZero", func(t *testing.T) {
		imported, skipped, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, batch)
		if err != nil {
			t.Fatalf("replay: %v", err)
		}
		if imported != 0 || skipped != 2 {
			t.Fatalf("want 0/2, got %d/%d", imported, skipped)
		}
		var rows, count int
		_ = pool.QueryRow(ctx,
			"SELECT COUNT(*) FROM chat.messages_masked WHERE conversation_id = $1", conv.ID,
		).Scan(&rows)
		_ = pool.QueryRow(ctx,
			"SELECT message_count FROM chat.conversations WHERE id = $1", conv.ID,
		).Scan(&count)
		if rows != 2 || count != 2 {
			t.Fatalf("replay duplicated rows or inflated count: rows=%d count=%d", rows, count)
		}
	})

	t.Run("NewTurnImportsOnlyNewMessages", func(t *testing.T) {
		mixed := append(batch[1:],
			ImportMessageInput{ClientID: "m3", Role: "user", Content: "next turn",
				CreatedAt: time.Date(2026, 1, 2, 4, 0, 0, 0, time.UTC)})
		imported, skipped, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, mixed)
		if err != nil {
			t.Fatalf("mixed import: %v", err)
		}
		if imported != 1 || skipped != 1 {
			t.Fatalf("want 1/1, got %d/%d", imported, skipped)
		}
		var count int
		_ = pool.QueryRow(ctx,
			"SELECT message_count FROM chat.conversations WHERE id = $1", conv.ID,
		).Scan(&count)
		if count != 3 {
			t.Fatalf("want message_count 3, got %d", count)
		}
	})

	t.Run("ToolRoleRejectedBeforeDB", func(t *testing.T) {
		_, _, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, []ImportMessageInput{
			{ClientID: "t1", Role: "tool", Content: "result", CreatedAt: ts1},
		})
		if !errors.Is(err, ErrImportInvalid) {
			t.Fatalf("want ErrImportInvalid for tool role, got %v", err)
		}
	})

	t.Run("EmptyBatchRejected", func(t *testing.T) {
		_, _, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, nil)
		if !errors.Is(err, ErrImportInvalid) {
			t.Fatalf("want ErrImportInvalid for empty batch, got %v", err)
		}
	})

	t.Run("OversizedBatchRejected", func(t *testing.T) {
		big := make([]ImportMessageInput, 201)
		for i := range big {
			big[i] = ImportMessageInput{ClientID: uuid.NewString(), Role: "user", Content: "x", CreatedAt: ts1}
		}
		_, _, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, big)
		if !errors.Is(err, ErrImportInvalid) {
			t.Fatalf("want ErrImportInvalid for oversized batch, got %v", err)
		}
	})

	t.Run("CrossUserIsNotFound", func(t *testing.T) {
		_, _, err := svc.ImportMessages(ctx, tenantID, otherUser, conv.ID, batch)
		if !errors.Is(err, ErrConversationNotFound) {
			t.Fatalf("want ErrConversationNotFound, got %v", err)
		}
	})

	t.Run("ImportWithFileIDsLinksAttachment", func(t *testing.T) {
		// FIX 10: a desktop-uploaded file linked via file_ids on import
		// gets message_id set — it then shows under its message bubble on
		// web. A replay of the same batch must not error or re-link.
		clientFileID := "file-" + uuid.NewString()
		row, err := fr.Create(ctx, &model.ConversationFile{
			ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
			Filename: "x_img.png", OriginalFilename: "img.png",
			ContentType: "image/png", FileSizeBytes: 4,
			StoragePath: "test/img.png", ExtractionStatus: "completed",
			ClientID: &clientFileID,
		})
		if err != nil {
			t.Fatalf("seed file: %v", err)
		}
		item := []ImportMessageInput{{
			ClientID: "mf1", Role: "user", Content: "see attached",
			CreatedAt: time.Date(2026, 1, 2, 5, 0, 0, 0, time.UTC),
			FileIDs:   []uuid.UUID{row.ID},
		}}
		if _, _, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, item); err != nil {
			t.Fatalf("import with file_ids: %v", err)
		}
		var linkedTo *uuid.UUID
		if err := pool.QueryRow(ctx,
			"SELECT message_id FROM chat.conversation_files WHERE id = $1", row.ID,
		).Scan(&linkedTo); err != nil {
			t.Fatalf("file lookup: %v", err)
		}
		if linkedTo == nil {
			t.Fatal("file not linked to the imported message")
		}
		var clientOfMsg string
		if err := pool.QueryRow(ctx,
			"SELECT client_id FROM chat.messages_masked WHERE id = $1", *linkedTo,
		).Scan(&clientOfMsg); err != nil || clientOfMsg != "mf1" {
			t.Fatalf("file linked to wrong message: client_id=%q err=%v", clientOfMsg, err)
		}
		// Replay: message dedups (skipped), link survives, no error.
		imported, skipped, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, item)
		if err != nil || imported != 0 || skipped != 1 {
			t.Fatalf("replay: want 0/1 nil, got %d/%d %v", imported, skipped, err)
		}
		var still *uuid.UUID
		_ = pool.QueryRow(ctx,
			"SELECT message_id FROM chat.conversation_files WHERE id = $1", row.ID,
		).Scan(&still)
		if still == nil || *still != *linkedTo {
			t.Fatal("replay disturbed the file link")
		}
	})

	t.Run("UploadDedupByClientID", func(t *testing.T) {
		// FIX 10 migration 027: re-uploading the same (conversation,
		// client_id) returns the existing row — no duplicate.
		backend, err := storage.NewLocalBackend(t.TempDir())
		if err != nil {
			t.Fatalf("local backend: %v", err)
		}
		fsvc := NewFileService(fr, cr, backend, Knobs{
			ExtractPythonBinary: "definitely-missing-python-binary",
		})
		clientID := "file-" + uuid.NewString()
		up := func() *model.ConversationFile {
			t.Helper()
			row, err := fsvc.Upload(ctx, UploadInput{
				TenantID: tenantID, UserID: userID, ConversationID: conv.ID,
				OriginalName: "notes.txt", ContentType: "text/plain",
				Size: 5, Body: memFile{bytes.NewReader([]byte("hello"))},
				ClientID: clientID,
			})
			if err != nil {
				t.Fatalf("upload: %v", err)
			}
			return row
		}
		first := up()
		second := up()
		if first.ID != second.ID {
			t.Fatalf("re-upload duplicated: %s vs %s", first.ID, second.ID)
		}
		var n int
		_ = pool.QueryRow(ctx,
			"SELECT COUNT(*) FROM chat.conversation_files WHERE conversation_id = $1 AND client_id = $2",
			conv.ID, clientID,
		).Scan(&n)
		if n != 1 {
			t.Fatalf("want exactly 1 row for the client_id, got %d", n)
		}
	})

	t.Run("DeletedMirrorStaysDeleted", func(t *testing.T) {
		// FIX 9 Part C: a delete on any client is intentional. Re-importing
		// the same client_id must surface ErrImportConversationGone (410) —
		// never resurrect the row — and the row must remain soft-deleted.
		if err := cr.SoftDelete(ctx, tenantID, conv.ID); err != nil {
			t.Fatalf("soft delete: %v", err)
		}
		_, _, err := svc.ImportConversation(ctx, ImportConversationInput{
			TenantID: tenantID, UserID: userID, ClientID: clientConvID,
		})
		if !errors.Is(err, ErrImportConversationGone) {
			t.Fatalf("want ErrImportConversationGone, got %v", err)
		}
		var stillDeleted bool
		if err := pool.QueryRow(ctx,
			"SELECT is_deleted FROM chat.conversations WHERE id = $1", conv.ID,
		).Scan(&stillDeleted); err != nil {
			t.Fatalf("row lookup: %v", err)
		}
		if !stillDeleted {
			t.Fatalf("import resurrected a deleted conversation")
		}
	})

	t.Run("MessagesImportToDeletedIsNotFound", func(t *testing.T) {
		// The message-import path sees a deleted conversation as not-found
		// (ownership gate filters is_deleted) — the desktop treats that 404
		// as "deleted remotely" and drops its local copy.
		_, _, err := svc.ImportMessages(ctx, tenantID, userID, conv.ID, batch)
		if !errors.Is(err, ErrConversationNotFound) {
			t.Fatalf("want ErrConversationNotFound for deleted conversation, got %v", err)
		}
	})
}
