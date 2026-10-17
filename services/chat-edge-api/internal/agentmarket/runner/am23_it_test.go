package runner

import (
	"context"
	"encoding/json"
	"sort"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM23 — the file a person asks for, from any agent. The integration fixtures are permanent.

// §1.1: a round whose file set names only a kind of file its agent does not make. On 17 Oct 2026, at 0630ae7, a
// bank reconciliation whose set said Word alone ended "succeeded" with no file and the thread said "เสร็จแล้วครับ":
// the settings page took any list, the runner handed it on, and the engine kept the workbook it makes and dropped
// the rest without a word. Now the round stops before any work, charged nothing, and says what was not made and
// what the agent makes.
func TestARoundAskedOnlyForAFileItsAgentDoesNotMakeStopsBeforeAnyWorkAndSaysWhy(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.setupBankInstall()
	run := e.am22BankRun()
	notMade := am23NotMadeByBank(t)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET formats = $2 WHERE id = $1`, run.ID, `["`+notMade+`"]`); err != nil {
		t.Fatal(err)
	}
	r := e.runner("r1")
	got := e.drive(r, run.ID, 5*time.Minute, parkedOrTerminal)
	if got.Status != am.RunFailed || got.StatusReason != "format_not_offered" {
		t.Fatalf("the round stops for it: %s %s", got.Status, got.StatusReason)
	}
	if arts, _ := e.store.RunArtifacts(ctx, run.ID); len(arts) != 0 {
		t.Fatalf("no file: %d", len(arts))
	}
	for _, s := range e.steps(run.ID) {
		if s.Name != "stopped" { // (the record of why the round stopped)
			t.Fatalf("no work was done before the refusal: step %s", s.Name)
		}
	}
	thread := strings.Join(e.threadOf(run.ID), "\n")
	if !strings.Contains(thread, "agent ตัวนี้ไม่ได้ทำ") || !strings.Contains(thread, "ทำได้: ตาราง Excel") {
		t.Fatalf("the thread says what was not made and what the agent makes:\n%s", trim(thread, 1500))
	}
	if strings.Contains(thread, "เสร็จแล้ว") {
		t.Fatalf("nothing says it is done:\n%s", trim(thread, 1500))
	}
	if run2, _ := e.store.RunByID(ctx, run.ID); run2.CreditsSpent != 0 {
		t.Fatalf("a round that did nothing is charged nothing: %v", run2.CreditsSpent)
	}
}

// am23NotMadeByBank is a kind of file the bank reconciliation's manifest does not declare.
func am23NotMadeByBank(t *testing.T) string {
	t.Helper()
	m := am23Manifest(t, "bank_reconciliation")
	for _, f := range []string{"docx", "pptx", "xls"} {
		if !m.FormatAllowed(f) {
			return f
		}
	}
	t.Fatal("the bank reconciliation makes every kind of file the test knows")
	return ""
}

// §3.2: the organisation's switch reaches the file. A bank reconciliation whose words a model drafted carries the
// one neutral line beside its commentary's references while the switch is on (its default), and not once an admin
// has switched it off; neither file names the model.
func TestTheNeutralLineOnAWorkingPaperFollowsTheOrganisationsSwitch(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.setupBankInstall()
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_settings (org_id) VALUES ('default') ON CONFLICT DO NOTHING`); err != nil {
		t.Fatal(err)
	}
	r := e.runner("r1")
	for _, on := range []bool{true, false} {
		if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.org_settings SET assistant_line = $2 WHERE org_id = $1`, am.DefaultOrg, on); err != nil {
			t.Fatal(err)
		}
		run := e.am22BankRun()
		got := e.drive(r, run.ID, 5*time.Minute, parkedOrTerminal)
		if got.Status != am.RunSucceeded {
			t.Fatalf("switch %v: %s %s", on, got.Status, got.StatusReason)
		}
		texts := e.am22SheetTexts(r, run)
		if strings.Contains(texts, am23AssistantLine) != on {
			t.Fatalf("switch %v: the neutral line is in the file exactly while the switch is on:\n%s", on, trim(texts, 1200))
		}
		if strings.Contains(texts, "fake-self-hosted") || strings.Contains(texts, am22RulesLine) {
			t.Fatalf("switch %v: no model's name, and no rules' sentence in a model-present run:\n%s", on, trim(texts, 1200))
		}
		if !strings.Contains(texts, "ค่าที่ปรึกษาเพิ่มขึ้นมาก") {
			t.Fatalf("switch %v: the model's words are there either way", on)
		}
		// the round's messages name no model either (its progress card is the run's own record of its steps, and
		// keeps which model wrote; the files card's payload carries it for that record and shows it nowhere)
		for _, line := range e.threadOf(run.ID) {
			var item struct {
				Text string `json:"text"`
			}
			if json.Unmarshal([]byte(line), &item) == nil && strings.Contains(item.Text, "fake-self-hosted") {
				t.Fatalf("the thread names no model either: %s", trim(item.Text, 300))
			}
		}
	}
}

// §1.2–§1.4: a bank reconciliation asked for its workbook, a report, a deck and a PDF delivers the four — the report
// and the deck written from the same computed table, the PDF the workbook as it prints, every figure in it as the
// workbook prints it — and a round asked for the PDF alone delivers the PDF alone (the workbook it prints is made for
// it and not delivered). The files card calls the PDF what it prints.
func TestABankRoundMakesTheReportTheDeckAndThePDFOfItsWorkbookWhenAsked(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.setupBankInstall()
	conv := startConverter(t)
	r := e.runner("r1")
	r.Converter = conv
	for _, c := range []struct {
		formats string
		want    []string
	}{{`["xlsx","docx","pptx","pdf"]`, []string{"docx", "pdf", "pptx", "xlsx"}}, {`["pdf"]`, []string{"pdf"}}} {
		run := e.am22BankRun()
		if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET formats = $2 WHERE id = $1`, run.ID, c.formats); err != nil {
			t.Fatal(err)
		}
		got := e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
		if got.Status != am.RunSucceeded {
			t.Fatalf("%s: %s %s", c.formats, got.Status, got.StatusReason)
		}
		arts, _ := e.store.RunArtifacts(ctx, run.ID)
		var have []string
		for _, a := range arts {
			have = append(have, a.Format)
			var q struct {
				Passed bool        `json:"passed"`
				Checks []gateCheck `json:"checks"`
			}
			_ = json.Unmarshal(a.Validation, &q)
			if !q.Passed {
				t.Fatalf("%s: %s did not pass its gate: %+v", c.formats, a.Format, q.Checks)
			}
			if a.Format == "pdf" {
				names := map[string]bool{}
				for _, k := range q.Checks {
					names[k.Name] = k.OK
				}
				if !names["pdf.pages"] || !names["pdf.figures"] {
					t.Fatalf("the workbook's PDF is gated on its pages and its figures: %+v", q.Checks)
				}
			}
		}
		sort.Strings(have)
		if strings.Join(have, ",") != strings.Join(c.want, ",") {
			t.Fatalf("%s delivers %v, not %v", c.formats, have, c.want)
		}
		outs := e.outputsOf(run.ID)
		if len(outs) != 1 || outs[0]["pdf_of"] != "xlsx" {
			t.Fatalf("the files card says what the PDF prints: %v", outs)
		}
	}
}
