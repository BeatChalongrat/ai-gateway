package runner

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM11b E — an agent that does what it is asked, end to end: the real engine in the real sandbox, the
// report agent's own package, and a stand-in for the model that reads a person's words (askedAnswer).
//
// A person types what they want with the files; the documents are made that way and the thread says so.
// What cannot be done is said with its reason. After the round, words about its documents make the
// files again from the same figures — no compute step — and a request to change a figure changes
// nothing and is refused in words.

// askedAnswer is the stand-in model's reading of a person's words (the user turn of am.AskedPrompt):
// it answers with ids of the two lists by the words it finds, the way a model would.
func (e *itEnv) askedAnswer(body []byte) string {
	var req struct {
		Messages []struct {
			Role    string `json:"role"`
			Content any    `json:"content"`
		} `json:"messages"`
	}
	_ = json.Unmarshal(body, &req)
	typed := ""
	for _, m := range req.Messages {
		s, _ := m.Content.(string)
		if a, b := strings.Index(s, "<<<\n"), strings.LastIndex(s, "\n>>>"); m.Role == "user" && a >= 0 && b > a {
			typed = s[a+4 : b]
		}
	}
	word := func(part string) string { // the person's own word that holds part
		for _, w := range strings.Fields(typed) {
			if strings.Contains(w, part) {
				return w
			}
		}
		return ""
	}
	apply := []string{}
	cannot := []map[string]string{}
	if strings.Contains(typed, "กราฟ") {
		apply = append(apply, "no_charts")
	}
	if strings.Contains(typed, "สั้น") {
		apply = append(apply, "brief")
	}
	if strings.Contains(typed, "ต่างมากที่สุด") {
		apply = append(apply, "sort_by_variance")
	}
	if w := word("แผนก"); w != "" {
		cannot = append(cannot, map[string]string{"id": "by_department", "words": w})
	}
	if w := word("แก้กำไร"); w != "" {
		cannot = append(cannot, map[string]string{"id": "change_figures", "words": w})
	}
	if strings.Contains(typed, "ยอดสูงสุด") {
		apply = append(apply, "sort_by_amount")
	}
	if w := word("เฉพาะ"); w != "" {
		cannot = append(cannot, map[string]string{"id": "only_exceptions", "words": w})
	}
	// AM13: the orders of the other four agents, in an order that is not their package's
	if strings.Contains(typed, "ตามวันที่") {
		apply = append(apply, "sort_by_date")
	}
	if strings.Contains(typed, "ยอดมาก") {
		apply = append(apply, "sort_by_amount")
	}
	if strings.Contains(typed, "ตามผู้ขาย") {
		apply = append(apply, "sort_by_seller")
	}
	if strings.Contains(typed, "ถูกสุด") {
		apply = append(apply, "sort_by_price")
	}
	if w := word("รายการเล็ก"); w != "" {
		cannot = append(cannot, map[string]string{"id": "drop_small", "words": w})
	}
	if w := word("ดีที่สุด"); w != "" {
		cannot = append(cannot, map[string]string{"id": "pick_best", "words": w})
	}
	if w := word("งบไม่เกิน"); w != "" { // (a model that takes a request's budget for a request about the document)
		cannot = append(cannot, map[string]string{"id": "other", "words": w})
	}
	b, _ := json.Marshal(map[string]any{"apply": apply, "cannot": cannot})
	return string(b)
}

// docxOf reads a finished .docx of the run out of storage: its headings' text and how many pictures it holds.
func (e *itEnv) docxOf(run uuid.UUID, version int) (text string, pictures int, quality string) {
	e.t.Helper()
	ctx := context.Background()
	arts, err := e.store.RunArtifacts(ctx, run)
	if err != nil {
		e.t.Fatal(err)
	}
	for _, a := range arts {
		if a.Format != "docx" || a.Version != version {
			continue
		}
		body, _, _, err := e.files.Download(ctx, a.StorageKey)
		if err != nil {
			e.t.Fatal(err)
		}
		data, _ := io.ReadAll(body)
		body.Close()
		zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if err != nil {
			e.t.Fatal(err)
		}
		for _, f := range zr.File {
			if strings.HasPrefix(f.Name, "word/media/") {
				pictures++
			}
			if f.Name == "word/document.xml" {
				r, _ := f.Open()
				x, _ := io.ReadAll(r)
				r.Close()
				text = string(x)
			}
		}
		return text, pictures, string(a.Validation)
	}
	e.t.Fatalf("the run has no .docx v%d", version)
	return "", 0, ""
}

func (e *itEnv) say(parent am.Run, text string) am.Run {
	e.t.Helper()
	params, _ := json.Marshal(map[string]any{"text": text})
	pid := parent.ID
	job, _, err := e.store.CreateRun(context.Background(), e.store.Pool, am.Run{InstallID: parent.InstallID, ParentRunID: &pid,
		TenantID: parent.TenantID, UserID: parent.UserID, Kind: am.KindReply, Message: text, Params: params,
		DefinitionVersion: parent.DefinitionVersion, Checksum: parent.Checksum, BindingVersion: parent.BindingVersion}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	return job
}

func (e *itEnv) renderJobs(parent uuid.UUID) []am.Run {
	e.t.Helper()
	rows, err := e.store.Pool.Query(context.Background(), `SELECT id FROM agent_market.agent_runs
		WHERE parent_run_id = $1 AND kind = 'render' ORDER BY created_at`, parent)
	if err != nil {
		e.t.Fatal(err)
	}
	var ids []uuid.UUID
	for rows.Next() {
		var id uuid.UUID
		_ = rows.Scan(&id)
		ids = append(ids, id)
	}
	rows.Close()
	var out []am.Run
	for _, id := range ids {
		r, err := e.store.RunByID(context.Background(), id)
		if err != nil {
			e.t.Fatal(err)
		}
		out = append(out, r)
	}
	return out
}

func has(lines []string, part string) bool {
	for _, l := range lines {
		if strings.Contains(l, part) {
			return true
		}
	}
	return false
}

func TestARoundDoesWhatItWasAskedAndSaysWhatItCouldNot(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.setupInstall(3_000_000)
	r := e.runner("r1")
	run := e.newRun("ปิดงบเดือนกันยายน ไม่ต้องใส่กราฟ แยกตามแผนกด้วย",
		"sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx", "sample-data/งบประมาณ_2569.xlsx")
	got := e.drive(r, run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the round: %s %s", got.Status, got.StatusReason)
	}
	// the reading is a step of the run, with what was read
	var asked am.Asked
	n := 0
	for _, s := range e.steps(run.ID) {
		if s.Name == "instructions" {
			n++
			if s.Status != "succeeded" || s.Kind != "ai" {
				t.Fatalf("the step: %+v", s)
			}
			_ = json.Unmarshal(s.Output, &asked)
		}
	}
	if n != 1 || strings.Join(asked.Apply, " ") != "no_charts" || len(asked.Cannot) != 1 || asked.Cannot[0].ID != "by_department" ||
		asked.Cannot[0].Words != "แยกตามแผนกด้วย" || asked.Model != "fake-self-hosted" {
		t.Fatalf("what was read: %d step(s) %+v", n, asked)
	}
	// the file: no chart section, no picture — and the gate read that back
	doc, pictures, quality := e.docxOf(run.ID, 1)
	if pictures != 0 || strings.Contains(doc, "แนวโน้มและองค์ประกอบ") || !strings.Contains(doc, "รายละเอียดรายบัญชี") {
		t.Fatalf("the report still has its charts: %d pictures", pictures)
	}
	if !strings.Contains(quality, `"docx.asked"`) || !strings.Contains(quality, "เอกสารเป็นไปตามที่ขอ") {
		t.Fatalf("the gate did not read back what was asked: %s", quality)
	}
	// the thread: what was done as asked, and what could not be, with its reason
	thread := e.visibleThread()
	if !has(thread, "ทำตามที่ขอแล้ว: ไม่ใส่กราฟ — รายงานและสไลด์ไม่มีกราฟ") {
		t.Fatalf("the thread does not say what was done:\n%s", strings.Join(thread, "\n"))
	}
	if !has(thread, "“แยกตามแผนกด้วย” ทำตามที่ขอไม่ได้เพราะรายงานนี้คำนวณตัวเลขรายบัญชีจากไฟล์ที่ส่งมา ยังไม่มีตัวเลขรายแผนก") {
		t.Fatalf("the thread does not say what could not be done, and why:\n%s", strings.Join(thread, "\n"))
	}

	// after the round: words about its documents — the files are made again from the same figures
	job := e.say(got, "เรียงจากรายการที่ต่างมากที่สุด")
	if j := e.drive(r, job.ID, 3*time.Minute, terminal); j.Status != am.RunSucceeded {
		t.Fatalf("the reply: %s %s", j.Status, j.StatusReason)
	}
	jobs := e.renderJobs(run.ID)
	if len(jobs) != 1 {
		t.Fatalf("one render job, got %d", len(jobs))
	}
	if j := e.drive(r, jobs[0].ID, 4*time.Minute, terminal); j.Status != am.RunSucceeded {
		t.Fatalf("the files made again: %s %s", j.Status, j.StatusReason)
	}
	if c := countSteps(e.steps(run.ID), "compute"); c != 1 {
		t.Fatalf("nothing is computed again: %d compute steps on the round", c)
	}
	if c := countSteps(e.steps(jobs[0].ID), "compute"); c != 0 {
		t.Fatalf("the render job computed: %d", c)
	}
	// version 2 is made with both: what the round started with is still in force
	if in := e.store.InstructionsInForce(ctx, run.ID); strings.Join(in, " ") != "no_charts sort_by_variance" {
		t.Fatalf("in force: %v", in)
	}
	_, pictures, quality = e.docxOf(run.ID, 2)
	if pictures != 0 || !strings.Contains(quality, "เรียงจากส่วนต่างจากงบประมาณมากไปน้อย") {
		t.Fatalf("version 2: %d pictures · %s", pictures, quality)
	}
	thread = e.visibleThread()
	if !has(thread, "กำลังทำไฟล์ใหม่ตามที่ขอ จากตัวเลขชุดเดิมของรอบนี้ ไม่คำนวณใหม่") ||
		!has(thread, "ทำตามที่ขอแล้ว: เรียงรายละเอียดรายบัญชีจากรายการที่ต่างมากที่สุด") {
		t.Fatalf("the thread after the second request:\n%s", strings.Join(thread, "\n"))
	}

	// a figure is not the person's to set: nothing is made, and the thread says why
	before, _ := e.store.RunArtifacts(ctx, run.ID)
	job = e.say(got, "แก้กำไรเป็น5ล้าน ให้หน่อย")
	if j := e.drive(r, job.ID, 3*time.Minute, terminal); j.Status != am.RunSucceeded {
		t.Fatalf("the reply: %s %s", j.Status, j.StatusReason)
	}
	after, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(e.renderJobs(run.ID)) != 1 || len(after) != len(before) {
		t.Fatalf("a request to change a figure made files: %d jobs, %d → %d artifacts", len(e.renderJobs(run.ID)), len(before), len(after))
	}
	if thread = e.visibleThread(); !has(thread, "“แก้กำไรเป็น5ล้าน” ทำตามที่ขอไม่ได้เพราะตัวเลขทุกตัวมาจากการคำนวณจากไฟล์ที่ส่งมา agent ไม่เปลี่ยนตัวเลขตามคำขอ") {
		t.Fatalf("the refusal:\n%s", strings.Join(thread, "\n"))
	}
	// words shaped like a question are a question about the round unless they ask for something the
	// agent can do: "แยกตามแผนกได้ไหม?" is answered as a question — never refused as a request
	refusals := func() int {
		n := 0
		for _, l := range e.visibleThread() {
			if strings.Contains(l, "ทำตามที่ขอไม่ได้เพราะ") {
				n++
			}
		}
		return n
	}
	was := refusals()
	job = e.say(got, "แยกตามแผนกได้ไหม?")
	if j := e.drive(r, job.ID, 3*time.Minute, terminal); j.Status != am.RunSucceeded {
		t.Fatalf("the reply to a question: %s %s", j.Status, j.StatusReason)
	}
	if refusals() != was || len(e.renderJobs(run.ID)) != 1 {
		t.Fatalf("a question was refused as a request: %d → %d refusals", was, refusals())
	}
	// and every figure of version 2 is version 1's: the P&L rows read the same in both files
	one, _, _ := e.docxOf(run.ID, 1)
	two, _, _ := e.docxOf(run.ID, 2)
	for _, figure := range []string{"12,480,300", "9,737,150"} {
		if !strings.Contains(one, figure) || !strings.Contains(two, figure) {
			t.Fatalf("the figure %s is not in both versions", figure)
		}
	}
}

// A round whose words ask nothing of the documents is as it always was: no line about a request, the same files.
func TestARoundNobodyAskedAnythingOfIsAsItWas(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx", "sample-data/งบประมาณ_2569.xlsx")
	got := e.drive(e.runner("r1"), run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the round: %s %s", got.Status, got.StatusReason)
	}
	_, pictures, quality := e.docxOf(run.ID, 1)
	if pictures != 3 || strings.Contains(quality, "docx.asked") {
		t.Fatalf("a round nobody asked anything of: %d pictures · %s", pictures, quality)
	}
	if thread := e.visibleThread(); has(thread, "ทำตามที่ขอ") {
		t.Fatalf("the thread speaks of a request nobody made:\n%s", strings.Join(thread, "\n"))
	}
}

// A second agent, and one that shows no model its documents: the expense audit. Its claims are put in
// the order asked, a request to leave claims out is refused with its reason, and the run still says —
// truthfully — that no model saw a receipt: the model that read the person's words saw only the words.
func TestTheExpenseAuditOrdersItsClaimsAsAskedAndNoModelSeesAReceipt(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupExpenseInstall()
	ctx := context.Background()
	files := []string{"claims_2569-10.xlsx", "policy_kb.docx"}
	receipts, _ := os.ReadDir(filepath.Join(e.root, filepath.FromSlash(expenseFixtures), "receipts"))
	for _, r := range receipts {
		files = append(files, "receipts/"+r.Name())
	}
	run := e.expenseRun(files...)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET message = $2 WHERE id = $1`, run.ID,
		"ตรวจการเบิกเดือนตุลาคม เรียงจากยอดสูงสุด ขอเฉพาะรายการที่มีปัญหา"); err != nil {
		t.Fatal(err)
	}
	r := e.runner("r1")
	if got := e.drive(r, run.ID, 6*time.Minute, func(x am.Run) bool { return parked(x) || terminal(x) }); got.Status != am.RunAwaitingInput {
		t.Fatalf("the judgement calls are asked: %s %s", got.Status, got.StatusReason)
	}
	e.answerWithDefaults(run.ID)
	got := e.drive(r, run.ID, 6*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("after the answers: %s %s", got.Status, got.StatusReason)
	}
	if got.ModelUsed != nil {
		t.Fatalf("the model that read the person's words is named as one that saw the round's data: %v", *got.ModelUsed)
	}
	var asked am.Asked
	for _, s := range e.steps(run.ID) {
		if s.Name == "instructions" {
			_ = json.Unmarshal(s.Output, &asked)
		}
	}
	if strings.Join(asked.Apply, " ") != "sort_by_amount" || len(asked.Cannot) != 1 || asked.Cannot[0].ID != "only_exceptions" {
		t.Fatalf("what was read: %+v", asked)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 1 || arts[0].Format != "xlsx" {
		t.Fatalf("one audit workbook: %+v", arts)
	}
	if q := string(arts[0].Validation); !strings.Contains(q, `"xlsx.asked"`) || !strings.Contains(q, "เรียงจากยอดสูงสุดลงไป") {
		t.Fatalf("the gate did not read the order back: %s", q)
	}
	thread := e.visibleThread()
	if !has(thread, "ทำตามที่ขอแล้ว: เรียงรายการเบิกจากยอดสูงสุด") {
		t.Fatalf("what was done:\n%s", strings.Join(thread, "\n"))
	}
	if !has(thread, "“ขอเฉพาะรายการที่มีปัญหา” ทำตามที่ขอไม่ได้เพราะทุกรายการเบิกต้องอยู่ในผลตรวจครบ") {
		t.Fatalf("what was not, and why:\n%s", strings.Join(thread, "\n"))
	}
}
