package runner

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM15 — the fixture of a run and a model that cannot be reached, in both directions.
//
// What the test stack showed on 12 Oct 2026 with the in-house model taken away: the bank
// reconciliation, with "sort by amount" typed beside its files, succeeded in 85 s — one file delivered,
// 1,316 credits charged, the words unread, "the documents are the usual ones" in the thread. So did the
// other four. Here the stand-in gateway says what the real one said (504 provider_timeout), and:
//
//	down → the run waits: nothing rendered, nothing delivered, the thread says the model cannot be
//	       reached — in the run's language — and not that anything was finished or unread;
//	back → the same run goes on from the step that could not ask, reads the words and delivers the
//	       file in the order asked; the steps before it are not run again.

const (
	am15Away   = "ตอนนี้ติดต่อโมเดลที่ agent นี้ใช้ไม่ได้ จึงยังไม่ทำไฟล์ออกมา"
	am15AwayEN = "the model this agent uses cannot be reached right now, so no file has been made yet"
	am15Back   = "ติดต่อโมเดลได้แล้ว รอบนี้ทำต่อจากขั้นที่ค้างไว้"
)

func parkedOrTerminal(r am.Run) bool { return parked(r) || terminal(r) }

// am15StepIDs: the id of every step of a run by name — a step that is run again is a new row.
func (e *itEnv) am15StepIDs(run uuid.UUID) map[string]string {
	out := map[string]string{}
	for _, s := range e.steps(run) {
		out[s.Name+"/"+s.Status] = s.ID.String()
	}
	return out
}

func (e *itEnv) am15Waiting(t *testing.T, run am.Run, failedStep string) {
	t.Helper()
	ctx := context.Background()
	if run.Status != am.RunAwaitingInput || run.StatusReason != am.ReasonModelUnreachable {
		t.Fatalf("the model is away: the run waits for it — it is %s (%s)", run.Status, run.StatusReason)
	}
	var names []string
	for _, s := range e.steps(run.ID) {
		names = append(names, s.Name+":"+s.Status)
		if s.Name == "render" || s.Name == "publish" || s.Name == "gate" {
			t.Fatalf("a run that waits for its model has made nothing — it has a %s step", s.Name)
		}
		if strings.HasPrefix(s.Name, failedStep) && s.Status != "failed" {
			t.Fatalf("the step that could not ask is left unfinished (failed), to be asked again: %s is %s", s.Name, s.Status)
		}
	}
	if !strings.Contains(strings.Join(names, " "), failedStep) {
		t.Fatalf("the run stopped at %s: %v", failedStep, names)
	}
	if arts, _ := e.store.RunArtifacts(ctx, run.ID); len(arts) != 0 {
		t.Fatalf("nothing is delivered while the model is away: %d file(s)", len(arts))
	}
	if run.DeliveredAt != nil || run.FinishedAt != nil {
		t.Fatalf("the run has neither delivered nor ended: %+v %+v", run.DeliveredAt, run.FinishedAt)
	}
	// and it is charged nothing while it waits: what its finished steps cost is held, not charged
	if c := e.charged(run.ID); c != 0 || run.CreditsSpent != 0 {
		t.Fatalf("a run that waits for its model is charged nothing: ledger %v, the run's own row %v", c, run.CreditsSpent)
	}
	if held := e.count(`SELECT count(*) FROM agent_market.agent_budget_ledger WHERE run_id = $1 AND void_reason = 'waiting_model'`, run.ID); held == 0 && run.CreditsCost > 0 {
		t.Fatalf("what its steps cost (%v) is held with the wait as the reason: %d rows", run.CreditsCost, held)
	}
}

func TestARunWaitsForAModelThatCannotBeReachedAndGoesOnWhenItIsBack(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	ctx := context.Background()
	r := e.runner("r1")

	for _, lang := range []am.Lang{am.Thai, am.English} {
		run := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
		params := `{}`
		if lang == am.English {
			params = `{"lang":"en"}`
		}
		if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET message = $2, params = $3::jsonb WHERE id = $1`, run.ID,
			"กระทบยอดเดือนกันยายน เรียงตามวันที่", params); err != nil {
			t.Fatal(err)
		}

		// ── down: an honest stop ──
		e.away.Store(true)
		e.askedCalls.Store(0)
		got := e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
		e.am15Waiting(t, got, "instructions")
		// (the catalogue's own sample runs share the stand-in model: only readings of typed words are counted)
		if n := e.askedCalls.Load(); n != 2 {
			t.Fatalf("the words were put to the model %d times; once more after a silence is twice (AM13b), then the run waits", n)
		}
		thread := e.threadOf(run.ID)
		said := am15Away
		if lang == am.English {
			said = am15AwayEN
		}
		if !has(thread, said) {
			t.Fatalf("%s: the thread says the model cannot be reached, in the run's language:\n%s", lang, strings.Join(thread, "\n"))
		}
		for _, wrong := range []string{"อ่านข้อความที่พิมพ์มาไม่สำเร็จ", "could not be read", "เสร็จแล้วครับ", "ทำตามที่ขอแล้ว"} {
			if has(e.threadOf(run.ID), wrong) {
				t.Fatalf("%s: a run that waits has finished nothing and left nothing unread — the thread says %q", lang, wrong)
			}
		}
		before := e.am15StepIDs(run.ID)

		// still away: the watch asks the model a word, gets none, and leaves the run where it is
		calls := e.awayCalls.Load()
		if n := r.resumeModelParked(ctx); n != 0 {
			t.Fatalf("the model is still away: %d run(s) resumed", n)
		}
		if e.awayCalls.Load() != calls+1 {
			t.Fatalf("the watch asks one word while a run waits: %d call(s)", e.awayCalls.Load()-calls)
		}
		if x, _ := e.store.RunByID(ctx, run.ID); x.Status != am.RunAwaitingInput {
			t.Fatalf("still waiting: %s", x.Status)
		}
		if n := e.count(`SELECT count(*) FROM agent_market.agent_thread_items WHERE run_id = $1 AND kind = 'notice' AND payload->>'reason' = $2`,
			run.ID, am.ReasonModelUnreachable); n != 1 {
			t.Fatalf("the wait is said once: %d notices", n)
		}

		// ── back: the same run finishes, from the step that could not ask ──
		e.away.Store(false)
		// (every run that waits goes on — a catalogue sample of the report may be among them)
		if n := r.resumeModelParked(ctx); n < 1 {
			t.Fatalf("the model answers: the waiting run goes on — %d resumed", n)
		}
		got = e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
		if got.Status != am.RunSucceeded {
			t.Fatalf("%s: the run that waited: %s %s", lang, got.Status, got.StatusReason)
		}
		after := e.am15StepIDs(run.ID)
		// AM18: the words are read before any work, so a run that waits for the model to read them has
		// done nothing yet; once the model is back it does each step, once
		for _, name := range []string{"preflight/succeeded", "compute/succeeded", "postcompute/succeeded"} {
			if before[name] != "" {
				t.Fatalf("%s was done before the words were read: the words are read first (AM18)", name)
			}
			if after[name] == "" {
				t.Fatalf("%s: not done once the model was back", name)
			}
		}
		var asked am.Asked
		for _, s := range e.steps(run.ID) {
			if s.Name == "instructions" && s.Status == "succeeded" {
				_ = json.Unmarshal(s.Output, &asked)
			}
		}
		if asked.Unread || strings.Join(asked.Apply, " ") != "sort_by_date" {
			t.Fatalf("the words typed with the files are read once the model is back: %+v", asked)
		}
		arts, _ := e.store.RunArtifacts(ctx, run.ID)
		if len(arts) != 1 {
			t.Fatalf("%s: one file: %d", lang, len(arts))
		}
		// (what the file says of its order is in the run's language: read in the Thai run)
		if lang == am.Thai && !strings.Contains(string(arts[0].Validation), "เรียงจากวันที่เก่าที่สุดขึ้นก่อน") {
			t.Fatalf("the file is in the order asked: %s", trim(string(arts[0].Validation), 400))
		}
		if lang == am.Thai && !has(e.threadOf(run.ID), am15Back) {
			t.Fatalf("the thread says the run goes on:\n%s", strings.Join(e.threadOf(run.ID), "\n"))
		}
		// charged like any run that delivers — for every step, the ones done before the wait too
		if c := e.charged(run.ID); c <= 0 || c != got.CreditsSpent || got.CreditsSpent != got.CreditsCost {
			t.Fatalf("a run that delivered is charged for all its steps: ledger %v, charged %v, cost %v", c, got.CreditsSpent, got.CreditsCost)
		}
		if held := e.count(`SELECT count(*) FROM agent_market.agent_budget_ledger WHERE run_id = $1 AND void_reason = 'waiting_model'`, run.ID); held != 0 {
			t.Fatalf("nothing is still held once the run went on: %d rows", held)
		}
	}
}

// threadOf is the text of a run's own items in the thread (the visible thread holds every run's).
func (e *itEnv) threadOf(run uuid.UUID) []string {
	e.t.Helper()
	rows, err := e.store.Pool.Query(context.Background(), `SELECT payload::text FROM agent_market.agent_thread_items WHERE run_id = $1 ORDER BY created_at, id`, run)
	if err != nil {
		e.t.Fatal(err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var s string
		_ = rows.Scan(&s)
		out = append(out, s)
	}
	return out
}

// A run that waits for its model and is cancelled is charged nothing — what its finished steps had
// been charged is voided, like any run that ends without delivering.
func TestARunThatWaitsForItsModelAndIsCancelledIsChargedNothing(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	ctx := context.Background()
	run := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET message = $2 WHERE id = $1`, run.ID,
		"กระทบยอดเดือนกันยายน เรียงตามวันที่"); err != nil {
		t.Fatal(err)
	}
	e.away.Store(true)
	r := e.runner("r1")
	e.am15Waiting(t, e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal), "instructions")
	if _, err := e.store.RequestCancel(ctx, run.TenantID, run.UserID, run.ID); err != nil {
		t.Fatal(err)
	}
	got := e.drive(r, run.ID, 2*time.Minute, terminal)
	if got.Status != am.RunCancelled {
		t.Fatalf("cancelled while waiting: %s %s", got.Status, got.StatusReason)
	}
	if c := e.charged(run.ID); c != 0 {
		t.Fatalf("a run that waited and was cancelled is charged nothing: %v", c)
	}
	if got.CreditsSpent != 0 {
		t.Fatalf("and its own row says so: %v", got.CreditsSpent)
	}
	if arts, _ := e.store.RunArtifacts(ctx, run.ID); len(arts) != 0 {
		t.Fatalf("no file: %d", len(arts))
	}
	// and it is not picked up again when the model is back
	e.away.Store(false)
	if n := r.resumeModelParked(ctx); n != 0 {
		t.Fatalf("a cancelled run does not go on: %d resumed", n)
	}
}

// A model that answers the watch's word and never this run's question: the run does not wait and go
// on for ever. After am.MaxModelWaits waits it ends failed, saying so, with nothing made or charged.
func TestARunThatFindsItsModelAwayEveryTimeItGoesOnEndsSayingSo(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	ctx := context.Background()
	run := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET message = $2 WHERE id = $1`, run.ID,
		"กระทบยอดเดือนกันยายน เรียงตามวันที่"); err != nil {
		t.Fatal(err)
	}
	e.askedSilent.Store(1000) // the reading of the words never comes back in time; anything else does
	r := e.runner("r1")
	for wait := 1; wait <= am.MaxModelWaits; wait++ {
		got := e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
		e.am15Waiting(t, got, "instructions")
		if n := r.resumeModelParked(ctx); n < 1 {
			t.Fatalf("wait %d: the model answers the watch's word, so the run goes on — %d resumed", wait, n)
		}
	}
	got := e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
	if got.Status != am.RunFailed || got.StatusReason != am.ReasonModelUnreachable {
		t.Fatalf("after %d waits the run ends, saying why: %s (%s)", am.MaxModelWaits, got.Status, got.StatusReason)
	}
	if !has(e.threadOf(run.ID), "ติดต่อโมเดลที่ agent นี้ใช้ไม่ได้หลายครั้งติดกัน") {
		t.Fatalf("the failure names the model:\n%s", strings.Join(e.threadOf(run.ID), "\n"))
	}
	if arts, _ := e.store.RunArtifacts(ctx, run.ID); len(arts) != 0 || e.charged(run.ID) != 0 {
		t.Fatalf("nothing made, nothing charged: %d file(s), %v", len(arts), e.charged(run.ID))
	}
	if n := e.count(`SELECT count(*) FROM agent_market.agent_thread_items WHERE run_id = $1 AND kind = 'notice' AND payload->>'reason' = $2`,
		run.ID, am.ReasonModelUnreachable); n != am.MaxModelWaits {
		t.Fatalf("%d waits were said: %d", am.MaxModelWaits, n)
	}
}

// A scan: the model that reads it by eye is away. The round does not go on from OCR alone with the
// step still naming the model (what it did until AM15) — it waits, and the scan is read when it is back.
func TestAScanWaitsForTheModelThatReadsIt(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInvoiceInstall()
	ctx := context.Background()
	r := e.runner("r1")
	r.Model.VisionModel = "fake-vision"
	r.Model.SelfHosted["fake-vision"] = true
	run := e.invoiceRun("s01_scan300_full_tax_invoice.pdf")
	// (AM18: words typed with the files are read first, by a model — a round of files alone, so that the
	// first thing this run needs the model for is the scan)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET message = '' WHERE id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}
	e.away.Store(true)
	got := e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
	e.am15Waiting(t, got, "vision:")
	for _, s := range e.steps(run.ID) {
		if s.Name == "preflight" || s.Name == "compute" {
			t.Fatalf("nothing is computed from a scan nobody read by eye: a %s step", s.Name)
		}
	}
	if !has(e.threadOf(run.ID), am15Away) {
		t.Fatalf("the thread says so:\n%s", strings.Join(e.threadOf(run.ID), "\n"))
	}
	e.away.Store(false)
	// (the watch asks the runner's primary model; the stand-in gateway answers for every name)
	if n := r.resumeModelParked(ctx); n < 1 {
		t.Fatalf("the model is back: %d resumed", n)
	}
	got = e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
	if got.StatusReason == am.ReasonModelUnreachable {
		t.Fatalf("the run went on: %s (%s)", got.Status, got.StatusReason)
	}
	read := false
	for _, s := range e.steps(run.ID) {
		if s.Name == "vision:s01_scan300_full_tax_invoice.pdf" && s.Status == "succeeded" {
			var v visionReading
			_ = json.Unmarshal(s.Output, &v)
			read = v.Model == "fake-vision" && v.Fields["total"] == "12850.70"
		}
	}
	if !read || got.ModelUsed == nil || *got.ModelUsed != "fake-vision" {
		t.Fatalf("the scan was read by the model once it was back (read=%v, model_used=%v)", read, got.ModelUsed)
	}
}

// The report's commentary: rules stand in for a model whose answer could not be used (it lied about a
// figure — TestLyingModelFallsBackToRules), not for one that is away. Away, the run waits; back, the
// model writes it.
func TestTheReportsCommentaryWaitsForAModelThatIsAwayAndIsNotWrittenByRules(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(0)
	ctx := context.Background()
	// (no words typed with the files: nothing to read before the commentary, which is then the first step to ask)
	run := e.newRun("", "sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx", "sample-data/งบประมาณ_2569.xlsx")
	e.away.Store(true)
	r := e.runner("r1")
	got := e.drive(r, run.ID, 8*time.Minute, parkedOrTerminal)
	if got.Status == am.RunAwaitingInput && got.StatusReason != am.ReasonModelUnreachable {
		// the sample asks a question first: answered by its defaults, as a person would by pressing them
		e.answerWithDefaults(run.ID)
		got = e.drive(r, run.ID, 8*time.Minute, func(x am.Run) bool {
			return terminal(x) || (parked(x) && x.StatusReason == am.ReasonModelUnreachable)
		})
	}
	e.am15Waiting(t, got, "narrative")
	if has(e.threadOf(run.ID), "เขียนจากกฎ") {
		t.Fatalf("the commentary was written by rules with the model away:\n%s", strings.Join(e.threadOf(run.ID), "\n"))
	}
	e.away.Store(false)
	if n := r.resumeModelParked(ctx); n < 1 {
		t.Fatalf("the model is back: %d resumed", n)
	}
	got = e.drive(r, run.ID, 8*time.Minute, parkedOrTerminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the report that waited: %s %s", got.Status, got.StatusReason)
	}
	for _, s := range e.steps(run.ID) {
		if s.Name == "narrative" && s.Status == "succeeded" {
			var n narrativeOut
			_ = json.Unmarshal(s.Output, &n)
			if n.Model != "fake-self-hosted" || strings.Contains(string(n.Narrative), `"written_by":"rules"`) {
				t.Fatalf("the commentary is the model's once it is back: model=%q %s", n.Model, trim(string(n.Narrative), 160))
			}
		}
	}
	if arts, _ := e.store.RunArtifacts(ctx, run.ID); len(arts) == 0 {
		t.Fatal("the report was delivered")
	}
}
