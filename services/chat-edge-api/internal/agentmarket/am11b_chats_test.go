package agentmarket

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM11b C — one empty chat at a time, and a chat named for what it is.
//
// AM10 put chats under each agent. Nothing said what happens when "แชทใหม่กับ agent นี้" is pressed
// five times: five rows reading "แชทใหม่ · 6 ต.ค. 2569 · 0 รอบ". Now the button opens the empty chat
// that is already there; the rows of chats nothing ever happened in go when the list is read, but one;
// and a chat is named from the first thing that happens in it — the request, the period, the file, the
// words. A chat with a round, a message or a file, a pinned chat and one the person named are never
// touched, and nothing of any round is.

func (e *testEnv) postNewChat(in Install) map[string]any {
	e.t.Helper()
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/threads", nil)
	if code != 200 {
		e.t.Fatalf("new thread: %d %v", code, body)
	}
	return body
}

func TestAM11bNewChatOpensTheEmptyChatThatIsThere(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	// nothing has happened in the first chat: the button opens it, five times over
	for i := 0; i < 5; i++ {
		body := e.postNewChat(in)
		th := body["thread"].(map[string]any)
		if body["reused"] != true || th["id"] != nil || th["title"] != "แชทแรก" {
			t.Fatalf("press %d: an empty first chat is opened, not joined by another: %v", i+1, body)
		}
	}
	if list := e.chats(in); len(list) != 1 || list[0]["id"] != nil || list[0]["title"] != "แชทแรก" || list[0]["rounds"].(float64) != 0 {
		t.Fatalf("five presses, one chat: %v", list)
	}
	// a round in the first chat: now a new chat is a new chat — once
	e.finishedRun(in, "2026-09", "")
	first := e.postNewChat(in)
	tid, _ := first["thread"].(map[string]any)["id"].(string)
	if first["reused"] == true || tid == "" || first["thread"].(map[string]any)["title"] != "แชทใหม่" {
		t.Fatalf("with the first chat used, a chat is made: %v", first)
	}
	for i := 0; i < 4; i++ {
		body := e.postNewChat(in)
		if body["reused"] != true || body["thread"].(map[string]any)["id"] != tid {
			t.Fatalf("press %d: the empty chat is opened again: %v", i+2, body)
		}
	}
	list := e.chats(in)
	if len(list) != 2 || chatTitled(list, tid)["title"] != "แชทใหม่" || chatTitled(list, "main")["title"] != "กันยายน 2569" {
		t.Fatalf("five presses, one new chat, and no date on its name: %v", list)
	}
	// the greeting is there once
	if items := e.threadItems(in, tid); len(items) != 1 || items[0]["kind"] != "greeting" {
		t.Fatalf("the reused chat was greeted once: %v", items)
	}
}

func TestAM11bEmptyChatsAreTidiedAndNothingWithARoundIsTouched(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	mk := func() string {
		id, _, err := e.store.CreateThread(ctx, e.store.Pool, in)
		if err != nil {
			t.Fatal(err)
		}
		return id.String()
	}
	// what a person's sidebar holds from before: five empty chats, and beside them a chat with a round,
	// a chat with words only, a pinned empty chat and an empty chat they named
	empty := []string{mk(), mk(), mk(), mk(), mk()}
	withRound, withWords, pinned, named := mk(), mk(), mk(), mk()
	run := e.finishedRun(in, "2026-08", "")
	for _, q := range []struct {
		sql  string
		args []any
	}{
		{`UPDATE agent_market.agent_runs SET thread_id = $2 WHERE id = $1`, []any{run.ID, withRound}},
		{`UPDATE agent_market.agent_threads SET pinned_at = now() WHERE id = $1`, []any{pinned}},
		{`UPDATE agent_market.agent_threads SET title = 'ของฉัน' WHERE id = $1`, []any{named}},
	} {
		if _, err := e.store.Pool.Exec(ctx, q.sql, q.args...); err != nil {
			t.Fatal(err)
		}
	}
	words := uuid.MustParse(withWords)
	if _, _, err := e.store.AddItemIn(ctx, e.store.Pool, in.ID, &words, in.TenantID, in.UserID, nil, UserMessage{Text: "งบเดือนนี้ต้องส่งวันไหน"}, nil); err != nil {
		t.Fatal(err)
	}
	// the first chat is empty too: it is the one empty chat that stays (it is the install's own)
	list := e.chats(in)
	ids := map[string]bool{}
	for _, c := range list {
		if c["id"] != nil {
			ids[c["id"].(string)] = true
		}
	}
	for _, id := range empty {
		if ids[id] {
			t.Fatalf("an empty chat is still listed: %v", list)
		}
	}
	for name, id := range map[string]string{"with a round": withRound, "with words": withWords, "pinned": pinned, "named": named} {
		if !ids[id] {
			t.Fatalf("the chat %s is gone: %v", name, list)
		}
	}
	if len(list) != 5 || chatTitled(list, "main") == nil {
		t.Fatalf("the first chat and the four that are someone's: %d %v", len(list), list)
	}
	// named for what they are
	if got := chatTitled(list, withRound)["title"]; got != "สิงหาคม 2569" {
		t.Fatalf("a chat with a round, by its period: %v", got)
	}
	if got := chatTitled(list, withWords)["title"]; got != "งบเดือนนี้ต้องส่งวันไหน" {
		t.Fatalf("a chat with words only, by its first words: %v", got)
	}
	if got := chatTitled(list, named); got["title"] != "ของฉัน" || got["named"] != true {
		t.Fatalf("a chat the person named: %v", got)
	}
	// nothing of the round was touched
	if got, err := e.store.RunByID(ctx, run.ID); err != nil || got.Status != RunSucceeded || got.ThreadID == nil || got.ThreadID.String() != withRound {
		t.Fatalf("the round: %+v %v", got, err)
	}
	var live int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_threads WHERE install_id = $1 AND deleted_at IS NULL`, in.ID).Scan(&live)
	if live != 4 {
		t.Fatalf("chats left in the table: %d", live)
	}
	// with the first chat used, the newest empty chat is the one that stays
	e.finishedRun(in, "2026-09", "")
	a, b := mk(), mk()
	list = e.chats(in)
	if chatTitled(list, a) != nil || chatTitled(list, b) == nil {
		t.Fatalf("of two empty chats the newest stays: %v", list)
	}
}

func TestAM11bAChatIsNamedFromTheFirstThingThatHappensInIt(t *testing.T) {
	e, _ := auditEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	// an agent that works from a request: the request names the chat — also when the door was closed
	in := e.installWebAgent()
	msg := "/installs/" + in.ID.String() + "/messages"
	const request = "ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ"
	if code, b := e.do("POST", msg, map[string]any{"text": request, "client_id": "n1"}); code != 200 || b["route"] != "web_closed" {
		t.Fatalf("closed: %d %v", code, b)
	}
	list := e.chats(in)
	title, _ := chatTitled(list, "main")["title"].(string)
	if !strings.HasPrefix(request, strings.TrimSuffix(title, "…")) || len([]rune(title)) > 48 || !strings.HasSuffix(title, "…") {
		t.Fatalf("named for the request, on one line of 48 letters: %q", title)
	}
	// an agent that works from files: the first file names the chat until a round gives it a period
	bank := e.installBank()
	f := e.stagedFile(bank, "statement_2569-09.xlsx")
	if code, b := e.do("POST", "/installs/"+bank.ID.String()+"/messages", map[string]any{"file_ids": []string{f.ID.String()}, "client_id": "n2"}); code != 200 {
		t.Fatalf("files: %d %v", code, b)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET period = NULL WHERE install_id = $1`, bank.ID); err != nil {
		t.Fatal(err)
	}
	if got := chatTitled(e.chats(bank), "main")["title"]; got != "statement_2569-09.xlsx" {
		t.Fatalf("named for its first file: %v", got)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET period = '2026-09' WHERE install_id = $1`, bank.ID); err != nil {
		t.Fatal(err)
	}
	if got := chatTitled(e.chats(bank), "main")["title"]; got != "กันยายน 2569" {
		t.Fatalf("named for its period once a round has one: %v", got)
	}
	// in English the names the app makes are English; a person's words are theirs
	if code, body := e.do("GET", "/installs/"+bank.ID.String()+"/threads", nil, "Accept-Language", "en"); code != 200 ||
		body["threads"].([]any)[0].(map[string]any)["title"] != "September 2026" {
		t.Fatalf("on an English screen: %d %v", code, body)
	}
}
