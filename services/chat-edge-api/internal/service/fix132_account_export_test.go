package service

// FIX132 C — "export all my data": one ZIP the user can open. Real
// database clone and a real local storage directory; the ZIP is opened
// and read back.

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

type exportFixture struct {
	tenant, user, other uuid.UUID
	fileBytes           []byte
}

func seedExportUser(t *testing.T, ctx context.Context, pool *pgxpool.Pool, store storage.Backend) exportFixture {
	t.Helper()
	f := exportFixture{tenant: uuid.New(), user: uuid.New(), other: uuid.New(), fileBytes: []byte("ใบเสนอราคา bytes")}
	exec := func(q string, args ...any) {
		t.Helper()
		if _, err := pool.Exec(ctx, q, args...); err != nil {
			t.Fatalf("seed %q: %v", q[:40], err)
		}
	}
	project := uuid.New()
	exec(`INSERT INTO chat.projects (id, tenant_id, user_id, name, instructions) VALUES ($1, $2, $3, 'ลูกค้า ก', 'ตอบเป็นทางการ')`,
		project, f.tenant, f.user)
	mkConv := func(owner uuid.UUID, title string, archived, deleted bool, proj *uuid.UUID) uuid.UUID {
		id := uuid.New()
		exec(`INSERT INTO chat.conversations (id, tenant_id, user_id, title, model_id, is_archived, is_deleted, deleted_at, project_id)
		      VALUES ($1, $2, $3, $4, 'gpt-4o-mini', $5, $6, CASE WHEN $6 THEN NOW() END, $7)`,
			id, f.tenant, owner, title, archived, deleted, proj)
		return id
	}
	msg := func(conv, owner uuid.UUID, role, content string, active bool) uuid.UUID {
		var id uuid.UUID
		if err := pool.QueryRow(ctx, `INSERT INTO chat.messages_masked
			(conversation_id, tenant_id, user_id, role, content_masked, is_active)
			VALUES ($1, $2, $3, $4, $5, $6) RETURNING id`, conv, f.tenant, owner, role, content, active).Scan(&id); err != nil {
			t.Fatal(err)
		}
		return id
	}
	c1 := mkConv(f.user, "สรุปประชุม", false, false, &project)
	m1 := msg(c1, f.user, "user", "สวัสดีครับ <b>ทดสอบ</b>", true)
	msg(c1, f.user, "user", "ข้อความเวอร์ชันเก่าที่ถูกแก้", false)
	msg(c1, f.user, "assistant", "สวัสดีครับ มีอะไรให้ช่วย", true)
	c2 := mkConv(f.user, "archived chat", true, false, nil)
	msg(c2, f.user, "user", "archived text", true)
	gone := mkConv(f.user, "deleted chat", false, true, nil)
	msg(gone, f.user, "user", "SHOULD-NOT-EXPORT deleted", true)
	oc := mkConv(f.other, "other user's chat", false, false, nil)
	msg(oc, f.other, "user", "SHOULD-NOT-EXPORT other user", true)

	put := func(conv uuid.UUID, msgID *uuid.UUID, name string, body []byte) {
		id := uuid.New()
		key := storage.JoinKey("conversation-files", f.tenant.String(), conv.String(), id.String()+"_"+name)
		if err := store.Upload(ctx, key, bytes.NewReader(body), "application/octet-stream", int64(len(body))); err != nil {
			t.Fatal(err)
		}
		exec(`INSERT INTO chat.conversation_files (id, conversation_id, message_id, tenant_id, user_id, filename,
		      original_filename, content_type, file_size_bytes, storage_path, extraction_status)
		      VALUES ($1, $2, $3, $4, $5, $6, $6, 'application/pdf', $7, $8, 'completed')`,
			id, conv, msgID, f.tenant, f.user, name, len(body), key)
	}
	put(c1, &m1, "ใบเสนอราคา.pdf", f.fileBytes)
	put(c1, nil, "draft.txt", []byte("never sent"))

	exec(`INSERT INTO chat.user_memories (tenant_id, user_id, memory_type, content, source_type, status)
	      VALUES ($1, $2, 'explicit_fact', 'แพ้กุ้ง', 'user_explicit', 'active')`, f.tenant, f.user)
	exec(`INSERT INTO chat.user_memories (tenant_id, user_id, memory_type, content, source_type, status)
	      VALUES ($1, $2, 'explicit_fact', '[deleted]', 'user_explicit', 'deleted')`, f.tenant, f.user)
	exec(`INSERT INTO chat.user_memories (tenant_id, user_id, memory_type, content, source_type, status)
	      VALUES ($1, $2, 'explicit_fact', 'SHOULD-NOT-EXPORT other memory', 'user_explicit', 'active')`, f.tenant, f.other)
	exec(`INSERT INTO chat.user_skills (tenant_id, user_id, name, instructions) VALUES ($1, $2, 'สรุปข่าว', 'สรุปสั้นๆ')`, f.tenant, f.user)
	exec(`INSERT INTO chat.user_preferences (tenant_id, user_id, custom_instructions_about) VALUES ($1, $2, 'ฉันเป็นนักบัญชี')`, f.tenant, f.user)
	return f
}

func newExportService(pool *pgxpool.Pool, store storage.Backend, root string, maxBytes int64) *AccountExportService {
	return NewAccountExportService(AccountExportSources{
		Conversations: repository.NewConversationRepo(pool),
		Messages:      repository.NewMessageRepo(pool),
		Files:         repository.NewFileRepo(pool),
		Memories:      repository.NewMemoryRepo(pool),
		Projects:      repository.NewProjectRepo(pool),
		Preferences:   repository.NewPreferencesRepo(pool),
		Skills:        repository.NewSkillRepo(pool),
		Exports:       repository.NewDataExportRepo(pool),
		Settings:      repository.NewTenantMemorySettingsRepo(pool),
	}, store, root, maxBytes, 100)
}

func readZip(t *testing.T, path string) map[string][]byte {
	t.Helper()
	zr, err := zip.OpenReader(path)
	if err != nil {
		t.Fatalf("open zip: %v", err)
	}
	defer zr.Close()
	out := map[string][]byte{}
	for _, f := range zr.File {
		rc, err := f.Open()
		if err != nil {
			t.Fatal(err)
		}
		b, _ := io.ReadAll(rc)
		rc.Close()
		out[f.Name] = b
	}
	return out
}

func runExport(t *testing.T, ctx context.Context, svc *AccountExportService, pool *pgxpool.Pool, f exportFixture) (*model.DataExportRequest, map[string][]byte) {
	t.Helper()
	row, err := svc.Start(ctx, f.user, f.tenant)
	if err != nil {
		t.Fatalf("start: %v", err)
	}
	if row.Status != model.ExportStatusPending {
		t.Fatalf("new export status = %q, want pending", row.Status)
	}
	svc.Wait()
	got, err := repository.NewDataExportRepo(pool).GetByID(ctx, row.ID, f.user, f.tenant)
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != model.ExportStatusCompleted || got.FilePath == "" || got.ExpiresAt == nil {
		t.Fatalf("export row after build = %+v", got)
	}
	return got, readZip(t, got.FilePath)
}

func TestAccountExport_ZipHoldsTheUsersDataAndNothingElse(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()
	root := t.TempDir()
	store, _ := storage.NewLocalBackend(filepath.Join(root, "uploads"))
	f := seedExportUser(t, ctx, pool, store)
	svc := newExportService(pool, store, root, 500<<20)
	svc.SetDisplayResolver(func(_ context.Context, m model.Message, _, _ uuid.UUID) string {
		return m.ContentMasked // what the chat shows with the display lifecycle off
	})

	row, files := runExport(t, ctx, svc, pool, f)
	if row.ExpiresAt.Sub(*row.CompletedAt).Hours() < 23.9 {
		t.Errorf("download window = %v, want 24h", row.ExpiresAt.Sub(*row.CompletedAt))
	}
	for _, name := range []string{"README.txt", "chat.html", "conversations.json", "memories.json", "projects.json", "settings.json"} {
		if _, ok := files[name]; !ok {
			t.Errorf("zip lacks %s", name)
		}
	}
	for name, b := range files {
		if strings.Contains(string(b), "SHOULD-NOT-EXPORT") {
			t.Errorf("%s carries data it must not (a deleted chat or another user's)", name)
		}
	}

	var convs []exportConversationJSON
	if err := json.Unmarshal(files["conversations.json"], &convs); err != nil {
		t.Fatalf("conversations.json: %v", err)
	}
	if len(convs) != 2 {
		t.Fatalf("conversations = %d, want 2 (live + archived)", len(convs))
	}
	var meeting *exportConversationJSON
	for i := range convs {
		if convs[i].Title == "สรุปประชุม" {
			meeting = &convs[i]
		}
	}
	if meeting == nil || meeting.ProjectName != "ลูกค้า ก" {
		t.Fatalf("meeting chat or its project missing: %+v", convs)
	}
	if len(meeting.Messages) != 2 {
		t.Fatalf("messages = %d, want 2 (the edited-away version is not what the chat shows)", len(meeting.Messages))
	}
	if meeting.Messages[0].Content != "สวัสดีครับ <b>ทดสอบ</b>" || len(meeting.Messages[0].Files) != 1 {
		t.Fatalf("first message = %+v", meeting.Messages[0])
	}
	att := meeting.Messages[0].Files[0]
	if att.PathInZip == "" || !bytes.Equal(files[att.PathInZip], f.fileBytes) {
		t.Fatalf("attachment bytes missing or wrong at %q", att.PathInZip)
	}
	if len(meeting.UnsentFiles) != 1 || files[meeting.UnsentFiles[0].PathInZip] == nil {
		t.Fatalf("the uploaded-but-unsent file is missing: %+v", meeting.UnsentFiles)
	}

	page := string(files["chat.html"])
	if !strings.Contains(page, `<meta charset="utf-8">`) || !strings.Contains(page, "สวัสดีครับ มีอะไรให้ช่วย") {
		t.Error("chat.html is not a UTF-8 page carrying the Thai text")
	}
	if strings.Contains(page, "<b>ทดสอบ</b>") || !strings.Contains(page, "&lt;b&gt;ทดสอบ&lt;/b&gt;") {
		t.Error("chat.html must escape message text, not render it as HTML")
	}
	if !strings.Contains(page, `href="`+att.PathInZip+`"`) {
		t.Error("chat.html does not link the attachment inside the ZIP")
	}

	var mem struct{ Memories []model.Memory }
	_ = json.Unmarshal(files["memories.json"], &mem)
	if len(mem.Memories) != 1 || mem.Memories[0].Content != "แพ้กุ้ง" {
		t.Errorf("memories.json = %+v, want only the live memory", mem.Memories)
	}
	if !strings.Contains(string(files["projects.json"]), "ตอบเป็นทางการ") {
		t.Error("projects.json lacks the project instructions")
	}
	if s := string(files["settings.json"]); !strings.Contains(s, "สรุปข่าว") || !strings.Contains(s, "ฉันเป็นนักบัญชี") {
		t.Error("settings.json lacks the skill or the custom instructions")
	}
	if !strings.Contains(string(files["README.txt"]), "ไม่ได้รวมไว้") || !strings.Contains(string(files["README.txt"]), "Not included") {
		t.Error("README.txt does not say what is not included")
	}
}

func TestAccountExport_FilesPastTheCapAreListedNotPacked(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	ctx := context.Background()
	root := t.TempDir()
	store, _ := storage.NewLocalBackend(filepath.Join(root, "uploads"))
	f := seedExportUser(t, ctx, pool, store)
	svc := newExportService(pool, store, root, 12) // room for "never sent" (10 bytes) only

	_, files := runExport(t, ctx, svc, pool, f)
	packed := 0
	for name := range files {
		if strings.HasPrefix(name, "files/") {
			packed++
		}
	}
	if packed != 1 {
		t.Errorf("files packed = %d, want 1 under a 12-byte cap", packed)
	}
	if !strings.Contains(string(files["README.txt"]), "over the size cap") {
		t.Error("README.txt does not list the file left out by the cap")
	}
}
