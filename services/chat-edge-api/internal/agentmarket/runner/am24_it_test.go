package runner

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM24 §1.2, the defect made on purpose (CARD-RULES §6d): the in-house model writes a Thai mark twice, through the
// real sandbox and the runner's own engine. The unit tests (am24_marks_test.go) stand in for the engine; these are
// the proof that the check is wired — that the runner asks the engine's thai_marks, and that what a person opens is
// the mended word with the run's record saying so:
//
//	slipping      → the model writes "เพิ่่ม" on both attempts: the workbook says "เพิ่ม", never "เพิ่่ม", and the
//	                narrative step records the text as written and as used, and that it was checked;
//	no reading    → the model writes two tone marks on one letter ("ก่้") both times: no answer is used, the rules
//	                write the commentary and the sheet says so; the slip is nowhere in the file.
func TestAThaiMarkTheModelWritesTwiceIsMendedInTheFileAndRecorded(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	run := e.am22BankRun()
	e.slipping.Store(1)
	r := e.runner("r1")
	got := e.drive(r, run.ID, 5*time.Minute, parkedOrTerminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("%s %s", got.Status, got.StatusReason)
	}
	texts := e.am22SheetTexts(r, run)
	if strings.Contains(texts, "เพิ่่ม") || !strings.Contains(texts, "ค่าที่ปรึกษาเพิ่มขึ้นมาก") {
		t.Fatalf("the workbook carries the mended word and never the slip; it holds:\n%s", trim(texts, 1200))
	}
	found := false
	for _, s := range e.steps(run.ID) {
		if s.Name != "narrative" {
			continue
		}
		found = true
		var n narrativeOut
		_ = json.Unmarshal(s.Output, &n)
		if n.MarksUnchecked != "" {
			t.Fatalf("the engine read the commentary's Thai: %q", n.MarksUnchecked)
		}
		if len(n.MarksMended) != 1 || n.MarksMended[0].Where != "movements[0]" ||
			!strings.Contains(n.MarksMended[0].Before, "เพิ่่ม") || !strings.Contains(n.MarksMended[0].After, "เพิ่มขึ้นมาก") {
			t.Fatalf("the run's record holds the text as written and as used: %+v", n.MarksMended)
		}
		// (the stand-in's answer has no headings of its own: AM23's soft rule sends both attempts back for them, and the
		// last is used at the end — read once then, by the engine, which is how a slip in such an answer is caught)
		if len(n.Tried) != 2 {
			t.Fatalf("both attempts were sent back: %q", n.Tried)
		}
	}
	if !found {
		t.Fatal("no narrative step")
	}
}

func TestAThaiSlipWithNoOneReadingLeavesTheRulesWords(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	run := e.am22BankRun()
	e.slipping.Store(2)
	r := e.runner("r1")
	got := e.drive(r, run.ID, 5*time.Minute, parkedOrTerminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the working paper is not held back for its commentary: %s %s", got.Status, got.StatusReason)
	}
	texts := e.am22SheetTexts(r, run)
	if strings.Contains(texts, "ก่้") || !strings.Contains(texts, am22RulesLine) {
		t.Fatalf("no answer with the slip is used; the rules write and say so; the workbook holds:\n%s", trim(texts, 1200))
	}
	for _, s := range e.steps(run.ID) {
		if s.Name == "narrative" {
			var n narrativeOut
			_ = json.Unmarshal(s.Output, &n)
			var words struct {
				WrittenBy string `json:"written_by"`
				Why       string `json:"why"`
			}
			_ = json.Unmarshal(n.Narrative, &words)
			if words.WrittenBy != "rules" || words.Why != "unusable" || len(n.MarksMended) != 0 {
				t.Fatalf("the rules wrote, because no answer could be used: %+v %+v", words, n.MarksMended)
			}
		}
	}
}
