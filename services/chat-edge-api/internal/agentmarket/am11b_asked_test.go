package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM11b E — an agent that does what it is asked.
//
// A model reads a person's words into the directives the agent's package declares. What it answers is
// not trusted: an id the package does not declare is not a directive, a request it says cannot be done
// is said under a reason of the package or the platform — never one the model wrote — and the person's
// words are quoted only when they are the person's words. These tests are the refusal cases: what a
// model must not be able to do by answering wrong.

var reportAsks = InstructionSpec{
	Can: []Directive{
		{ID: "no_charts", Label: "ไม่ใส่กราฟ", Note: "รายงานและสไลด์ไม่มีกราฟ"},
		{ID: "brief", Label: "รายงานฉบับย่อ", Asks: "ขอสั้นๆ · ขอหน้าเดียว"},
		{ID: "sort_by_variance", Label: "เรียงรายละเอียดรายบัญชีจากรายการที่ต่างมากที่สุด", Unless: "รอบนี้ไม่มีงบประมาณและไม่มีงวดก่อนให้เทียบ จึงไม่มีส่วนต่างให้เรียง"},
	},
	Cannot: []Directive{
		{ID: "by_department", Label: "แยกตามแผนก", Why: "รายงานนี้คำนวณตัวเลขรายบัญชีจากไฟล์ที่ส่งมา ยังไม่มีตัวเลขรายแผนก"},
	},
}

func TestAM11bAModelsAnswerHoldsOnlyWhatThePackageDeclares(t *testing.T) {
	const typed = "ปิดงบเดือนกันยายน ไม่ต้องใส่กราฟ  แยกตามแผนกด้วย แล้วแก้กำไรเป็น 5 ล้าน"
	// in the model's order, with an id nobody declared, one twice, a made-up quote, and a "cannot" for what it also applies
	raw := json.RawMessage(`{"apply": ["sort_by_variance", "no_charts", "no_charts", "delete_the_ledger", " brief "],
		"cannot": [{"id": "by_department", "words": "แยกตามแผนกด้วย"}, {"id": "change_figures", "words": "แก้กำไรเป็น 5 ล้าน"},
		           {"id": "skip_checks", "words": "ไม่ต้องตรวจภาษี"}, {"id": "invented_reason", "words": "ไม่ต้องใส่กราฟ"},
		           {"id": "no_charts", "words": "ไม่ต้องใส่กราฟ"}, {"id": "by_department", "words": "แยกตามแผนกด้วย"}]}`)
	got := ReadAsked(reportAsks, typed, raw)
	if strings.Join(got.Apply, " ") != "no_charts brief sort_by_variance" {
		t.Fatalf("the directives, each once, in the package's order, and none it does not declare: %v", got.Apply)
	}
	want := []AskedCannot{{ID: "by_department", Words: "แยกตามแผนกด้วย"}, {ID: "change_figures", Words: "แก้กำไรเป็น 5 ล้าน"},
		{ID: "skip_checks", Words: ""}} // "ไม่ต้องตรวจภาษี" was not typed: the reason stays, the quote goes
	if len(got.Cannot) != len(want) {
		t.Fatalf("what cannot be done: %+v", got.Cannot)
	}
	for i, c := range want {
		if got.Cannot[i] != c {
			t.Fatalf("cannot[%d]: %+v, want %+v", i, got.Cannot[i], c)
		}
	}
	if got.Unread || got.Nothing() {
		t.Fatalf("it was read, and asked something: %+v", got)
	}
	// an answer that is not the shape is words nothing read — never a guess
	for _, bad := range []string{`ไม่ต้องใส่กราฟ`, `["no_charts"]`, ``} {
		if r := ReadAsked(reportAsks, typed, json.RawMessage(bad)); !r.Unread || len(r.Apply) != 0 {
			t.Fatalf("%q was taken for an answer: %+v", bad, r)
		}
	}
	// a model sets a person's words as print does ("สั้น ๆ"): they are still the person's words
	if r := ReadAsked(reportAsks, "ขอสั้นๆ แล้วใส่โลโก้ทุกหน้า", json.RawMessage(`{"apply": ["brief"], "cannot": [{"id": "other", "words": "ใส่โลโก้ ทุกหน้า"}]}`)); len(r.Cannot) != 1 || r.Cannot[0].Words != "ใส่โลโก้ ทุกหน้า" {
		t.Fatalf("a quote that differs only in its spaces was dropped: %+v", r)
	}
	// words that ask nothing of the documents
	if r := ReadAsked(reportAsks, "ปิดงบเดือนกันยายน", json.RawMessage(`{"apply": [], "cannot": []}`)); !r.Nothing() {
		t.Fatalf("nothing was asked: %+v", r)
	}
}

func TestAM11bTheQuestionGivesAModelNothingToEchoAndOnlyTheTwoLists(t *testing.T) {
	system, user := AskedPrompt(reportAsks, "ไม่ต้องใส่กราฟ")
	// AM12: the platform takes out of an answer whatever repeats the system message — the shape of the
	// answer is therefore asked for in the user turn, and the system message holds no JSON at all
	if strings.ContainsAny(system, "{}[]") {
		t.Fatalf("the system message holds JSON an answer would repeat: %q", system)
	}
	// (a directive is shown with how people ask for it when the package says, else with what it does)
	for _, must := range []string{"no_charts: ไม่ใส่กราฟ — รายงานและสไลด์ไม่มีกราฟ", "brief: รายงานฉบับย่อ (คนมักขอว่า: ขอสั้นๆ · ขอหน้าเดียว)", "by_department: แยกตามแผนก",
		"change_figures: ", "skip_checks: ", "change_rules: ", "<<<\nไม่ต้องใส่กราฟ\n>>>", `{"apply": ["id"], "cannot": [{"id": "id", "words": "คำของผู้ใช้"}], "standing": ["id"], "ask": [{"ids": ["id", "id"], "words": "คำของผู้ใช้"}]}`} { // (the shape grew in AM18)
		if !strings.Contains(user, must) {
			t.Fatalf("the user turn lacks %q:\n%s", must, user)
		}
	}
	// a reason is never in the question: a model picks an id, the reason is the package's or the platform's
	if strings.Contains(user, reportAsks.Cannot[0].Why) || strings.Contains(user, NeverAsked()[0].Why.In(Thai)) {
		t.Fatalf("a reason was handed to the model to rewrite:\n%s", user)
	}
}

func TestAM11bWhatWasDoneAndWhatWasNotIsSaidWithItsReason(t *testing.T) {
	done := AskedDone(reportAsks, []string{"no_charts", "brief", "unknown"})
	if got := done.In(Thai); got != "ทำตามที่ขอแล้ว: ไม่ใส่กราฟ — รายงานและสไลด์ไม่มีกราฟ · รายงานฉบับย่อ" {
		t.Fatalf("done: %q", got)
	}
	if !AskedDone(reportAsks, nil).EmptyIn(Thai) || !AskedDone(reportAsks, []string{"unknown"}).EmptyIn(Thai) {
		t.Fatal("nothing done is nothing said")
	}
	lines := AskedNotDone(reportAsks, []string{"sort_by_variance"}, []AskedCannot{
		{ID: "by_department", Words: "แยกตามแผนกด้วย"}, {ID: "change_figures"}, {ID: AskedOther, Words: "ใส่โลโก้ลูกค้าทุกหน้า"}, {ID: AskedOther}})
	want := []string{
		"“เรียงรายละเอียดรายบัญชีจากรายการที่ต่างมากที่สุด” ทำตามที่ขอไม่ได้เพราะรอบนี้ไม่มีงบประมาณและไม่มีงวดก่อนให้เทียบ จึงไม่มีส่วนต่างให้เรียง",
		"“แยกตามแผนกด้วย” ทำตามที่ขอไม่ได้เพราะรายงานนี้คำนวณตัวเลขรายบัญชีจากไฟล์ที่ส่งมา ยังไม่มีตัวเลขรายแผนก",
		"“แก้หรือกำหนดตัวเลขเอง” ทำตามที่ขอไม่ได้เพราะตัวเลขทุกตัวมาจากการคำนวณจากไฟล์ที่ส่งมา agent ไม่เปลี่ยนตัวเลขตามคำขอ",
		"“ใส่โลโก้ลูกค้าทุกหน้า” ทำตามที่ขอไม่ได้เพราะ agent นี้ปรับเอกสารตามคำขอได้เฉพาะ: ไม่ใส่กราฟ · รายงานฉบับย่อ · เรียงรายละเอียดรายบัญชีจากรายการที่ต่างมากที่สุด",
		"มีคำขอที่ทำตามไม่ได้เพราะ agent นี้ปรับเอกสารตามคำขอได้เฉพาะ: ไม่ใส่กราฟ · รายงานฉบับย่อ · เรียงรายละเอียดรายบัญชีจากรายการที่ต่างมากที่สุด",
	}
	if len(lines) != len(want) {
		t.Fatalf("%d lines, want %d", len(lines), len(want))
	}
	for i, w := range want {
		if got := lines[i].In(Thai); got != w {
			t.Fatalf("line %d:\n got %q\nwant %q", i, got, w)
		}
		// every refusal is the card's own words: "ทำตามที่ขอไม่ได้เพราะ…" / "มีคำขอที่ทำตามไม่ได้เพราะ…"
		if !strings.Contains(lines[i].In(Thai), "ไม่ได้เพราะ") {
			t.Fatalf("line %d gives no reason: %q", i, lines[i].In(Thai))
		}
	}
	// on an English screen: the platform's sentences are English; a person's own words stay theirs
	en := lines[2].In(English)
	if en != "“changing a figure or setting one by hand” cannot be done as asked because every figure is computed from the files sent, and the agent does not change a figure because it is asked to" {
		t.Fatalf("in English: %q", en)
	}
	if got := AskedUnread().In(English); !strings.HasPrefix(got, "The words typed with this run could not be read") {
		t.Fatalf("unread, in English: %q", got)
	}
}

func TestAM11bAManifestThatDeclaresNothingTakesNoInstructions(t *testing.T) {
	var m Manifest
	if m.TakesInstructions() {
		t.Fatal("no instructions block")
	}
	m.Instructions = &InstructionSpec{Cannot: reportAsks.Cannot}
	if m.TakesInstructions() {
		t.Fatal("nothing can be asked: the agent does not claim to listen")
	}
	m = Manifest{} // (a fresh one: decoding into the block above would write into the list it was given)
	if err := json.Unmarshal([]byte(`{"instructions": {"can": [{"id": "no_charts", "label": "ไม่ใส่กราฟ", "note": "n", "unless": "u"}], "cannot": [{"id": "x", "label": "l", "why": "w"}]}}`), &m); err != nil {
		t.Fatal(err)
	}
	if !m.TakesInstructions() || m.Instructions.Can[0].Unless != "u" || m.Instructions.Cannot[0].Why != "w" {
		t.Fatalf("the manifest's block: %+v", m.Instructions)
	}
}

// The directives a round's documents are made with now: what the round started with, then what the
// latest finished re-render that carried any was made with. A re-render that failed, one of another
// round, and one that carried none (another format) do not change them.
func TestAM11bTheDirectivesInForceFollowTheRoundsLatestFinishedRender(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run := e.finishedRun(in, "2026-09", "")
	other := e.finishedRun(in, "2026-08", "")
	if got := e.store.InstructionsInForce(ctx, run.ID); len(got) != 0 {
		t.Fatalf("a round nobody asked anything of: %v", got)
	}
	step := func(id uuid.UUID, status, output string) {
		t.Helper()
		if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_run_steps (run_id, seq, name, kind, idempotency_key, status, output)
			VALUES ($1, (SELECT COALESCE(MAX(seq), 0) + 1 FROM agent_market.agent_run_steps WHERE run_id = $1), 'instructions', 'ai', $2, $3, $4::jsonb)`,
			id, uuid.NewString(), status, output); err != nil {
			t.Fatal(err)
		}
	}
	step(run.ID, "failed", `{"apply": ["brief"]}`)
	step(run.ID, "succeeded", `{"apply": ["no_charts"], "cannot": [{"id": "by_department"}]}`)
	if got := e.store.InstructionsInForce(ctx, run.ID); strings.Join(got, " ") != "no_charts" {
		t.Fatalf("what the round started with: %v", got)
	}
	render := func(parent uuid.UUID, status, params string) {
		t.Helper()
		pid := parent
		job, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, ParentRunID: &pid, TenantID: in.TenantID, UserID: in.UserID,
			Kind: KindRender, Params: json.RawMessage(params), DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = $2 WHERE id = $1`, job.ID, status); err != nil {
			t.Fatal(err)
		}
	}
	render(run.ID, "succeeded", `{"formats": ["docx"], "instructions": ["no_charts", "sort_by_variance"]}`)
	render(run.ID, "failed", `{"formats": ["docx"], "instructions": ["brief"]}`)
	render(run.ID, "succeeded", `{"formats": ["xlsx"], "why": "message"}`)
	render(other.ID, "succeeded", `{"formats": ["docx"], "instructions": ["brief"]}`)
	if got := e.store.InstructionsInForce(ctx, run.ID); strings.Join(got, " ") != "no_charts sort_by_variance" {
		t.Fatalf("after a finished re-render: %v", got)
	}
	if got := e.store.InstructionsInForce(ctx, other.ID); strings.Join(got, " ") != "brief" {
		t.Fatalf("the other round's are its own: %v", got)
	}
}
