package agentmarket

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/google/uuid"
)

// Migration 008, down and back up, with rows in every place it adds: a chat
// named, pinned and deleted, the first chat named, a round carrying a form
// (its file marked "form", its item of kind "form"). The down leaves none of
// it and nothing else is touched; the second up brings the shape back, empty.
func TestAM10Migration008DownUndoesAllAndComesBack(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.install() // seeds the catalogue and the runner's capabilities
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: "sran.invoice-processing", Version: "1.0.0", Name: "ใบกำกับภาษี",
		Manifest: json.RawMessage(invoiceTemplatesManifest), Checksum: "inv", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	_, body := e.do("POST", "/installs", map[string]any{"slug": "sran.invoice-processing"})
	in, _ := e.store.GetInstall(ctx, e.user.TenantUUID, e.user.UserID, uuid.MustParse(body["install"].(map[string]any)["id"].(string)))
	base := "/installs/" + in.ID.String() + "/threads/"

	// what 008 holds, made the way a person makes it
	kept, gone := e.newChat(in), e.newChat(in)
	for _, step := range []struct {
		method, path string
		body         map[string]any
	}{
		{"PATCH", base + kept, map[string]any{"title": "งบ Q3 ส่งบอร์ด"}},
		{"PATCH", base + kept, map[string]any{"pinned": true}},
		{"PATCH", base + "main", map[string]any{"title": "แชทหลัก"}},
		{"DELETE", base + gone, nil},
	} {
		if code, b := e.do(step.method, step.path, step.body); code != 200 {
			t.Fatalf("%s %s: %d %v", step.method, step.path, code, b)
		}
	}
	round := e.finishedRun(in, "2026-09", "")
	plain := e.stagedFile(in, "invoices.pdf") // an ordinary file of the install: the down must leave it alone
	form := e.stagedFile(in, "ฟอร์มสรุป.docx")
	if code, b := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{form.ID.String()},
		"text": "ใส่ในฟอร์มนี้ให้หน่อย", "client_id": "m008"}); code != 200 || b["route"] != "form" {
		t.Fatalf("the form goes to the round: %d %v", code, b)
	}

	type shape struct{ cols, formStatus, formKind, recorded int }
	look := func() shape {
		return shape{
			cols: e.countRows(`SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market' AND (
				(table_name = 'agent_threads' AND column_name IN ('title', 'pinned_at', 'deleted_at')) OR
				(table_name = 'agent_installs' AND column_name IN ('first_chat_title', 'first_chat_pinned_at', 'first_chat_deleted_at')) OR
				(table_name = 'agent_runs' AND column_name = 'round_template'))`),
			formStatus: e.countRows(`SELECT count(*) FROM pg_constraint WHERE conname = 'agent_files_status_check'
				AND pg_get_constraintdef(oid) LIKE '%''form''%'`),
			formKind: e.countRows(`SELECT count(*) FROM pg_constraint WHERE conname = 'agent_thread_items_kind_check'
				AND pg_get_constraintdef(oid) LIKE '%''form''%'`),
			recorded: e.countRows(`SELECT count(*) FROM agent_market.schema_migrations WHERE version = '008_am10'`),
		}
	}
	type rows struct{ installs, threads, runs, files, items, formFiles, formItems, removed int }
	count := func() rows {
		return rows{
			installs:  e.countRows(`SELECT count(*) FROM agent_market.agent_installs`),
			threads:   e.countRows(`SELECT count(*) FROM agent_market.agent_threads`),
			runs:      e.countRows(`SELECT count(*) FROM agent_market.agent_runs`),
			files:     e.countRows(`SELECT count(*) FROM agent_market.agent_files`),
			items:     e.countRows(`SELECT count(*) FROM agent_market.agent_thread_items`),
			formFiles: e.countRows(`SELECT count(*) FROM agent_market.agent_files WHERE status = 'form'`),
			formItems: e.countRows(`SELECT count(*) FROM agent_market.agent_thread_items WHERE kind = 'form'`),
			removed:   e.countRows(`SELECT count(*) FROM agent_market.agent_files WHERE status = 'removed'`),
		}
	}

	// up: everything 008 adds is there and holds something
	if s := look(); s != (shape{7, 1, 1, 1}) {
		t.Fatalf("after the up: %+v", s)
	}
	before := count()
	held := e.countRows(`SELECT (SELECT count(*) FROM agent_market.agent_threads WHERE title IS NOT NULL OR pinned_at IS NOT NULL OR deleted_at IS NOT NULL)
		+ (SELECT count(*) FROM agent_market.agent_installs WHERE first_chat_title IS NOT NULL)
		+ (SELECT count(*) FROM agent_market.agent_runs WHERE round_template IS NOT NULL)`)
	if held != 4 || before.formFiles != 1 || before.formItems != 1 {
		t.Fatalf("the rows 008 holds: %d (2 chats, 1 first chat, 1 round) · %+v", held, before)
	}
	t.Logf("up        %+v · %+v · rows holding 008's data %d", look(), before, held)

	// down: none of it is left, and nothing else moved
	down, err := migrationFS.ReadFile("migrations/008_am10.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatalf("down: %v", err)
	}
	if s := look(); s != (shape{}) {
		t.Fatalf("the down leaves: %+v", s)
	}
	after := count()
	want := before
	want.items--                                     // the form's item goes (AM9 has no such kind)
	want.formFiles, want.formItems = 0, 0            // …
	want.removed = before.removed + before.formFiles // and its file is kept as a removed file, not deleted
	if after != want {
		t.Fatalf("rows after the down: %+v, want %+v", after, want)
	}
	t.Logf("down      %+v · %+v", look(), after)
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_files WHERE id = $1 AND status = 'staged'`, plain.ID); n != 1 {
		t.Fatal("the down touched an ordinary file")
	}
	// the shape AM9 runs on refuses what only 008 allows
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_files SET status = 'form' WHERE id = $1`, plain.ID); err == nil {
		t.Fatal("after the down a file can still be marked as a form")
	}

	// up again: the migrator applies 008 alone, and the shape is back — empty
	did, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(did) != 1 || did[0] != "008_am10" {
		t.Fatalf("up again: %v %v", did, err)
	}
	if s := look(); s != (shape{7, 1, 1, 1}) {
		t.Fatalf("after the second up: %+v", s)
	}
	if again := count(); again != after {
		t.Fatalf("the second up moved rows: %+v, was %+v", again, after)
	}
	t.Logf("up again  %+v · %+v · the migrator applied %v", look(), count(), did)
	// and it works: the chat that kept its row can be named again, the round still stands
	if code, b := e.do("PATCH", base+kept, map[string]any{"title": "ชื่อใหม่"}); code != 200 {
		t.Fatalf("rename after the second up: %d %v", code, b)
	}
	if r, err := e.store.RunByID(ctx, round.ID); err != nil || r.Status != RunSucceeded {
		t.Fatalf("the round after down and up: %v %v", r.Status, err)
	}
}
