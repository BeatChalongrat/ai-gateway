package service

// FIX132 C — deleting an account. Real database (a private clone of the
// test template) and a real local storage directory.
//
//   - coverage: every chat.* / identity.* table with a user column is in
//     the purge, the conversation cascade, or the documented keep-list —
//     a new table that holds user data cannot silently survive a purge;
//   - the request step hides every chat now, stops the user's tasks and
//     refuses every token issued so far;
//   - the purge erases everything the user owns (rows, file bytes,
//     export files, identity row) and nothing of anyone else's, writes
//     one audit row with counts, and a second purge finds nothing.

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/auth"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

func TestAccountPurgeCoverage_EveryUserTableIsAccountedFor(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()

	covered := map[string]bool{
		"identity.users":             true, // deleted by id
		"identity.invitation_tokens": true, // created_by deleted, used_by cleared
	}
	for _, t := range PurgeUserTables {
		covered[t.Table] = true
	}
	cascadeTable := map[string]string{"messages": "messages_masked"}
	for _, k := range repository.EraseKinds() {
		tbl := k
		if v, ok := cascadeTable[k]; ok {
			tbl = v
		}
		covered["chat."+tbl] = true
	}
	for tbl := range KeptUserTables {
		covered[tbl] = true
	}

	rows, err := pool.Query(ctx, `
		SELECT DISTINCT table_schema || '.' || table_name FROM information_schema.columns
		WHERE table_schema IN ('chat', 'identity')
		  AND column_name IN ('user_id', 'owner_user_id', 'created_by', 'used_by', 'uploaded_by', 'actor_user_id')
		ORDER BY 1`)
	if err != nil {
		t.Fatalf("information_schema: %v", err)
	}
	defer rows.Close()
	for rows.Next() {
		var tbl string
		if err := rows.Scan(&tbl); err != nil {
			t.Fatal(err)
		}
		if !covered[tbl] {
			t.Errorf("%s holds a user column but the account purge neither erases nor documents keeping it — add it to PurgeUserTables or KeptUserTables", tbl)
		}
	}
}

type accountFixture struct {
	tenant, user, other uuid.UUID
	conv, otherConv     uuid.UUID
	fileKey, otherKey   string
	kbKey, otherKBKey   string
	exportPath          string
}

func seedAccount(t *testing.T, ctx context.Context, pool *pgxpool.Pool, store storage.Backend, exportRoot string) accountFixture {
	t.Helper()
	f := accountFixture{tenant: uuid.New(), user: uuid.New(), other: uuid.New()}
	exec := func(q string, args ...any) {
		t.Helper()
		if _, err := pool.Exec(ctx, q, args...); err != nil {
			t.Fatalf("seed %q: %v", q[:40], err)
		}
	}
	exec(`INSERT INTO identity.tenants (id, name, slug, is_personal) VALUES ($1, 'alice''s workspace', $2, true)`,
		f.tenant, "t-"+f.tenant.String()[:12])
	for _, u := range []struct {
		id    uuid.UUID
		email string
	}{{f.user, "alice@example.com"}, {f.other, "bob@example.com"}} {
		exec(`INSERT INTO identity.users (id, tenant_id, email, name, role, is_active, provisioned_from)
		      VALUES ($1, $2, $3, $3, 'user', true, 'portal')`, u.id, f.tenant, u.email)
	}
	exec(`INSERT INTO identity.refresh_tokens (user_id, token_hash, family_id, expires_at)
	      VALUES ($1, 'h', $2, NOW() + interval '1 day')`, f.user, uuid.New())

	mkConv := func(owner uuid.UUID) (uuid.UUID, uuid.UUID, string) {
		conv := uuid.New()
		exec(`INSERT INTO chat.conversations (id, tenant_id, user_id, title, model_id)
		      VALUES ($1, $2, $3, 'สวัสดี', 'gpt-4o-mini')`, conv, f.tenant, owner)
		var msg uuid.UUID
		if err := pool.QueryRow(ctx, `INSERT INTO chat.messages_masked (conversation_id, tenant_id, user_id, role, content_masked)
			VALUES ($1, $2, $3, 'user', 'ข้อความส่วนตัว') RETURNING id`, conv, f.tenant, owner).Scan(&msg); err != nil {
			t.Fatal(err)
		}
		fileID := uuid.New()
		key := storage.JoinKey("conversation-files", f.tenant.String(), conv.String(), fileID.String()+"_a.txt")
		if err := store.Upload(ctx, key, bytes.NewReader([]byte("bytes")), "text/plain", 5); err != nil {
			t.Fatal(err)
		}
		exec(`INSERT INTO chat.conversation_files (id, conversation_id, message_id, tenant_id, user_id, filename,
		      original_filename, content_type, file_size_bytes, storage_path, extraction_status)
		      VALUES ($1, $2, $3, $4, $5, 'a.txt', 'a.txt', 'text/plain', 5, $6, 'completed')`,
			fileID, conv, msg, f.tenant, owner, key)
		exec(`INSERT INTO chat.feedback (message_id, tenant_id, user_id, rating) VALUES ($1, $2, $3, 'thumbs_up')`, msg, f.tenant, owner)
		exec(`INSERT INTO chat.conversation_states (conversation_id, tenant_id, user_id) VALUES ($1, $2, $3)`, conv, f.tenant, owner)
		exec(`INSERT INTO chat.canvas_artifacts (tenant_id, user_id, conversation_id, artifact_key, type, content)
		      VALUES ($1, $2, $3, 'k', 'markdown', 'x')`, f.tenant, owner, conv)
		exec(`INSERT INTO chat.cloud_scheduled_tasks (tenant_id, user_id, conversation_id, name, prompt, interval_seconds, next_run_at)
		      VALUES ($1, $2, $3, 'daily', 'สรุปข่าว', 86400, NOW() + interval '1 day')`, f.tenant, owner, conv)
		exec(`INSERT INTO chat.user_memories (tenant_id, user_id, memory_type, content, source_type)
		      VALUES ($1, $2, 'explicit_fact', 'แพ้กุ้ง', 'user_explicit')`, f.tenant, owner)
		exec(`INSERT INTO chat.memory_events (tenant_id, user_id, action, actor_type) VALUES ($1, $2, 'created', 'user')`, f.tenant, owner)
		exec(`INSERT INTO chat.projects (tenant_id, user_id, name) VALUES ($1, $2, 'ลูกค้า A')`, f.tenant, owner)
		exec(`INSERT INTO chat.folders (tenant_id, user_id, name) VALUES ($1, $2, 'f')`, f.tenant, owner)
		exec(`INSERT INTO chat.user_preferences (tenant_id, user_id) VALUES ($1, $2)`, f.tenant, owner)
		exec(`INSERT INTO chat.user_skills (tenant_id, user_id, name, instructions) VALUES ($1, $2, 's', 'do it')`, f.tenant, owner)
		return conv, msg, key
	}
	f.conv, _, f.fileKey = mkConv(f.user)
	f.otherConv, _, f.otherKey = mkConv(f.other)

	// FIX132 B: knowledge bases are private to their creator — one each, with a
	// stored document and a chunk.
	mkKB := func(owner uuid.UUID) string {
		kb, doc := uuid.New(), uuid.New()
		key := storage.JoinKey("knowledge", f.tenant.String(), kb.String(), doc.String()+"_policy.txt")
		if err := store.Upload(ctx, key, bytes.NewReader([]byte("นโยบาย")), "text/plain", int64(len("นโยบาย"))); err != nil {
			t.Fatal(err)
		}
		exec(`INSERT INTO chat.knowledge_bases (id, tenant_id, user_id, name) VALUES ($1, $2, $3, 'นโยบาย')`, kb, f.tenant, owner)
		exec(`INSERT INTO chat.knowledge_documents (id, knowledge_base_id, tenant_id, uploaded_by, filename, original_filename,
		      content_type, storage_path, status) VALUES ($1, $2, $3, $4, 'policy.txt', 'policy.txt', 'text/plain', $5, 'indexed')`,
			doc, kb, f.tenant, owner, key)
		exec(`INSERT INTO chat.knowledge_chunks (document_id, knowledge_base_id, tenant_id, content) VALUES ($1, $2, $3, 'ลาพักร้อน 12 วัน')`,
			doc, kb, f.tenant)
		return key
	}
	f.kbKey = mkKB(f.user)
	f.otherKBKey = mkKB(f.other)

	f.exportPath = filepath.Join(exportRoot, "exports", f.tenant.String(), f.user.String(), "x.zip")
	if err := os.MkdirAll(filepath.Dir(f.exportPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(f.exportPath, []byte("zip"), 0o600); err != nil {
		t.Fatal(err)
	}
	exec(`INSERT INTO chat.data_export_requests (tenant_id, user_id, export_type, status, file_path, file_size_bytes)
	      VALUES ($1, $2, 'full_account', 'completed', $3, 3)`, f.tenant, f.user, f.exportPath)
	return f
}

func newAccountDeletion(pool *pgxpool.Pool, store storage.Backend, root string) *AccountDeletionService {
	return NewAccountDeletionService(pool,
		repository.NewConversationEraseRepo(pool), repository.NewFileRepo(pool),
		repository.NewDeletionAuditRepo(pool), store, root)
}

func TestAccountDeletion_RequestHidesChatsStopsTasksAndRevokesSessions(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()
	root := t.TempDir()
	store, _ := storage.NewLocalBackend(filepath.Join(root, "uploads"))
	f := seedAccount(t, ctx, pool, store, root)

	svc := newAccountDeletion(pool, store, root)
	rev := auth.NewMemoryRevocations()
	svc.SetRevoker(rev)
	res, err := svc.RequestDeletion(ctx, f.user)
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	if res.ConversationsHidden != 1 || res.TasksStopped != 1 || !res.SessionsRevoked {
		t.Fatalf("result = %+v, want 1 hidden, 1 task stopped, revoked", res)
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.conversations
		WHERE user_id = $1 AND is_deleted AND deleted_at > NOW() - interval '1 minute'`, f.user); n != 1 {
		t.Errorf("hidden chats with deleted_at now = %d, want 1", n)
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.cloud_scheduled_tasks WHERE user_id = $1 AND enabled`, f.user); n != 0 {
		t.Errorf("enabled tasks left = %d", n)
	}
	// A token issued a minute ago is refused; the other user is untouched.
	if revoked, _ := rev.IsRevoked(ctx, "", f.user.String(), time.Now().Add(-time.Minute)); !revoked {
		t.Error("a token issued before the request is still accepted")
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.conversations WHERE user_id = $1 AND NOT is_deleted`, f.other); n != 1 {
		t.Errorf("another user's chat was hidden")
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.deletion_audit
		WHERE user_id = $1 AND action = 'account_deletion_requested'`, f.user); n != 1 {
		t.Errorf("request audit rows = %d, want 1", n)
	}
	// Idempotent.
	if res, err := svc.RequestDeletion(ctx, f.user); err != nil || res.ConversationsHidden != 0 {
		t.Errorf("second request = %+v, %v; want nothing new hidden", res, err)
	}
}

func TestAccountPurge_ErasesEverythingTheUserOwnsAndNothingElse(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()
	root := t.TempDir()
	uploads := filepath.Join(root, "uploads")
	store, _ := storage.NewLocalBackend(uploads)
	f := seedAccount(t, ctx, pool, store, root)

	userTables := []string{"chat.conversations", "chat.messages_masked"}
	for _, p := range PurgeUserTables {
		if p.Column == "user_id" {
			userTables = append(userTables, p.Table)
		}
	}
	otherBefore := map[string]int{}
	for _, tbl := range userTables {
		otherBefore[tbl] = rowCount(t, ctx, pool, `SELECT count(*) FROM `+tbl+` WHERE user_id = $1`, f.other)
	}

	svc := newAccountDeletion(pool, store, root)
	if _, err := svc.RequestDeletion(ctx, f.user); err != nil {
		t.Fatal(err)
	}
	counts, err := svc.Purge(ctx, f.user)
	if err != nil {
		t.Fatalf("purge: %v", err)
	}

	for _, tbl := range userTables {
		if n := rowCount(t, ctx, pool, `SELECT count(*) FROM `+tbl+` WHERE user_id = $1`, f.user); n != 0 {
			t.Errorf("%s: %d rows of the purged user left", tbl, n)
		}
		if n := rowCount(t, ctx, pool, `SELECT count(*) FROM `+tbl+` WHERE user_id = $1`, f.other); n != otherBefore[tbl] {
			t.Errorf("%s: the other user's rows went from %d to %d", tbl, otherBefore[tbl], n)
		}
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM identity.users WHERE id = $1`, f.user); n != 0 {
		t.Error("identity row of the purged user left")
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM identity.users WHERE id = $1`, f.other); n != 1 {
		t.Error("the other user's identity row was erased")
	}
	if objectOnDisk(t, uploads, f.fileKey) {
		t.Error("the purged user's file bytes are still on disk")
	}
	if !objectOnDisk(t, uploads, f.otherKey) {
		t.Error("the other user's file bytes were removed")
	}
	if _, err := os.Stat(f.exportPath); !os.IsNotExist(err) {
		t.Error("the purged user's export file is still on disk")
	}
	// Knowledge bases: the user's KB, its documents, chunks and stored file go;
	// the other user's stay.
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.knowledge_documents WHERE uploaded_by = $1`, f.user); n != 0 {
		t.Errorf("knowledge documents of the purged user left: %d", n)
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.knowledge_chunks c JOIN chat.knowledge_bases b
		ON b.id = c.knowledge_base_id WHERE b.user_id = $1`, f.other); n != 1 {
		t.Errorf("the other user's knowledge chunks = %d, want 1", n)
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.knowledge_chunks WHERE content = 'ลาพักร้อน 12 วัน'`); n != 1 {
		t.Errorf("knowledge chunks left = %d, want only the other user's 1", n)
	}
	if objectOnDisk(t, uploads, f.kbKey) {
		t.Error("the purged user's knowledge-base file is still on disk")
	}
	if !objectOnDisk(t, uploads, f.otherKBKey) {
		t.Error("the other user's knowledge-base file was removed")
	}
	if counts["conversations"] != 1 || counts["user_memories"] != 1 || counts["identity_users"] != 1 {
		t.Errorf("counts = %v", counts)
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM chat.deletion_audit
		WHERE user_id = $1 AND action = 'account_purged'`, f.user); n != 1 {
		t.Errorf("purge audit rows = %d, want 1", n)
	}
	var name string
	_ = pool.QueryRow(ctx, `SELECT name FROM identity.tenants WHERE id = $1`, f.tenant).Scan(&name)
	if name == "alice's workspace" {
		t.Error("the workspace still carries the name derived from the purged user's email")
	}

	// A second purge finds nothing.
	again, err := svc.Purge(ctx, f.user)
	if err != nil {
		t.Fatalf("second purge: %v", err)
	}
	if again["conversations"] != 0 || again["user_memories"] != 0 || again["identity_users"] != 0 {
		t.Errorf("second purge erased something: %v", again)
	}
}
