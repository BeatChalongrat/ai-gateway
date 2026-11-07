package service

// FIX132 C — a chat the user deleted is permanently removed 30 days
// later, for EVERY tenant, without anyone turning anything on.
//
// Until FIX132 the user_request rule ran only for tenants whose
// transcript_retention_enabled flag was true (default false) and only
// when dry_run was off (default true): on the defaults a deleted chat
// stayed in the database, and its files on disk, forever. These tests
// run the real nightly pass against a throwaway database and a real
// local storage directory: a chat deleted 31 days ago is erased — rows
// AND file bytes — and one deleted 29 days ago is untouched.

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// deletedChatWithFile seeds one soft-deleted conversation (deleted
// daysAgo days ago) with one message and one attached file whose bytes
// live in the local storage root. Returns the conversation id and the
// file's storage key.
func deletedChatWithFile(
	t *testing.T, ctx context.Context, pool *pgxpool.Pool, store storage.Backend,
	tenant, user uuid.UUID, daysAgo int,
) (uuid.UUID, string) {
	t.Helper()
	conv := uuid.New()
	if _, err := pool.Exec(ctx, `
		INSERT INTO chat.conversations
			(id, tenant_id, user_id, title, model_id, is_deleted, deleted_at, updated_at)
		VALUES ($1, $2, $3, 'fix132-purge', 'gpt-4o-mini', true,
		        NOW() - make_interval(days => $4), NOW() - make_interval(days => $4))`,
		conv, tenant, user, daysAgo,
	); err != nil {
		t.Fatalf("seed conversation: %v", err)
	}
	var msg uuid.UUID
	if err := pool.QueryRow(ctx, `
		INSERT INTO chat.messages_masked (conversation_id, tenant_id, user_id, role, content_masked)
		VALUES ($1, $2, $3, 'user', 'my private text') RETURNING id`,
		conv, tenant, user,
	).Scan(&msg); err != nil {
		t.Fatalf("seed message: %v", err)
	}
	fileID := uuid.New()
	key := storage.JoinKey("conversation-files", tenant.String(), conv.String(), fileID.String()+"_notes.txt")
	body := []byte("file bytes that must go")
	if err := store.Upload(ctx, key, bytes.NewReader(body), "text/plain", int64(len(body))); err != nil {
		t.Fatalf("seed object: %v", err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO chat.conversation_files
			(id, conversation_id, message_id, tenant_id, user_id, filename, original_filename,
			 content_type, file_size_bytes, storage_path, extraction_status)
		VALUES ($1, $2, $3, $4, $5, 'notes.txt', 'notes.txt', 'text/plain', $6, $7, 'completed')`,
		fileID, conv, msg, tenant, user, len(body), key,
	); err != nil {
		t.Fatalf("seed file row: %v", err)
	}
	return conv, key
}

func convExists(t *testing.T, ctx context.Context, pool *pgxpool.Pool, id uuid.UUID) bool {
	t.Helper()
	var ok bool
	if err := pool.QueryRow(ctx,
		"SELECT EXISTS(SELECT 1 FROM chat.conversations WHERE id = $1)", id,
	).Scan(&ok); err != nil {
		t.Fatalf("exists: %v", err)
	}
	return ok
}

func rowCount(t *testing.T, ctx context.Context, pool *pgxpool.Pool, q string, args ...any) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, q, args...).Scan(&n); err != nil {
		t.Fatalf("count %q: %v", q, err)
	}
	return n
}

func objectOnDisk(t *testing.T, root, key string) bool {
	t.Helper()
	_, err := os.Stat(filepath.Join(root, filepath.FromSlash(key)))
	return err == nil
}

// TestSweep_DeletedChatIsPurgedAfter30DaysByDefault — no settings row
// for the tenant at all (the state of every tenant in production).
func TestSweep_DeletedChatIsPurgedAfter30DaysByDefault(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()

	root := t.TempDir()
	store, err := storage.NewLocalBackend(root)
	if err != nil {
		t.Fatalf("local storage: %v", err)
	}
	tenant, user := uuid.New(), uuid.New()
	old, oldKey := deletedChatWithFile(t, ctx, pool, store, tenant, user, 31)
	recent, recentKey := deletedChatWithFile(t, ctx, pool, store, tenant, user, 29)

	sweeper := NewTranscriptRetentionService(
		repository.NewTenantRetentionSettingsRepo(pool),
		repository.NewConversationEraseRepo(pool),
		repository.NewFileRepo(pool),
		repository.NewDeletionAuditRepo(pool),
		store,
	)
	stats := sweeper.EnforceOnce(ctx)

	// Deleted 31 days ago: every row and the bytes are gone.
	if convExists(t, ctx, pool, old) {
		t.Errorf("chat deleted 31 days ago still exists after the nightly pass (erased=%d)", stats.Erased)
	}
	if n := rowCount(t, ctx, pool, "SELECT count(*) FROM chat.messages_masked WHERE conversation_id = $1", old); n != 0 {
		t.Errorf("messages of the purged chat left behind: %d", n)
	}
	if n := rowCount(t, ctx, pool, "SELECT count(*) FROM chat.conversation_files WHERE conversation_id = $1", old); n != 0 {
		t.Errorf("file rows of the purged chat left behind: %d", n)
	}
	if objectOnDisk(t, root, oldKey) {
		t.Errorf("file bytes of the purged chat still on disk: %s", oldKey)
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.deletion_audit
		WHERE resource_id = $1 AND action = 'conversation_hard_deleted' AND rule = 'user_request'`, old); n != 1 {
		t.Errorf("hard-delete audit rows for the purged chat = %d, want 1", n)
	}

	// Deleted 29 days ago: still inside the window, untouched.
	if !convExists(t, ctx, pool, recent) {
		t.Errorf("chat deleted 29 days ago was erased — inside the 30-day window")
	}
	if n := rowCount(t, ctx, pool, "SELECT count(*) FROM chat.conversation_files WHERE conversation_id = $1", recent); n != 1 {
		t.Errorf("file row of the 29-day chat = %d, want 1", n)
	}
	if !objectOnDisk(t, root, recentKey) {
		t.Errorf("file bytes of the 29-day chat were removed")
	}
}

// The purge can still be held at dry-run or turned off: by the env
// ceiling for every tenant, or by one tenant's own flags.
func TestSweep_DeletedPurgeCanBeHeldOffOrDryRun(t *testing.T) {
	cases := []struct {
		name       string
		envMode    string
		tenantRow  *model.TenantRetentionSettings // nil = no row
		wantAudits int                            // retention_dry_run entries
	}{
		{name: "env off", envMode: "off"},
		{name: "env dry_run", envMode: "dry_run", wantAudits: 1},
		{name: "tenant off", envMode: "on", tenantRow: &model.TenantRetentionSettings{
			DeletedConversationPurgeDays: 30, DryRun: true,
			DeletedPurgeEnabled: false,
		}},
		{name: "tenant dry_run", envMode: "on", tenantRow: &model.TenantRetentionSettings{
			DeletedConversationPurgeDays: 30, DryRun: true,
			DeletedPurgeEnabled: true, DeletedPurgeDryRun: true,
		}, wantAudits: 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			pool := retentionTestPool(t)
			t.Cleanup(pool.Close)
			ctx := context.Background()
			root := t.TempDir()
			store, err := storage.NewLocalBackend(root)
			if err != nil {
				t.Fatalf("local storage: %v", err)
			}
			tenant, user := uuid.New(), uuid.New()
			if tc.tenantRow != nil {
				row := *tc.tenantRow
				row.TenantID = tenant
				if err := repository.NewTenantRetentionSettingsRepo(pool).Upsert(ctx, &row); err != nil {
					t.Fatalf("seed settings: %v", err)
				}
			}
			old, oldKey := deletedChatWithFile(t, ctx, pool, store, tenant, user, 45)

			sweeper := NewTranscriptRetentionService(
				repository.NewTenantRetentionSettingsRepo(pool),
				repository.NewConversationEraseRepo(pool),
				repository.NewFileRepo(pool),
				repository.NewDeletionAuditRepo(pool),
				store,
			)
			sweeper.SetDeletedPurgeMode(tc.envMode)
			stats := sweeper.EnforceOnce(ctx)

			if stats.Erased != 0 || !convExists(t, ctx, pool, old) || !objectOnDisk(t, root, oldKey) {
				t.Fatalf("%s: the purge deleted something (erased=%d)", tc.name, stats.Erased)
			}
			if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.deletion_audit
				WHERE tenant_id = $1 AND action = 'retention_dry_run'`, tenant); n != tc.wantAudits {
				t.Errorf("%s: dry-run audit entries = %d, want %d", tc.name, n, tc.wantAudits)
			}
		})
	}
}
