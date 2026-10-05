package agentmarket

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM10: chats under each agent — named for what they are, renamed, pinned,
// deleted without losing a working paper.

func (e *testEnv) newChat(in Install) string {
	e.t.Helper()
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/threads", nil)
	if code != 200 {
		e.t.Fatalf("new thread: %d %v", code, body)
	}
	return body["thread"].(map[string]any)["id"].(string)
}

func (e *testEnv) chats(in Install) []map[string]any {
	e.t.Helper()
	code, body := e.do("GET", "/installs/"+in.ID.String()+"/threads", nil)
	if code != 200 {
		e.t.Fatalf("threads: %d %v", code, body)
	}
	var out []map[string]any
	for _, x := range body["threads"].([]any) {
		out = append(out, x.(map[string]any))
	}
	return out
}

func chatTitled(list []map[string]any, id string) map[string]any {
	for _, c := range list {
		if (id == "main" && c["id"] == nil) || (c["id"] != nil && c["id"] == id) {
			return c
		}
	}
	return nil
}

func TestAChatIsNamedForItsRoundRenamedAndPinned(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	tid := e.newChat(in)
	list := e.chats(in)
	if len(list) != 2 || chatTitled(list, "main")["title"] != "แชทแรก" || !strings.HasPrefix(chatTitled(list, tid)["title"].(string), "แชทใหม่") {
		t.Fatalf("before any round a chat is named for when it began: %v", list)
	}
	// a round names the chat by its period
	run := e.finishedRun(in, "2026-09", "")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET thread_id = $2 WHERE id = $1`, run.ID, tid); err != nil {
		t.Fatal(err)
	}
	list = e.chats(in)
	if c := chatTitled(list, tid); c["title"] != "กันยายน 2569" || c["named"] != false || c["period"] != "2026-09" || c["rounds"].(float64) != 1 {
		t.Fatalf("named for its period: %v", c)
	}
	// rename: the person's own name wins, and is kept
	code, body := e.do("PATCH", "/installs/"+in.ID.String()+"/threads/"+tid, map[string]any{"title": "  งบ Q3 ส่งบอร์ด  "})
	if code != 200 || body["thread"].(map[string]any)["title"] != "งบ Q3 ส่งบอร์ด" || body["thread"].(map[string]any)["named"] != true {
		t.Fatalf("rename: %d %v", code, body)
	}
	if code, _ := e.do("PATCH", "/installs/"+in.ID.String()+"/threads/"+tid, map[string]any{"title": strings.Repeat("ก", 81)}); code != http.StatusBadRequest {
		t.Fatalf("a name of 81 runes: %d", code)
	}
	// an empty name puts the derived one back
	_, body = e.do("PATCH", "/installs/"+in.ID.String()+"/threads/"+tid, map[string]any{"title": ""})
	if body["thread"].(map[string]any)["title"] != "กันยายน 2569" {
		t.Fatalf("derived name back: %v", body)
	}
	// the first chat can be renamed and pinned too (it has no row of its own)
	_, body = e.do("PATCH", "/installs/"+in.ID.String()+"/threads/main", map[string]any{"title": "แชทหลัก", "pinned": true})
	if c := body["thread"].(map[string]any); c["title"] != "แชทหลัก" || c["pinned_at"] == nil {
		t.Fatalf("first chat renamed and pinned: %v", body)
	}
	// pinned first, then by activity; unpin puts it back
	_, body = e.do("PATCH", "/installs/"+in.ID.String()+"/threads/"+tid, map[string]any{"pinned": true})
	if body["thread"].(map[string]any)["pinned_at"] == nil {
		t.Fatalf("pin: %v", body)
	}
	_, body = e.do("PATCH", "/installs/"+in.ID.String()+"/threads/main", map[string]any{"pinned": false})
	if body["thread"].(map[string]any)["pinned_at"] != nil {
		t.Fatalf("unpin: %v", body)
	}
	list = e.chats(in)
	if list[0]["id"] != tid {
		t.Fatalf("the pinned chat comes first: %v", list)
	}
	// someone else's chat id
	if code, _ := e.do("PATCH", "/installs/"+in.ID.String()+"/threads/"+uuid.NewString(), map[string]any{"title": "x"}); code != http.StatusNotFound {
		t.Fatalf("unknown chat: %d", code)
	}
}

// Deleting a chat takes it off the list. Its finished round, the run page,
// the artifacts and the files stay; a round still waiting in it is cancelled
// and charged nothing; the chat itself answers THREAD_GONE from then on.
func TestDeletingAChatKeepsItsRoundsEvidence(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	tid := e.newChat(in)
	done := e.finishedRun(in, "2026-08", "")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET thread_id = $2 WHERE id = $1`, done.ID, tid); err != nil {
		t.Fatal(err)
	}
	art, _, err := e.store.InsertArtifact(ctx, Artifact{RunID: done.ID, InstallID: in.ID, Format: "docx", Kind: "report", Version: 1,
		StorageKey: "agent-market/x.docx", SHA256: "x", Size: 3, Name: "x.docx", ProducedByStep: "step-1"}, in.TenantID, in.UserID)
	if err != nil {
		t.Fatal(err)
	}
	// a waiting round in the same chat
	waiting, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum, ThreadID: ptrUUID(uuid.MustParse(tid))}, "w1")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input', awaiting_since = now() WHERE id = $1`, waiting.ID); err != nil {
		t.Fatal(err)
	}
	code, body := e.do("DELETE", "/installs/"+in.ID.String()+"/threads/"+tid, nil)
	if code != 200 || body["deleted"] != true || body["cancelled_runs"].(float64) != 1 || body["kept_runs"].(float64) != 1 {
		t.Fatalf("delete: %d %v", code, body)
	}
	// off the list
	if c := chatTitled(e.chats(in), tid); c != nil {
		t.Fatalf("still listed: %v", c)
	}
	// the chat answers THREAD_GONE for reading and writing
	for _, path := range []string{"/thread?thread=" + tid, "/events?thread=" + tid} {
		if code, body := e.do("GET", "/installs/"+in.ID.String()+path, nil); code != http.StatusNotFound || body["error"].(map[string]any)["code"] != "THREAD_GONE" {
			t.Fatalf("%s: %d %v", path, code, body)
		}
	}
	if code, body := e.do("POST", "/installs/"+in.ID.String()+"/rounds", map[string]any{"thread_id": tid}); code != http.StatusNotFound {
		t.Fatalf("a round in a deleted chat: %d %v", code, body)
	}
	if code, _ := e.do("DELETE", "/installs/"+in.ID.String()+"/threads/"+tid, nil); code != http.StatusNotFound {
		t.Fatalf("deleting twice: %d", code)
	}
	// the evidence lives on: the run page, its artifact, the runs list
	code, body = e.do("GET", "/runs/"+done.ID.String(), nil)
	if code != 200 || body["run"].(map[string]any)["status"] != "succeeded" || len(body["artifacts"].([]any)) != 1 {
		t.Fatalf("run page: %d %v", code, body)
	}
	if code, _ := e.do("GET", "/artifacts/"+art.ID.String()+"/download", nil); code == http.StatusNotFound {
		t.Fatal("the artifact is gone")
	}
	_, body = e.do("GET", "/installs/"+in.ID.String()+"/runs", nil)
	ids := map[string]string{}
	for _, r := range body["runs"].([]any) {
		m := r.(map[string]any)
		ids[m["id"].(string)] = m["status"].(string)
	}
	if ids[done.ID.String()] != "succeeded" || ids[waiting.ID.String()] != "cancelled" {
		t.Fatalf("the runs list after the delete: %v", ids)
	}
	var charged int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_budget_ledger WHERE run_id = $1 AND voided_at IS NULL`, waiting.ID).Scan(&charged)
	if charged != 0 {
		t.Fatalf("the cancelled round is charged: %d rows", charged)
	}
	// deleting the first chat works the same way, and a reminder's next round
	// then opens a chat of its own instead of the deleted one
	code, body = e.do("DELETE", "/installs/"+in.ID.String()+"/threads/main", nil)
	if code != 200 {
		t.Fatalf("delete first chat: %d %v", code, body)
	}
	if code, body := e.do("GET", "/installs/"+in.ID.String()+"/thread", nil); code != http.StatusNotFound || body["error"].(map[string]any)["code"] != "THREAD_GONE" {
		t.Fatalf("the first chat after its delete: %d %v", code, body)
	}
	if n := len(e.chats(in)); n != 0 {
		t.Fatalf("no chat listed: %d", n)
	}
	var gone bool
	_ = e.store.Pool.QueryRow(ctx, `SELECT first_chat_deleted_at IS NOT NULL FROM agent_market.agent_installs WHERE id = $1`, in.ID).Scan(&gone)
	if !gone {
		t.Fatal("the install does not record the first chat's delete")
	}
	// a new chat can still be started, and lists alone
	tid2 := e.newChat(in)
	if list := e.chats(in); len(list) != 1 || list[0]["id"] != tid2 {
		t.Fatalf("after deleting everything a new chat stands alone: %v", list)
	}
}

func ptrUUID(u uuid.UUID) *uuid.UUID { return &u }

// A reminder never reopens a chat the person deleted: its round goes to a chat
// of its own, and the notification's link points there.
func TestAReminderSkipsADeletedFirstChat(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	id := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	if code, _ := e.do("DELETE", "/installs/"+in.ID.String()+"/threads/main", nil); code != 200 {
		t.Fatalf("delete first chat: %d", code)
	}
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	if p := e.pass(thaiAt(2026, 10, 5, 9, 1)); p.Fired != 1 {
		t.Fatalf("fired: %+v", p)
	}
	runs := e.installRuns(in)
	if len(runs) != 1 || runs[0].ThreadID == nil {
		t.Fatalf("the round opened in a chat of its own: %+v", runs)
	}
	list := e.chats(in)
	if len(list) != 1 || list[0]["id"] != runs[0].ThreadID.String() || list[0]["waiting"] != true {
		t.Fatalf("that chat is the only one listed, waiting: %v", list)
	}
	var link string
	_ = e.store.Pool.QueryRow(context.Background(), `SELECT link FROM agent_market.notifications WHERE install_id = $1 ORDER BY created_at DESC LIMIT 1`, in.ID).Scan(&link)
	if !strings.Contains(link, "thread="+runs[0].ThreadID.String()) {
		t.Fatalf("the notification links to the new chat: %q", link)
	}
}
