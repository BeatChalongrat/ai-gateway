package runner

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM18 part B — the agent does what it is asked of its documents, in the person's own words, end to end:
// the real engine in the real sandbox, the bank reconciliation's own package, and the stand-in for the
// model that reads a person's words (askedAnswer).
//
//   - the language is asked for with the files: the round is made in it, its working paper's sheets are
//     named in it, and the thread says so;
//   - "every time" is kept as the install's own way: the next round, with no words at all, is made the
//     same — and a person clears it on the settings page (the defaults), after which a round is as the
//     package makes it;
//   - a finished round asked for the other language keeps its file; a new round starts from the same
//     files in the language asked;
//   - a request that could mean two things is asked with a card before any work, and the answer is what
//     the file is made with; a language the agent does not speak is refused with the reason, before work.

var thaiLetters = regexp.MustCompile(`[\x{0E00}-\x{0E7F}]`)

// xlsxSheetNames reads the names of a finished workbook's sheets out of storage.
func (e *itEnv) xlsxSheetNames(run uuid.UUID) []string {
	e.t.Helper()
	ctx := context.Background()
	arts, err := e.store.RunArtifacts(ctx, run)
	if err != nil {
		e.t.Fatal(err)
	}
	for _, a := range arts {
		if a.Format != "xlsx" {
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
			if f.Name != "xl/workbook.xml" {
				continue
			}
			r, _ := f.Open()
			x, _ := io.ReadAll(r)
			r.Close()
			var names []string
			for _, m := range regexp.MustCompile(`<sheet [^>]*name="([^"]*)"`).FindAllStringSubmatch(string(x), -1) {
				names = append(names, m[1])
			}
			return names
		}
	}
	e.t.Fatalf("the run has no .xlsx")
	return nil
}

func (e *itEnv) bankRunSaying(text string) am.Run {
	e.t.Helper()
	run := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	if _, err := e.store.Pool.Exec(context.Background(), `UPDATE agent_market.agent_runs SET message = $2 WHERE id = $1`, run.ID, text); err != nil {
		e.t.Fatal(err)
	}
	run.Message = text
	return run
}

func (e *itEnv) askedOf(run uuid.UUID) (asked am.Asked, first string) {
	e.t.Helper()
	for i, s := range e.steps(run) {
		if i == 0 {
			first = s.Name
		}
		if s.Name == "instructions" {
			_ = json.Unmarshal(s.Output, &asked)
		}
	}
	return asked, first
}

func (e *itEnv) defaultsOf(install uuid.UUID) (ids []string, lang am.Lang) {
	e.t.Helper()
	defs, err := e.store.ListDefaults(context.Background(), install)
	if err != nil {
		e.t.Fatal(err)
	}
	return am.StandingOf(defs)
}

func TestTheLanguageAndEveryTimeAreHeardKeptAndCleared(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	in := e.setupBankInstall()
	ctx := context.Background()
	r := e.runner("r1")

	// A. asked with the files: English, and by date every time
	run := e.bankRunSaying("กระทบยอดเดือนกันยายน ขอเป็นภาษาอังกฤษ เรียงตามวันที่ทุกครั้ง")
	got := e.drive(r, run.ID, 6*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the round: %s %s", got.Status, got.StatusReason)
	}
	if got.Lang() != am.English {
		t.Fatalf("the round is in the language asked: %q", got.Lang())
	}
	asked, first := e.askedOf(run.ID)
	if first != "instructions" {
		t.Fatalf("the words are read before any work; the first step is %q", first)
	}
	if asked.Lang != "en" || strings.Join(asked.Apply, " ") != "sort_by_date" || strings.Join(asked.Standing, " ") != "sort_by_date lang_en" {
		t.Fatalf("what was read: %+v", asked)
	}
	ids, lang := e.defaultsOf(in.ID)
	if strings.Join(ids, " ") != "sort_by_date" || lang != am.English {
		t.Fatalf("kept as the install's own way: %v %q", ids, lang)
	}
	names := e.xlsxSheetNames(run.ID)
	if len(names) < 5 || thaiLetters.MatchString(strings.Join(names, " ")) || !contains(names, "Reconciling items") {
		t.Fatalf("an English paper's sheets: %v", names)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 1 || !strings.Contains(string(arts[0].Validation), `"xlsx.asked"`) || thaiLetters.MatchString(string(arts[0].Validation)) {
		t.Fatalf("the gate read the order back, in English: %s", arts[0].Validation)
	}
	thread := e.visibleThread()
	for _, want := range []string{"This round's documents are in English, as asked", "Kept as this agent's own way from now on:", "settings page",
		"Done as asked:"} {
		if !has(thread, want) {
			t.Fatalf("the thread does not say %q:\n%s", want, strings.Join(thread, "\n"))
		}
	}
	if has(thread, "ทำตามที่ขอแล้ว") {
		t.Fatalf("a Thai line in an English round's thread:\n%s", strings.Join(thread, "\n"))
	}

	// B. the next round, with no request at all: English, by date — because the install keeps it
	run2 := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	got2 := e.drive(r, run2.ID, 6*time.Minute, terminal)
	if got2.Status != am.RunSucceeded || got2.Lang() != am.English {
		t.Fatalf("the next round follows the install's way: %s %q", got2.Status, got2.Lang())
	}
	arts2, _ := e.store.RunArtifacts(ctx, run2.ID)
	if len(arts2) != 1 || !strings.Contains(string(arts2[0].Validation), `"xlsx.asked"`) {
		t.Fatalf("the order kept is applied without a word: %s", arts2[0].Validation)
	}
	if asked2, _ := e.askedOf(run2.ID); len(asked2.Apply) != 0 || len(asked2.Standing) != 0 {
		t.Fatalf("nothing was asked this time: %+v", asked2)
	}
	if thread = e.visibleThread(); !has(thread, "Made as asked earlier:") {
		t.Fatalf("the thread says what was made because it is kept:\n%s", strings.Join(thread, "\n"))
	}

	// C. the finished round asked for Thai: its file stays; a new round from the same files, in Thai
	job := e.say(got2, "ขอเป็นภาษาไทย")
	if j := e.drive(r, job.ID, 3*time.Minute, terminal); j.Status != am.RunSucceeded {
		t.Fatalf("the reply: %s %s", j.Status, j.StatusReason)
	}
	if again, _ := e.store.RunArtifacts(ctx, run2.ID); len(again) != 1 || again[0].ID != arts2[0].ID {
		t.Fatalf("the finished round's file was touched: %+v", again)
	}
	if n := len(e.renderJobs(run2.ID)); n != 0 {
		t.Fatalf("no file of the finished round is made again in another language: %d render jobs", n)
	}
	var newID uuid.UUID
	if err := e.store.Pool.QueryRow(ctx, `SELECT id FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'run'
		AND id NOT IN ($2, $3) ORDER BY created_at DESC LIMIT 1`, in.ID, run.ID, run2.ID).Scan(&newID); err != nil {
		t.Fatalf("a new round was started: %v", err)
	}
	run3, _ := e.store.RunByID(ctx, newID)
	if run3.Lang() != am.Thai || run3.Message != "ขอเป็นภาษาไทย" {
		t.Fatalf("the new round is in Thai, from the words: %q %q", run3.Lang(), run3.Message)
	}
	if files, _ := e.store.RunFiles(ctx, run3.ID, "active"); len(files) != 2 {
		t.Fatalf("the new round has the finished round's files: %d", len(files))
	}
	got3 := e.drive(r, run3.ID, 6*time.Minute, terminal)
	if got3.Status != am.RunSucceeded || got3.Lang() != am.Thai {
		t.Fatalf("the Thai round: %s %q", got3.Status, got3.Lang())
	}
	if names := e.xlsxSheetNames(run3.ID); !contains(names, "รายการกระทบยอด") {
		t.Fatalf("a Thai paper's sheets: %v", names)
	}
	thread = e.visibleThread()
	if !has(thread, "A finished round's files keep their language") {
		t.Fatalf("the thread says the old file stays and a new round starts:\n%s", strings.Join(thread, "\n"))
	}
	// ("Thai, this once" did not change the install's way: the language kept is still English)
	if _, lang := e.defaultsOf(in.ID); lang != am.English {
		t.Fatalf("a request for this round changed the install's way: %q", lang)
	}

	// D. cleared by a person, on the settings page: a round is as the package makes it
	for _, key := range []string{am.StandingKey, am.LanguageKey} {
		if ok, err := e.store.DeleteDefault(ctx, in.ID, key); err != nil || !ok {
			t.Fatalf("clear %s: %v %v", key, ok, err)
		}
	}
	run4 := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	got4 := e.drive(r, run4.ID, 6*time.Minute, terminal)
	if got4.Status != am.RunSucceeded || got4.Lang() != am.Thai {
		t.Fatalf("after clearing: %s %q", got4.Status, got4.Lang())
	}
	arts4, _ := e.store.RunArtifacts(ctx, run4.ID)
	if len(arts4) != 1 || strings.Contains(string(arts4[0].Validation), `"xlsx.asked"`) {
		t.Fatalf("nothing is in force after clearing: %s", arts4[0].Validation)
	}
	if names := e.xlsxSheetNames(run4.ID); !contains(names, "รายการกระทบยอด") {
		t.Fatalf("a Thai paper again: %v", names)
	}
}

func TestARequestThatCouldMeanTwoThingsIsAskedBeforeWorkAndAnotherLanguageIsRefused(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	ctx := context.Background()
	r := e.runner("r1")
	run := e.bankRunSaying("กระทบยอดเดือนกันยายน เรียงรายการใหม่ด้วย และขอเป็นภาษาญี่ปุ่น")
	got := e.drive(r, run.ID, 3*time.Minute, parkedOrTerminal)
	if got.Status != am.RunAwaitingInput {
		t.Fatalf("the round waits for the answer: %s %s", got.Status, got.StatusReason)
	}
	// before any work: the words were read, and nothing else was done
	steps := e.steps(run.ID)
	for _, s := range steps {
		if s.Name == "parse" || s.Name == "preflight" || s.Name == "compute" {
			t.Fatalf("work was done before the question was answered: %s", s.Name)
		}
	}
	card, qs := e.openCard(run.ID)
	if card.Kind != "questions" || !card.Blocking || len(qs) != 1 || qs[0].Key != am.AskedChoiceKey(0) || len(qs[0].Options) != 2 {
		t.Fatalf("one card, one question, two things it could mean: %+v %+v", card, qs)
	}
	if !strings.Contains(qs[0].Question, "เรียงรายการใหม่ด้วย") {
		t.Fatalf("the question quotes the person: %s", qs[0].Question)
	}
	e.answerCard(card, map[string]json.RawMessage{qs[0].Key: json.RawMessage(`"sort_by_amount"`)})
	got = e.drive(r, run.ID, 6*time.Minute, terminal)
	if got.Status != am.RunSucceeded || got.Lang() != am.Thai {
		t.Fatalf("the round after the answer: %s %s %q", got.Status, got.StatusReason, got.Lang())
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 1 || !strings.Contains(string(arts[0].Validation), `"xlsx.asked"`) || !strings.Contains(string(arts[0].Validation), "เรียงจากจำนวนเงินมากที่สุดลงไป") {
		t.Fatalf("the file is made with what was chosen, and the gate read it back: %s", arts[0].Validation)
	}
	thread := e.visibleThread()
	for _, want := range []string{
		"ทำตามที่ขอแล้ว: เรียงรายการกระทบยอดจากจำนวนเงินมากที่สุด",
		"ทำตามที่ขอไม่ได้เพราะagent นี้ทำเอกสารได้เป็นภาษาไทยและภาษาอังกฤษเท่านั้น",
	} {
		if !has(thread, want) {
			t.Fatalf("the thread does not say %q:\n%s", want, strings.Join(thread, "\n"))
		}
	}
	// nothing was kept: the choice and the refusal were this round's
	if ids, lang := e.defaultsOf(e.install.ID); len(ids) != 0 || lang != "" {
		t.Fatalf("kept without being asked to: %v %q", ids, lang)
	}
}
