package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM5 phase C — nothing technical reaches a person: not from a row written
// before this rule existed, not in a message over HTTP; and a failed round
// is not a dead end — it can be tried again with the files it had.

// legacyItem writes a thread item the way a runner before AM5 did: straight
// into the table, with whatever text it had.
func (e *testEnv) legacyItem(in Install, run Run, kind string, payload map[string]any) {
	e.t.Helper()
	b, _ := json.Marshal(payload)
	if _, err := e.store.Pool.Exec(context.Background(), `INSERT INTO agent_market.agent_thread_items
		(install_id, tenant_id, user_id, run_id, kind, payload) VALUES ($1, $2, $3, $4, $5, $6)`,
		in.ID, in.TenantID, in.UserID, run.ID, kind, b); err != nil {
		e.t.Fatal(err)
	}
}

func (e *testEnv) failedRun(in Install, reason string, files ...string) Run {
	e.t.Helper()
	ctx := context.Background()
	r, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		Message: "ปิดงบ ก.ย. 69", DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum, EngineChecksum: "engsum",
		Answers: json.RawMessage(`{"period_mixed":{"value":"exclude","label":"ตัดออก","moment":"preflight","via":"card","code":"period_mixed"}}`)}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for _, name := range files {
		rid := r.ID
		if _, err := e.store.CreateFile(ctx, e.store.Pool, File{ID: uuid.New(), TenantID: in.TenantID, UserID: in.UserID, InstallID: in.ID,
			RunID: &rid, Name: name, Size: 10, SHA256: name, StorageKey: "agent-market/" + name, Status: "active"}); err != nil {
			e.t.Fatal(err)
		}
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'failed', status_reason = $2, period = '2026-09',
		started_at = now() - interval '1 minute', finished_at = now() WHERE id = $1`, r.ID, reason); err != nil {
		e.t.Fatal(err)
	}
	r, _ = e.store.RunByID(ctx, r.ID)
	return r
}

func TestTheThreadNeverServesTechnicalTextWrittenBeforeAM5(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	run := e.failedRun(in, "render_failed", "GL_2569-09.xlsx")
	// the owner's two failed rounds of 28 Sep, as they sit in the live table
	e.legacyItem(in, run, "agent_text", map[string]any{"run_id": run.ID.String(), "tone": "error",
		"text": "รอบนี้ทำไม่สำเร็จครับ (engine step failed: TypeError: 'NoneType' object is not iterable) — ส่งไฟล์ใหม่หรือพิมพ์ “ทำรอบใหม่” ไฟล์ที่ส่งมาและผลงานของรอบก่อน ๆ ยังอยู่ครบ"})
	e.legacyItem(in, run, "progress", map[string]any{"run_id": run.ID.String(), "kind": "run", "status": "failed", "steps": []map[string]any{
		{"key": "parse", "label": "อ่านไฟล์ 1 ไฟล์", "status": "done", "detail": "GL_2569-09.xlsx"},
		{"key": "quality", "label": "ตรวจคุณภาพเอกสาร", "status": "failed", "detail": "engine step failed: TypeError: 'NoneType' object is not iterable"}}})
	e.legacyItem(in, run, "outputs", map[string]any{"run_id": run.ID.String(), "artifacts": []map[string]any{{"id": uuid.NewString(), "name": "a.xlsx",
		"format": "template", "quality": map[string]any{"passed": true, "checks": []map[string]any{{"name": "template.recalculated", "ok": true,
			"label":  "คำนวณสูตรในแบบฟอร์มใหม่",
			"detail": "คำนวณล่วงหน้าไม่สำเร็จ (converter: Post \"http://doc-converter:8080/convert\": dial tcp 172.20.0.3:8080: connect: connection refused) Excel จะคำนวณเมื่อเปิดไฟล์"}}}}}})
	e.legacyItem(in, run, "notice", map[string]any{"run_id": run.ID.String(), "text": "อัปเกรดไม่สำเร็จ: ERROR: deadlock detected (SQLSTATE 40P01)"})
	var all []string
	for _, it := range e.threadItems(in, "") {
		b, _ := json.Marshal(it["payload"])
		all = append(all, string(b))
	}
	joined := strings.Join(all, "\n")
	for _, bad := range []string{"TypeError", "NoneType", "not iterable", "engine step failed", "dial tcp", "connection refused", "doc-converter", "SQLSTATE", "deadlock"} {
		if strings.Contains(joined, bad) {
			t.Fatalf("the thread serves %q:\n%s", bad, joined)
		}
	}
	for _, kept := range []string{"รอบนี้ทำไม่สำเร็จครับ", "ไฟล์ที่ส่งมาและผลงานของรอบก่อน ๆ ยังอยู่ครบ", "GL_2569-09.xlsx", "ตรวจคุณภาพเอกสาร",
		"Excel จะคำนวณเมื่อเปิดไฟล์", "คำนวณสูตรในแบบฟอร์มใหม่"} {
		if !strings.Contains(joined, kept) {
			t.Fatalf("what a person can read stays (%q):\n%s", kept, joined)
		}
	}
	// what the person typed is theirs, whatever it says
	e.legacyItem(in, run, "user_text", map[string]any{"text": "ทำไมขึ้น TypeError คะ"})
	found := false
	for _, it := range e.threadItems(in, "") {
		if it["kind"] == "user_text" {
			found = it["payload"].(map[string]any)["text"] == "ทำไมขึ้น TypeError คะ"
		}
	}
	if !found {
		t.Fatal("a person's own message is never rewritten")
	}
}

func TestMessagesOverHTTPNameNoIdentifier(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	e.seedDefinition()
	e.heartbeat(map[string]Binding{"spreadsheet.read.xlsx": {Capability: "spreadsheet.read.xlsx", Impl: "sandbox", Version: "x"}})
	code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"})
	msg := body["error"].(map[string]any)["message"].(string)
	if code != http.StatusConflict || strings.Contains(msg, "deliverable.report.docx") || !strings.Contains(msg, "รายงาน Word") {
		t.Fatalf("a missing part of the system is named in words: %d %q", code, msg)
	}
	// every message of the table reads as a person's sentence
	for code := range userMessages {
		if m := Message(code, "detail", "รายละเอียด", "code", "X", "what", "รายการ"); technicalText(m) {
			t.Fatalf("%s reads technical: %q", code, m)
		}
	}
	// and one that was handed an error's text does not carry it
	rec := httptest.NewRecorder()
	say(rec, http.StatusConflict, "BAD_ANSWER", "detail", "e2_check_failed:doc1:vat_7_percent: one answer expected (json: cannot unmarshal)")
	if s := rec.Body.String(); strings.Contains(s, "cannot unmarshal") || strings.Contains(s, "e2_check_failed") {
		t.Fatalf("the last line of defence: %s", s)
	}
}

// technicalText is the test's own idea of what a person cannot read (it
// does not lean on the guard it is testing).
func technicalText(s string) bool {
	for _, bad := range []string{"Error", "Exception", "Traceback", "panic", "object", "unmarshal", "dial tcp", "SQLSTATE", "_failed", "err=", ".go", ".py"} {
		if strings.Contains(s, bad) {
			return true
		}
	}
	return false
}

func TestAFailedRoundCanBeTriedAgainWithItsFiles(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	failed := e.failedRun(in, "render_failed", "GL_2569-09.xlsx", "งบประมาณ_2569.xlsx")
	code, body := e.do("POST", "/runs/"+failed.ID.String()+"/retry", nil)
	if code != 200 {
		t.Fatalf("retry: %d %v", code, body)
	}
	id := uuid.MustParse(body["run"].(map[string]any)["id"].(string))
	if id == failed.ID {
		t.Fatal("a retry is a new round: the failed one stays as it ended")
	}
	run, err := e.store.RunByID(ctx, id)
	if err != nil || run.Status != RunQueued || run.Kind != KindRun || run.Message != failed.Message {
		t.Fatalf("queued, with the same instruction: %+v %v", run, err)
	}
	files, _ := e.store.RunFiles(ctx, id, "active")
	if len(files) != 2 {
		t.Fatalf("the same files: %v", files)
	}
	var answers map[string]Answer
	_ = json.Unmarshal(run.Answers, &answers)
	if string(answers["period_mixed"].Value) != `"exclude"` {
		t.Fatalf("what the person already answered is not asked again: %s", run.Answers)
	}
	if old, _ := e.store.RunByID(ctx, failed.ID); old.Status != RunFailed {
		t.Fatalf("the failed round is still failed: %v", old.Status)
	}
	if item, ok := body["item"].(map[string]any); !ok || item["kind"] != "progress" {
		t.Fatalf("the new round's progress card comes back for the thread: %v", body["item"])
	}
	// a second click is the same round, not a third
	code, again := e.do("POST", "/runs/"+failed.ID.String()+"/retry", nil)
	if code != 200 || again["run"].(map[string]any)["id"] != id.String() {
		t.Fatalf("idempotent: %d %v", code, again)
	}
	// a round that did not fail has nothing to retry; nor has someone else's
	ok := e.finishedRun(in, "2026-08", "")
	code, body = e.do("POST", "/runs/"+ok.ID.String()+"/retry", nil)
	if code != http.StatusConflict || technicalText(body["error"].(map[string]any)["message"].(string)) {
		t.Fatalf("not failed: %d %v", code, body)
	}
	other := e.failedRun(in, "compute_failed", "a.xlsx")
	if code, _ := e.do("POST", "/runs/"+other.ID.String()+"/retry", nil, "X-Test-User", uuid.NewString()); code != http.StatusNotFound {
		t.Fatalf("someone else's round: %d", code)
	}
	// the files of a failed round are gone after their keeping time: say so
	gone := e.failedRun(in, "parse_failed")
	code, body = e.do("POST", "/runs/"+gone.ID.String()+"/retry", nil)
	if msg := body["error"].(map[string]any)["message"].(string); code != http.StatusConflict || !strings.Contains(msg, "ส่งไฟล์") {
		t.Fatalf("nothing to retry with: %d %q", code, msg)
	}
}
