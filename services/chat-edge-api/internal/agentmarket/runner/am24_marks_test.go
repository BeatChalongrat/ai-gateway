package runner

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync/atomic"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// am24Marks stands in for e2/marks.py over the slips these tests write (test_am24_marks.py holds the rules): a mark
// written twice has one reading; two tone marks on one letter have none. It counts its calls.
func am24Marks(calls *int32) marksFunc {
	one := map[string]string{"ร่่": "ร่", "กัั": "กั"}
	return func(_ context.Context, texts []string) ([]markResult, error) {
		atomic.AddInt32(calls, 1)
		out := make([]markResult, len(texts))
		for i, t := range texts {
			rs := []rune(t)
			mended := t
			fixable := true
			for j := 0; j+3 <= len(rs); j++ {
				w := string(rs[j : j+3])
				if as, ok := one[w]; ok {
					a := as
					out[i].Slips = append(out[i].Slips, struct {
						At      int     `json:"at"`
						Written string  `json:"written"`
						As      *string `json:"as"`
					}{At: j, Written: w, As: &a})
					mended = strings.Replace(mended, w, as, 1)
				}
				if w == "ก่้" {
					fixable = false
					out[i].Slips = append(out[i].Slips, struct {
						At      int     `json:"at"`
						Written string  `json:"written"`
						As      *string `json:"as"`
					}{At: j, Written: w})
				}
			}
			if len(out[i].Slips) > 0 && fixable {
				m := mended
				out[i].Mended = &m
			}
		}
		return out, nil
	}
}

func am24Ask(t *testing.T, check marksFunc, l am.Lang, answers ...string) (narrativeAnswer, string, []string, error) {
	t.Helper()
	ans, c, tried, err := askNarrative(context.Background(), am22Stub(t, answers...), l, []string{"primary"}, "s", "u", am22Limits(), check)
	return ans, c.Model, tried, err
}

// AM24 §1.2 — the refusal cases first: a slip is sent back with the reason and the word it is in; an answer whose slip
// has no one reading is never used, however often it is given; the clean answer after a slip is the one used.
func TestAThaiMarkWrittenTwiceIsSentBackAndTheCleanAnswerIsUsed(t *testing.T) {
	var calls int32
	ans, model, tried, err := am24Ask(t, am24Marks(&calls), am.Thai,
		`{"summary":"ยอดปรับปรุงเท่ากััน {revenue}"}`, `{"summary":"ยอดปรับปรุงเท่ากัน {revenue}"}`)
	if err != nil || model != "primary" || ans.parsed["summary"] != "ยอดปรับปรุงเท่ากัน {revenue}" || len(ans.fixes) != 0 {
		t.Fatalf("the second, clean answer is the one used, nothing mended: %v %q %v %v", err, model, ans.parsed, ans.fixes)
	}
	if len(tried) != 1 || !strings.Contains(tried[0], `เท่ากััน"`) || !strings.Contains(tried[0], "ภาษาไทยไม่เขียน") {
		t.Fatalf("the first answer was sent back naming the word, in Thai: %q", tried)
	}
	if calls != 2 {
		t.Fatalf("each answer's Thai was read: %d calls", calls)
	}
}

func TestASlipWithNoOneReadingIsNeverUsed(t *testing.T) {
	var calls int32
	_, _, tried, err := am24Ask(t, am24Marks(&calls), am.Thai, `{"summary":"ก่้ {revenue}"}`)
	if !errors.Is(err, ErrNoModel) || errors.Is(err, ErrModelUnreachable) || len(tried) != 2 {
		t.Fatalf("two tone marks on one letter, twice: no answer may be used — the rules write: %v %q", err, tried)
	}
	// nor when the same answer is also too long: its length does not carry it to the renderer
	long := make([]string, 9)
	for i := range long {
		long[i] = "ข้อ " + strings.Repeat("ก", i+1)
	}
	long[3] = strings.Repeat("ยาวเกินไป", 8) + " ก่้"
	if _, _, _, err := am24Ask(t, am24Marks(&calls), am.Thai, am22Answer(long, "สรุป {revenue}")); err == nil {
		t.Fatal("a too-long answer with a slip that has no reading is not the renderer's")
	}
}

// After both attempts still slip, the last answer whose every slip has one reading is used mended — and each mended
// text is recorded as written and as used.
func TestAMarkStillWrittenTwiceAfterTheRetryIsMendedAndRecorded(t *testing.T) {
	var calls int32
	ans, model, tried, err := am24Ask(t, am24Marks(&calls), am.Thai,
		`{"summary":"ร่่างนี้ {revenue}","movements":["ปกติ","ยอดเท่ากััน"],"headings":{"watch":"เรื่องที่ต้องดู","summary":"กัับยอด"}}`)
	if err != nil || model != "primary" || len(tried) != 2 {
		t.Fatalf("slipped twice: the mended answer is used: %v %q %q", err, model, tried)
	}
	if ans.parsed["summary"] != "ร่างนี้ {revenue}" || ans.parsed["movements"].([]any)[1] != "ยอดเท่ากัน" ||
		ans.parsed["movements"].([]any)[0] != "ปกติ" || ans.parsed["headings"].(map[string]string)["watch"] != "เรื่องที่ต้องดู" {
		t.Fatalf("only the slips change: %v", ans.parsed)
	}
	if ans.parsed["headings"].(map[string]string)["summary"] != "กับยอด" {
		t.Fatalf("a heading is read and mended like any text: %v", ans.parsed["headings"])
	}
	if len(ans.fixes) != 3 || ans.fixes[0].Where != "headings.summary" || ans.fixes[1].Where != "movements[1]" ||
		ans.fixes[1].Before != "ยอดเท่ากััน" || ans.fixes[1].After != "ยอดเท่ากัน" || ans.fixes[2].Where != "summary" {
		t.Fatalf("each mended text is recorded where it is, as written and as used: %+v", ans.fixes)
	}
}

// A too-long answer with a mendable slip goes to the renderer mended, and recorded; one that could not be checked
// says so — a check that did not look is not one that passed.
func TestATooLongAnswerIsMendedOnItsWayAndAnUncheckedOneSaysSo(t *testing.T) {
	var calls int32
	long := make([]string, 9)
	for i := range long {
		long[i] = "ข้อ " + strings.Repeat("ก", i+1)
	}
	long[3] = strings.Repeat("ยาวเกินไป", 8) + " ร่่าง"
	ans, _, tried, err := am24Ask(t, am24Marks(&calls), am.Thai, am22Answer(long, "สรุป {revenue}"))
	if err != nil || len(ans.fixes) != 1 || !strings.HasSuffix(ans.parsed["movements"].([]any)[3].(string), " ร่าง") {
		t.Fatalf("too long and a slip, twice: mended on its way to the renderer: %v %+v %q", err, ans.fixes, tried)
	}
	if calls != 1 {
		t.Fatalf("an answer only too long is read once, when it is the one used: %d readings", calls)
	}
	down := func(context.Context, []string) ([]markResult, error) { return nil, errors.New("sandbox: no engine") }
	ans, _, _, err = am24Ask(t, down, am.Thai, `{"summary":"ร่่างนี้ {revenue}"}`)
	if err != nil || ans.unchecked == "" || !strings.Contains(ans.unchecked, "no engine") {
		t.Fatalf("an answer whose Thai could not be read is recorded unchecked: %v %q", err, ans.unchecked)
	}
}

// The record says whether the answer USED was checked: an attempt the engine could not read, then one it read and
// found clean, is a checked answer — and a runner with no engine says why it could not check, never panics.
func TestTheRecordSaysWhetherTheAnswerUsedWasChecked(t *testing.T) {
	var calls int32
	flaky := func(ctx context.Context, texts []string) ([]markResult, error) {
		if atomic.AddInt32(&calls, 1) == 1 {
			return nil, errors.New("sandbox: busy")
		}
		return make([]markResult, len(texts)), nil
	}
	// the first answer has a slip with one reading (read, kept mended, asked again); the second is clean, but the
	// engine cannot read it: the answer used is the second, unchecked — the record says so, of it
	ans, _, _, err := am24Ask(t, func(ctx context.Context, texts []string) ([]markResult, error) {
		if atomic.AddInt32(&calls, 1) == 1 {
			return am24Marks(new(int32))(ctx, texts)
		}
		return nil, errors.New("sandbox: busy")
	}, am.Thai, `{"summary":"ร่่าง {revenue}"}`, `{"summary":"ยอดเท่ากัน {revenue}"}`)
	if err != nil || ans.unchecked == "" || ans.parsed["summary"] != "ยอดเท่ากัน {revenue}" {
		t.Fatalf("the answer used (the second) could not be checked, and the record says so: %v %q %v", err, ans.unchecked, ans.parsed)
	}
	// an answer only too long is not read until it is the one used: the clean second answer is the only one read
	calls = 0
	long := make([]string, 9)
	for i := range long {
		long[i] = "ข้อ " + strings.Repeat("ก", i+1)
	}
	long[3] = strings.Repeat("ยาวเกินไป", 8)
	ans, _, _, err = am24Ask(t, flaky, am.Thai, am22Answer(long, "สรุป {revenue}"), `{"summary":"ยอดเท่ากัน {revenue}"}`)
	if err != nil || calls != 1 || ans.unchecked == "" {
		t.Fatalf("one reading, of the answer used (the engine was busy for it): %v %d %q", err, calls, ans.unchecked)
	}
	calls = 1 // (the engine reads the next one)
	ans, _, _, err = am24Ask(t, flaky, am.Thai, `{"summary":"ยอดเท่ากัน {revenue}"}`)
	if err != nil || ans.unchecked != "" {
		t.Fatalf("the answer used was checked; the record must not say otherwise: %v %q", err, ans.unchecked)
	}
	rc := &runCtx{r: &Runner{}}
	if _, err := rc.engineMarks(context.Background(), []string{"ร่่าง"}); err == nil || !strings.Contains(err.Error(), "no engine") {
		t.Fatalf("a runner with no sandbox says it could not check: %v", err)
	}
}

// An answer with no Thai is never sent to the engine; an English run's reason is English.
func TestAnAnswerWithNoThaiIsNotCheckedAndAnEnglishRunsReasonIsEnglish(t *testing.T) {
	var calls int32
	if _, _, _, err := am24Ask(t, am24Marks(&calls), am.English, `{"summary":"Revenue was {revenue}."}`); err != nil || calls != 0 {
		t.Fatalf("no Thai, no engine call: %v %d", err, calls)
	}
	_, _, tried, _ := am24Ask(t, am24Marks(&calls), am.English, `{"summary":"Paid to ร่่าง {revenue}."}`, `{"summary":"Revenue was {revenue}."}`)
	if len(tried) != 1 || !strings.Contains(tried[0], "as Thai never writes it") {
		t.Fatalf("an English run is told in English: %q", tried)
	}
}

// The wiring (CARD-RULES §6d): the narrative step hands askNarrative the runner's engine to read the Thai with. The
// rule above is only as good as this call — given nil, every answer passes unread (am24_it_test.go shows it red).
func TestTheNarrativeStepHasItsThaiReadByTheEngine(t *testing.T) {
	src, err := os.ReadFile("deliver.go")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(src), "askNarrative(ctx, rc.r.Model, l, models, rc.pkg.Instructions, user, cv, rc.engineMarks)") {
		t.Fatal("the narrative step no longer asks the engine to read its commentary's Thai")
	}
	if !strings.Contains(mustRead(t, "marks_am24.go"), `"op": "thai_marks"`) {
		t.Fatal("the engine is not asked for thai_marks")
	}
}

func mustRead(t *testing.T, f string) string {
	t.Helper()
	b, err := os.ReadFile(f)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
