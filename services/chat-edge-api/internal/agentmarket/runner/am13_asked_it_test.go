package runner

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM13 — the other four agents do what they are asked: an order for their table, and nothing else.
//
// Two real rounds, each with its agent's own package and a stand-in for the model that reads a person's
// words (askedAnswer). They are the two rules the platform holds for every such agent:
//
//   - a table has one order: asked for two in the same words, the round makes its documents with one
//     and says, by name, that the other was not done; asked for another afterwards, the new order takes
//     the place of the one in force — the renderer is never handed two;
//   - an agent that starts from what a person types is typed a request. A model that takes the budget
//     for a request about the document is not believed: nothing of the request is ever answered with
//     "cannot be done as asked". Asked which option is best, the agent says it does not judge that.

func refusalsIn(lines []string) int {
	n := 0
	for _, l := range lines {
		if strings.Contains(l, "ทำตามที่ขอไม่ได้เพราะ") {
			n++
		}
	}
	return n
}

func TestTheBankReconciliationTakesOneOrderAndALaterOneTakesItsPlace(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	ctx := context.Background()
	run := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET message = $2 WHERE id = $1`, run.ID,
		"กระทบยอดเดือนกันยายน เรียงตามวันที่ และเรียงจากยอดมาก ด้วย ตัดรายการเล็กออก"); err != nil {
		t.Fatal(err)
	}
	r := e.runner("r1")
	got := e.drive(r, run.ID, 6*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the round: %s %s", got.Status, got.StatusReason)
	}
	if got.ModelUsed != nil {
		t.Fatalf("the model that read the person's words is named as one that saw the statement: %v", *got.ModelUsed)
	}
	var asked am.Asked
	for _, s := range e.steps(run.ID) {
		if s.Name == "instructions" {
			_ = json.Unmarshal(s.Output, &asked)
		}
	}
	if strings.Join(asked.Apply, " ") != "sort_by_amount" || len(asked.Instead) != 1 ||
		asked.Instead[0] != (am.AskedInstead{ID: "sort_by_date", Kept: "sort_by_amount"}) {
		t.Fatalf("two orders were asked; one is applied and the other is known to have given way: %+v", asked)
	}
	if len(asked.Cannot) != 1 || asked.Cannot[0].ID != "drop_small" {
		t.Fatalf("what cannot be done: %+v", asked.Cannot)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 1 || arts[0].Format != "xlsx" {
		t.Fatalf("one working paper: %+v", arts)
	}
	if q := string(arts[0].Validation); !strings.Contains(q, `"xlsx.asked"`) || !strings.Contains(q, "เรียงจากจำนวนเงินมากที่สุดลงไป") {
		t.Fatalf("the gate did not read the order back out of the file: %s", q)
	}
	thread := e.visibleThread()
	for _, want := range []string{
		"ทำตามที่ขอแล้ว: เรียงรายการกระทบยอดจากจำนวนเงินมากที่สุด",
		"“เรียงรายการกระทบยอดตามวันที่” ทำตามที่ขอไม่ได้เพราะขอ “เรียงรายการกระทบยอดจากจำนวนเงินมากที่สุด” มาด้วย และทำได้ทีละอย่าง",
		"ทำตามที่ขอไม่ได้เพราะรายการกระทบยอดทุกรายการต้องอยู่ครบ",
	} {
		if !has(thread, want) {
			t.Fatalf("the thread does not say %q:\n%s", want, strings.Join(thread, "\n"))
		}
	}
	// the statement is the statement: the two adjusted balances are the clean month's
	sum := e.outputsOf(run.ID)[0]["summary"].(map[string]any)
	if sum["tied"] != true || sum["adjusted_bank"] != "1538519.65" || sum["adjusted_book"] != "1538519.65" {
		t.Fatalf("an order moved the statement: %v", sum)
	}

	// afterwards: by date. The new order takes the place of the one in force — the renderer gets one.
	was := refusalsIn(thread)
	job := e.say(got, "เรียงตามวันที่ แทน")
	if j := e.drive(r, job.ID, 3*time.Minute, terminal); j.Status != am.RunSucceeded {
		t.Fatalf("the reply: %s %s", j.Status, j.StatusReason)
	}
	jobs := e.renderJobs(run.ID)
	if len(jobs) != 1 {
		t.Fatalf("one render job: %d", len(jobs))
	}
	var p struct {
		Instructions []string `json:"instructions"`
	}
	_ = json.Unmarshal(jobs[0].Params, &p)
	if strings.Join(p.Instructions, " ") != "sort_by_date" {
		t.Fatalf("the file is made again with the one order now asked: %v", p.Instructions)
	}
	if j := e.drive(r, jobs[0].ID, 4*time.Minute, terminal); j.Status != am.RunSucceeded {
		t.Fatalf("the render job: %s %s", j.Status, j.StatusReason)
	}
	if c := countSteps(e.steps(jobs[0].ID), "compute"); c != 0 {
		t.Fatalf("the render job computed: %d", c)
	}
	if in := e.store.InstructionsInForce(ctx, run.ID); strings.Join(in, " ") != "sort_by_date" {
		t.Fatalf("in force: %v", in)
	}
	arts, _ = e.store.RunArtifacts(ctx, run.ID)
	last := arts[len(arts)-1]
	for _, a := range arts {
		if a.Version > last.Version {
			last = a
		}
	}
	if q := string(last.Validation); last.Version != 2 || !strings.Contains(q, "เรียงจากวันที่เก่าที่สุดขึ้นก่อน") || strings.Contains(q, "เรียงจากจำนวนเงินมากที่สุดลงไป") {
		t.Fatalf("version 2 is by date, read back from the file: v%d %s", last.Version, q)
	}
	thread = e.visibleThread()
	if !has(thread, "ทำตามที่ขอแล้ว: เรียงรายการกระทบยอดตามวันที่") {
		t.Fatalf("what was done the second time:\n%s", strings.Join(thread, "\n"))
	}
	if now := refusalsIn(thread); now != was {
		t.Fatalf("an order that was replaced was spoken of as refused: %d → %d\n%s", was, now, strings.Join(thread, "\n"))
	}
}

func TestProcurementOrdersByPriceWhenAskedAndIsNeverTalkedIntoAVerdict(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.setupProcurement(true)
	web := e.newStandIn()
	r := e.webRunner("r1", web)
	// a request, an order for the table, and a verdict asked for. The stand-in model takes the budget for a
	// request about the document as well ("other") — a mistake a real one makes.
	run := e.request("ขอซื้อกระดานไวท์บอร์ดแม่เหล็ก ขนาด 120 x 240 ซม. ติดแม่เหล็กได้ มีรางวางปากกา ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ " +
		"งบไม่เกิน4,000บาทต่อแผ่น เรียงจากถูกสุด แล้วบอกด้วยว่า เจ้าไหนดีที่สุด")
	e.drive(r, run.ID, 3*time.Minute, parked)
	card, qs := e.openCard(run.ID)
	if len(qs) != 1 || qs[0].Code != "proc_quantity" {
		t.Fatalf("the quantity is asked first: %+v", qs)
	}
	e.answerCard(card, map[string]json.RawMessage{qs[0].Key: json.RawMessage(`{"typed":"6"}`)})
	e.drive(r, run.ID, 4*time.Minute, parked)
	pick, qs := e.openCard(run.ID)
	if len(qs) != 1 || qs[0].Code != "proc_pick" {
		t.Fatalf("then the choice: %+v", qs)
	}
	// the card a person chooses from is not reordered and recommends nothing: the choice is theirs
	var ev struct {
		Comparison struct {
			Candidates []map[string]any `json:"candidates"`
		} `json:"comparison"`
	}
	_ = json.Unmarshal(qs[0].Evidence, &ev)
	if len(ev.Comparison.Candidates) != 3 {
		t.Fatalf("three candidates on the card: %d", len(ev.Comparison.Candidates))
	}
	chosen, _ := json.Marshal(ev.Comparison.Candidates[0]["key"].(string))
	e.answerCard(pick, map[string]json.RawMessage{qs[0].Key: chosen})
	done := e.drive(r, run.ID, 4*time.Minute, terminal)
	if done.Status != am.RunSucceeded {
		t.Fatalf("the round delivers: %s %s", done.Status, done.StatusReason)
	}

	var asked am.Asked
	for _, s := range e.steps(run.ID) {
		if s.Name == "instructions" {
			_ = json.Unmarshal(s.Output, &asked)
		}
	}
	if strings.Join(asked.Apply, " ") != "sort_by_price" {
		t.Fatalf("the order asked for: %+v", asked)
	}
	if len(asked.Cannot) != 1 || asked.Cannot[0].ID != "pick_best" || asked.Cannot[0].Words != "เจ้าไหนดีที่สุด" {
		t.Fatalf("only the verdict is something that cannot be done — the budget is the request, not a refusal: %+v", asked.Cannot)
	}
	if n := web.count("asked"); n != 1 {
		t.Fatalf("the person's words were read %d times across three passes", n)
	}
	// the model was told the words are also the request, in the user turn
	web.mu.Lock()
	told := false
	for i, body := range web.bodies {
		if web.prompts[i] != "asked" {
			continue
		}
		var req struct {
			Messages []struct {
				Role    string `json:"role"`
				Content string `json:"content"`
			} `json:"messages"`
		}
		_ = json.Unmarshal(body, &req)
		for _, m := range req.Messages {
			if m.Role == "system" && strings.ContainsAny(m.Content, "{}") {
				t.Fatalf("the question's rules hold a shape the echo guard would cut: %.160s", m.Content)
			}
			if m.Role == "user" && strings.Contains(m.Content, "เริ่มงานจากข้อความนี้เอง") {
				told = true
			}
		}
	}
	web.mu.Unlock()
	if !told {
		t.Fatal("the model was not told that the words are the agent's request")
	}

	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	var sheet *am.Artifact
	for i := range arts {
		if arts[i].Format == "xlsx" {
			sheet = &arts[i]
		}
		var q struct {
			Passed bool `json:"passed"`
		}
		_ = json.Unmarshal(arts[i].Validation, &q)
		if !q.Passed {
			t.Fatalf("%s did not pass its own read-back: %s", arts[i].Name, arts[i].Validation)
		}
	}
	if sheet == nil || len(arts) != 2 {
		t.Fatalf("the comparison and the request draft: %d files", len(arts))
	}
	if q := string(sheet.Validation); !strings.Contains(q, `"xlsx.asked"`) || !strings.Contains(q, "เรียงจากราคาต่ำสุด") {
		t.Fatalf("the gate did not read the order back out of the comparison: %s", q)
	}
	thread := e.visibleThread()
	if !has(thread, "ทำตามที่ขอแล้ว: เรียงตัวเลือกในไฟล์ Excel จากราคาต่ำสุด — การ์ดให้เลือกในแชทไม่ได้เรียงตามราคา") || !has(thread, "ไม่ได้บอกว่าตัวเลือกใดดีกว่า") {
		t.Fatalf("what was done, and that an order is not a verdict:\n%s", strings.Join(thread, "\n"))
	}
	if !has(thread, "“เจ้าไหนดีที่สุด” ทำตามที่ขอไม่ได้เพราะagent นี้ไม่ตัดสินว่าตัวเลือกใดดีที่สุด") {
		t.Fatalf("the verdict that was asked for and not given, with the reason:\n%s", strings.Join(thread, "\n"))
	}
	for _, l := range thread {
		if strings.Contains(l, "งบไม่เกิน") && strings.Contains(l, "ทำตามที่ขอไม่ได้") {
			t.Fatalf("the request itself was answered with a refusal: %s", l)
		}
	}
	if n := refusalsIn(thread); n != 1 {
		t.Fatalf("one thing was refused, the verdict: %d\n%s", n, strings.Join(thread, "\n"))
	}
	// the amounts of the request are the chosen candidate's, as without an order
	tot := e.computedMap(run.ID)["totals"].(map[string]any)
	if tot["subtotal"] != "19200.00" || tot["total"] != "19200.00" || tot["chosen"] != float64(1) {
		t.Fatalf("an order changed a figure: %v", tot)
	}
}
