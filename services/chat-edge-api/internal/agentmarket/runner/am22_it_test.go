package runner

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"html"
	"io"
	"regexp"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM22 §1.1 — the bank reconciliation's commentary is written by a model, and when no model can be reached
// its rules write it and the working paper says so on its face. The two fixtures are permanent:
//
//	away    → the run is not held back for a commentary: it delivers, the commentary sheet carries the line
//	          that the rules wrote it, the thread says so, and nothing is charged for a model step that did
//	          not happen;
//	present → the model's words are on the sheet, the sheet names the model, and the rules' line is nowhere
//	          in the file (the fixture that catches the line left in by accident).

const am22RulesLine = "คำอธิบายในส่วนนี้เขียนตามกฎของแพ็กเกจ เพราะไม่มีโมเดลที่ใช้ได้"

// am22BankRun is a bank run with nothing typed beside its files: no words to read, so the commentary is the
// first step that asks a model.
func (e *itEnv) am22BankRun() am.Run {
	run := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	if _, err := e.store.Pool.Exec(context.Background(), `UPDATE agent_market.agent_runs SET message = '' WHERE id = $1`, run.ID); err != nil {
		e.t.Fatal(err)
	}
	run.Message = ""
	return run
}

var xmlTag = regexp.MustCompile(`<[^>]*>`)

// am22SheetTexts is the text of the delivered workbook's sheets (what a person reads in it).
func (e *itEnv) am22SheetTexts(r *Runner, run am.Run) string {
	e.t.Helper()
	ctx := context.Background()
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	for _, a := range arts {
		if a.Format != "xlsx" {
			continue
		}
		body, _, _, err := r.Files.Download(ctx, a.StorageKey)
		if err != nil {
			e.t.Fatal(err)
		}
		data, _ := io.ReadAll(body)
		body.Close()
		z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if err != nil {
			e.t.Fatal(err)
		}
		var b strings.Builder
		for _, f := range z.File {
			if f.Name == "xl/sharedStrings.xml" || f.Name == "xl/workbook.xml" || strings.HasPrefix(f.Name, "xl/worksheets/") {
				rc, _ := f.Open()
				x, _ := io.ReadAll(rc)
				rc.Close()
				// the text a person reads: the markup taken out and the characters it writes as references
				// (&#3591; is the Thai letter) written back, so a cell's runs read as one sentence
				b.WriteString(html.UnescapeString(xmlTag.ReplaceAllString(string(x), "")))
				b.WriteByte('\n')
			}
		}
		return b.String()
	}
	e.t.Fatal("no workbook was delivered")
	return ""
}

func TestAReconciliationWhoseModelIsAwayIsDeliveredWithTheRulesWordsSayingSo(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	run := e.am22BankRun()
	e.away.Store(true)
	r := e.runner("r1")
	got := e.drive(r, run.ID, 5*time.Minute, parkedOrTerminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the working paper is not held back for its commentary: %s %s", got.Status, got.StatusReason)
	}
	if e.awayCalls.Load() == 0 {
		t.Fatal("the commentary asked a model, and found it away")
	}
	texts := e.am22SheetTexts(r, run)
	if !strings.Contains(texts, am22RulesLine) || !strings.Contains(texts, "คำอธิบายรอบนี้") {
		t.Fatalf("the commentary sheet says on its face that the rules wrote it; the workbook holds:\n%s", trim(texts, 1200))
	}
	if !has(e.threadOf(run.ID), "เขียนจากกฎ") {
		t.Fatalf("the round's message says the rules wrote the words:\n%s", strings.Join(e.threadOf(run.ID), "\n"))
	}
	for _, s := range e.steps(run.ID) {
		if s.Name == "narrative" {
			var n narrativeOut
			_ = json.Unmarshal(s.Output, &n)
			var words struct {
				WrittenBy string `json:"written_by"`
				Why       string `json:"why"`
			}
			_ = json.Unmarshal(n.Narrative, &words) // (the database gives the JSON back in its own spacing)
			if s.Status != "succeeded" || n.Model != "" || words.WrittenBy != "rules" || words.Why != "unavailable" {
				t.Fatalf("the narrative step is the rules', because no model could be reached: %s model=%q %s", s.Status, n.Model, trim(string(n.Narrative), 160))
			}
			if s.Credits != 0 {
				t.Fatalf("a model step that did not happen is charged nothing: %v", s.Credits)
			}
		}
	}
	outs := e.outputsOf(run.ID)
	if len(outs) != 1 {
		t.Fatalf("one files card: %v", outs)
	}
	model, _ := outs[0]["model"].(map[string]any)
	if model["written_by_rules"] != true {
		t.Fatalf("the files card says the rules wrote the words: %v", model)
	}
}

func TestAReconciliationAModelWroteNamesTheModelAndNeverCarriesTheRulesLine(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	run := e.am22BankRun()
	r := e.runner("r1")
	got := e.drive(r, run.ID, 5*time.Minute, parkedOrTerminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("%s %s", got.Status, got.StatusReason)
	}
	texts := e.am22SheetTexts(r, run)
	if strings.Contains(texts, am22RulesLine) {
		t.Fatal("a commentary a model wrote carries the line that the rules wrote it")
	}
	if !strings.Contains(texts, "fake-self-hosted") || !strings.Contains(texts, "ค่าที่ปรึกษาเพิ่มขึ้นมาก") {
		t.Fatalf("the sheet carries the model's words and names the model; the workbook holds:\n%s", trim(texts, 1200))
	}
	if got.ModelUsed == nil || *got.ModelUsed != "fake-self-hosted" {
		t.Fatalf("the run names the model that wrote its words: %v", got.ModelUsed)
	}
	for _, s := range e.steps(run.ID) {
		if s.Name == "narrative" {
			// what the model was shown is the computed table: no file of the round, no row of a statement
			var n narrativeOut
			_ = json.Unmarshal(s.Output, &n)
			if n.Model != "fake-self-hosted" || !n.SelfHosted {
				t.Fatalf("the in-house model wrote: %q %v", n.Model, n.SelfHosted)
			}
		}
	}
}

// narrativeModel is the model that wrote a round's commentary (AM22), "" when its rules did.
func narrativeModel(steps []am.Step) string {
	for _, s := range steps {
		if s.Name == "narrative" {
			return s.ModelID
		}
	}
	return ""
}
