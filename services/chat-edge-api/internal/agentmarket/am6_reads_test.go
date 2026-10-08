package agentmarket

import (
	"bufio"
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// AM6 — the filter is inverted. AM5 took out the error shapes it knew; an
// error of a shape nobody listed went through. Now only text that was built
// for a person reaches a person: a field that is not declared is not sent,
// and a declared text is sent only when it reads as written for a person.

// neverListed are technical strings of shapes no pattern of the AM5 list
// names: no exception suffix, no "error:", no identifier with an underscore,
// no three lower-case words in a row, no path, no address.
var neverListed = []string{
	"zx81 frobnicate qux/9 <<retry budget>>",
	"0xDEADBEEF @ ModuleLoader",
	"ValueProblem in Step seven: Pipeline halted",
}

func TestTheOldListMissesAShapeNobodyListed(t *testing.T) {
	for _, s := range neverListed {
		if LooksTechnical(s) || LooksLikeError(s) {
			t.Fatalf("%q is a shape the old list knows: the test needs one it does not", s)
		}
		if in := "อ่านไฟล์ที่ส่งมาไม่สำเร็จ " + s; LooksTechnical(in) {
			t.Fatalf("%q inside a Thai sentence is a shape the old list knows", s)
		}
	}
}

// unlistedItems writes, straight into the table, one item of every kind the
// agent's side writes, each carrying the never-listed strings: in a text a
// person reads, and in a field nothing declares.
func (e *testEnv) unlistedItems(in Install, run Run) {
	e.t.Helper()
	a, b, c := neverListed[0], neverListed[1], neverListed[2]
	rid := run.ID.String()
	e.legacyItem(in, run, "agent_text", map[string]any{"run_id": rid, "text": a, "debug": b, "trace": map[string]any{"frame": c}})
	e.legacyItem(in, run, "agent_text", map[string]any{"run_id": rid, "tone": "error", "text": "สร้างไฟล์ผลงานไม่สำเร็จ " + a,
		"failure": map[string]any{"reason": "render_failed", "what": "สร้างไฟล์ผลงานไม่สำเร็จ: " + b, "next": c, "files": a, "charged": b,
			"failed_checks": []string{"รายงาน: หัวข้อครบทุกส่วน", c}, "cause": a}})
	e.legacyItem(in, run, "notice", map[string]any{"run_id": rid, "text": "อัปเกรดไม่สำเร็จ: " + c, "detail": a})
	e.legacyItem(in, run, "progress", map[string]any{"run_id": rid, "kind": "run", "status": "failed", "error": b, "steps": []map[string]any{
		{"key": "parse", "label": "อ่านไฟล์ 1 ไฟล์", "status": "done", "detail": "GL_2569-09.xlsx"},
		{"key": "render", "label": "สร้างไฟล์ผลลัพธ์", "status": "failed", "detail": a, "stderr": c},
		{"key": "quality", "label": b, "status": "failed", "detail": "ตรวจไม่ผ่าน " + c}}})
	e.legacyItem(in, run, "outputs", map[string]any{"run_id": rid, "period_label": "กันยายน 2569", "log": a,
		"gate":    map[string]any{"passed": false, "what": "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท " + b, "next": c},
		"summary": map[string]any{"period_label": "กันยายน 2569", "note": a, "not_included": []map[string]any{{"doc_key": "d1", "name": "a.pdf", "reason": b, "detail": c, "how": a}}},
		"artifacts": []map[string]any{{"id": uuid.NewString(), "name": "a.xlsx", "format": "xlsx", "size": 10, "version": 1,
			"quality": map[string]any{"passed": true, "checks": []map[string]any{{"name": "sheets", "ok": true, "label": "ชีตครบ", "detail": a},
				{"name": "fonts", "ok": false, "label": c, "detail": "ฟอนต์ไทยฝังครบ", "raw": b}}}}}})
	q := []map[string]any{{"key": "k1", "code": "period_mixed", "severity": "blocking", "header": "รายการนอกงวด",
		"question": "พบรายการนอกงวด 12 รายการ จะให้ตัดออกไหมครับ " + a, "why": b,
		"options": []map[string]any{{"label": "ตัดออก", "description": c, "value": "exclude"}, {"label": "นับรวมด้วย", "description": "นับรายการทั้งหมด", "value": "include"}},
		"default": "exclude", "default_label": "ตัดออก",
		"evidence": map[string]any{"file": "GL_2569-09.xlsx", "count": 12, "error": a, "question": b, "rows": [][]any{{1, "ขายสินค้า", "100.00"}}},
		"input":    map[string]any{"kind": "text", "hint": c}}}
	e.legacyItem(in, run, "card", map[string]any{"card_id": uuid.NewString(), "run_id": rid, "kind": "questions", "moment": "preflight",
		"blocking": true, "status": "open", "intro": "มี 1 เรื่องที่ผมไม่แน่ใจ " + b, "note": c, "questions": q, "engine": a})
	e.legacyItem(in, run, "intent", map[string]any{"status": "chosen", "choice": "about_last", "text": "ขอไฟล์ PDF", "item_id": uuid.NewString(),
		"last_round": map[string]any{"run_id": rid, "period_label": "กันยายน 2569"}, "note": "พิมพ์คำถามได้เลย " + a, "why": b})
	e.legacyItem(in, run, "greeting", map[string]any{"text": "สวัสดีครับ " + c, "tagline": a, "cannot": "อ่านอย่างเดียว", "slug": "sran.financial-report",
		"version": "1.0.0", "needs": []map[string]any{{"role": "primary", "label": "บัญชีแยกประเภท (GL)", "note": b, "required": true}}, "banner": a})
}

func TestTextOfAShapeNobodyListedNeverReachesAPerson(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run := e.failedRun(in, "render_failed", "GL_2569-09.xlsx")
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_run_steps (run_id, seq, name, kind, status, idempotency_key, error)
		VALUES ($1, 1, 'render', 'deliverable', 'failed', 'k-render', $2)`, run.ID, neverListed[0]); err != nil {
		t.Fatal(err)
	}
	e.unlistedItems(in, run)
	// the thread
	var sent []string
	for _, it := range e.threadItems(in, "") {
		b, _ := json.Marshal(it["payload"])
		sent = append(sent, it["kind"].(string)+" "+string(b))
	}
	joined := strings.Join(sent, "\n")
	for _, bad := range []string{"zx81", "frobnicate", "qux/9", "retry budget", "0xDEADBEEF", "ModuleLoader", "ValueProblem", "Pipeline halted", "Step seven"} {
		if strings.Contains(joined, bad) {
			t.Fatalf("the thread sends %q to a person:\n%s", bad, joined)
		}
	}
	// what was built for a person is still there
	for _, kept := range []string{"สร้างไฟล์ผลงานไม่สำเร็จ", "รายงาน: หัวข้อครบทุกส่วน", "อ่านไฟล์ 1 ไฟล์", "GL_2569-09.xlsx", "สร้างไฟล์ผลลัพธ์", "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท",
		"กันยายน 2569", "ชีตครบ", "ฟอนต์ไทยฝังครบ", "รายการนอกงวด", "พบรายการนอกงวด 12 รายการ จะให้ตัดออกไหมครับ", "ตัดออก", "นับรวมด้วย", "นับรายการทั้งหมด",
		"ขายสินค้า", "มี 1 เรื่องที่ผมไม่แน่ใจ", "ขอไฟล์ PDF", "พิมพ์คำถามได้เลย", "สวัสดีครับ", "อ่านอย่างเดียว", "a.xlsx", "a.pdf"} {
		if !strings.Contains(joined, kept) {
			t.Fatalf("what a person can read stays (%q):\n%s", kept, joined)
		}
	}
	// AM14: a greeting is read from the agent's own manifest in the language of the screen, not from the
	// words its row was first written with — so the row's own label is not what a person is shown; the
	// manifest's is (testManifest: "GL"), and the day's facts of the item stay
	if strings.Contains(joined, "บัญชีแยกประเภท (GL)") || !strings.Contains(joined, `"label":"GL"`) {
		t.Fatalf("the greeting's tick list is the manifest's own: %s", joined)
	}
	// the event stream sends the same items
	cctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(cctx, "GET", e.srv.URL+"/agent-market/installs/"+in.ID.String()+"/events?after=0", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	sc := bufio.NewScanner(resp.Body)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	events := 0
	for sc.Scan() && events < len(sent) {
		line := sc.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		events++
		for _, bad := range []string{"zx81", "frobnicate", "0xDEADBEEF", "ModuleLoader", "ValueProblem", "Pipeline halted"} {
			if strings.Contains(line, bad) {
				t.Fatalf("the event stream sends %q to a person: %s", bad, line)
			}
		}
	}
	if events == 0 {
		t.Fatal("the event stream sent nothing")
	}
	// nothing is lost: the rows keep what was written, and the run page keeps the step's error
	var kept int
	if err := e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_thread_items WHERE install_id = $1 AND payload::text LIKE '%frobnicate%'`,
		in.ID).Scan(&kept); err != nil || kept == 0 {
		t.Fatalf("the rows keep what was written: %d %v", kept, err)
	}
	code, page := e.do("GET", "/runs/"+run.ID.String(), nil)
	if code != 200 {
		t.Fatalf("run page: %d %v", code, page)
	}
	found := false
	for _, s := range page["steps"].([]any) {
		found = found || s.(map[string]any)["error"] == neverListed[0]
	}
	if !found {
		t.Fatalf("the run page shows the step's own error: %v", page["steps"])
	}
	// what the person typed is theirs, whatever it says
	e.legacyItem(in, run, "user_text", map[string]any{"text": "ทำไมขึ้น " + neverListed[0], "status": "handled"})
	mine := lastOfKind(e.threadItems(in, ""), "user_text")
	if mine == nil || mine["payload"].(map[string]any)["text"] != "ทำไมขึ้น "+neverListed[0] {
		t.Fatalf("a person's own message is never rewritten: %v", mine)
	}
}

func TestAPackageCannotPutAnUnlistedShapeOnTheScreen(t *testing.T) {
	for _, bad := range neverListed {
		// a quality check the package worded, and a gate's own sentences
		f := FailureFor("quality_gate", FailureFacts{Kind: KindRun, HasFiles: true, KeepDays: 7,
			FailedChecks: []string{"รายงาน: หัวข้อครบทุกส่วน", "สไลด์: " + bad, bad}, GateWhat: bad, GateNext: "ลองใหม่ " + bad})
		b, _ := json.Marshal(f)
		for _, part := range strings.Fields(bad) {
			if len(part) > 3 && strings.Contains(string(b), part) {
				t.Fatalf("%q of %q reached the person: %s", part, bad, b)
			}
		}
		if !strings.Contains(string(b), "รายงาน: หัวข้อครบทุกส่วน") {
			t.Fatalf("the check the package worded for a person stays: %s", b)
		}
	}
}
