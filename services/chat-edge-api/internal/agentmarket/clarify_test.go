package agentmarket

import (
	"encoding/json"
	"fmt"
	"testing"
)

func f(key, sev string) Finding {
	return Finding{
		Key: key, Code: key, Severity: sev, Moment: "preflight", Header: "หัวข้อ", Question: "ถาม " + key,
		Options: []Option{
			{Label: "ตัดออก ใช้เฉพาะ ก.ย.", Value: json.RawMessage(`"exclude"`)},
			{Label: "นับรวมด้วย", Value: json.RawMessage(`"include"`)},
		},
		Default: json.RawMessage(`"exclude"`), Kind: "question", Rememberable: true,
		Evidence: json.RawMessage(`{"rows":[]}`),
	}
}

func plan(t *testing.T, in PlanInput) PlanResult {
	t.Helper()
	res, err := Plan(in)
	if err != nil {
		t.Fatal(err)
	}
	return res
}

func TestCleanInputAsksNothing(t *testing.T) {
	res := plan(t, PlanInput{Moment: "preflight"})
	if res.Card != nil || res.Park || len(res.AutoAnswers) != 0 {
		t.Fatalf("clean input must be silent: %+v", res)
	}
}

func TestFiveFindingsOneCardOfFourPlusAssumption(t *testing.T) {
	in := PlanInput{Moment: "preflight", Findings: []Finding{
		f("a", "blocking"), f("b", "cosmetic"), f("c", "blocking"), f("d", "cosmetic"), f("e", "blocking"),
	}}
	res := plan(t, in)
	if res.Card == nil || len(res.Card.Questions) != 4 {
		t.Fatalf("want one card of 4, got %+v", res.Card)
	}
	// blocking first, in check order; then the first cosmetic.
	got := []string{}
	for _, q := range res.Card.Questions {
		got = append(got, q.Key)
	}
	if fmt.Sprint(got) != "[a c e b]" {
		t.Fatalf("order %v", got)
	}
	if !res.Park || !res.Card.Blocking {
		t.Fatal("a card with blocking questions parks the run")
	}
	d, ok := res.AutoAnswers["d"]
	if !ok || d.Via != "default" {
		t.Fatalf("leftover cosmetic must take its default: %+v", res.AutoAnswers)
	}
	found := false
	for _, a := range res.Assumptions {
		if a.Code == "d" && a.Source == "default" {
			found = true
		}
	}
	if !found {
		t.Fatal("the defaulted cosmetic finding must be written as an assumption")
	}
}

func TestFiveBlockingTwoCardsNoneDefaulted(t *testing.T) {
	all := []Finding{f("a", "blocking"), f("b", "blocking"), f("c", "blocking"), f("d", "blocking"), f("e", "blocking")}
	res := plan(t, PlanInput{Moment: "preflight", Findings: all})
	if len(res.Card.Questions) != 4 || len(res.AutoAnswers) != 0 {
		t.Fatalf("first card: 4 questions and nothing defaulted, got %d / %v", len(res.Card.Questions), res.AutoAnswers)
	}
	// While card 1 is open, no second card appears.
	open := map[string]bool{"a": true, "b": true, "c": true, "d": true}
	res2 := plan(t, PlanInput{Moment: "preflight", Findings: all, Asked: open, OpenKeys: open})
	if res2.Card != nil || !res2.Park {
		t.Fatalf("second card must wait for the first: %+v", res2)
	}
	// Card 1 answered → the fifth blocking finding gets its own card.
	answers := map[string]Answer{}
	for k := range open {
		answers[k] = Answer{Value: json.RawMessage(`"exclude"`), Via: "card"}
	}
	res3 := plan(t, PlanInput{Moment: "preflight", Findings: all, Answers: answers, Asked: open})
	if res3.Card == nil || len(res3.Card.Questions) != 1 || res3.Card.Questions[0].Key != "e" || len(res3.AutoAnswers) != 0 {
		t.Fatalf("second card must ask e, nothing defaulted: %+v", res3)
	}
}

func TestSameKeyNeverAskedTwice(t *testing.T) {
	asked := map[string]bool{"a": true}
	res := plan(t, PlanInput{Moment: "postcompute", Findings: []Finding{f("a", "blocking")}, Asked: asked})
	if res.Card != nil {
		t.Fatal("a key already asked in this run must not be asked again")
	}
}

func TestRememberedAnswerAppliedSilently(t *testing.T) {
	res := plan(t, PlanInput{Moment: "preflight", Findings: []Finding{f("a", "blocking")},
		Remembered: map[string]json.RawMessage{"a": json.RawMessage(`"exclude"`)}})
	if res.Card != nil || res.Park {
		t.Fatal("remembered answer must not ask")
	}
	if res.AutoAnswers["a"].Via != "remembered" || len(res.Assumptions) != 1 || res.Assumptions[0].Source != "remembered" {
		t.Fatalf("%+v", res)
	}
}

func TestRememberedValueThatIsNoLongerAnOptionIsAsked(t *testing.T) {
	res := plan(t, PlanInput{Moment: "preflight", Findings: []Finding{f("a", "blocking")},
		Remembered: map[string]json.RawMessage{"a": json.RawMessage(`"gone"`)}})
	if res.Card == nil {
		t.Fatal("a stale remembered value must fall back to asking")
	}
}

func TestCosmeticOnlyCardDoesNotPark(t *testing.T) {
	res := plan(t, PlanInput{Moment: "preflight", Findings: []Finding{f("x", "cosmetic")}})
	if res.Card == nil || res.Card.Blocking || res.Park {
		t.Fatalf("cosmetic-only card must not park: %+v", res)
	}
	if res.AutoAnswers["x"].Via != "default" {
		t.Fatal("the run continues on the default")
	}
}

func TestRequestFilesComesFirstAndAlone(t *testing.T) {
	rf := Finding{Key: "missing_file", Code: "missing_file", Severity: "blocking", Header: "ขาดไฟล์",
		Question: "ยังไม่มี GL", Kind: "request_files", Request: json.RawMessage(`[{"role":"primary"}]`),
		Options: f("z", "blocking").Options, Default: json.RawMessage(`"exclude"`)}
	res := plan(t, PlanInput{Moment: "preflight", Findings: []Finding{f("a", "blocking"), rf}})
	if res.Card == nil || res.Card.Kind != "request_files" || len(res.Card.Questions) != 1 || !res.Park {
		t.Fatalf("%+v", res.Card)
	}
}

func TestMalformedFindingIsRefused(t *testing.T) {
	bad := f("a", "blocking")
	bad.Options = bad.Options[:1]
	if _, err := Plan(PlanInput{Findings: []Finding{bad}}); err == nil {
		t.Fatal("one option must be refused")
	}
	bad = f("a", "blocking")
	bad.Header = "หัวข้อที่ยาวเกินกว่าสิบสอง"
	if _, err := Plan(PlanInput{Findings: []Finding{bad}}); err == nil {
		t.Fatal("long header must be refused")
	}
	bad = f("a", "blocking")
	bad.Question = "กรุณาพิมพ์รหัสผ่านของระบบบัญชี"
	if _, err := Plan(PlanInput{Findings: []Finding{bad}}); err == nil {
		t.Fatal("a card must never ask for a secret")
	}
}

func TestResolveSubmissionByLabelAndText(t *testing.T) {
	qs := []Question{questionOf(f("a", "blocking")), questionOf(f("b", "blocking"))}
	ans, texts, err := ResolveSubmission(qs, map[string]Submitted{
		"a": {Labels: []string{"นับรวมด้วย"}},
		"b": {Text: "ตัดออกไปเลยครับ"},
	}, "preflight", "card-1")
	if err != nil {
		t.Fatal(err)
	}
	if string(ans["a"].Value) != `"include"` || ans["a"].CardID != "card-1" {
		t.Fatalf("%+v", ans)
	}
	if texts["b"] == "" {
		t.Fatal("free text goes to interpretation")
	}
}

func TestSkippedAnswerIsRecordedAsASkip(t *testing.T) {
	qs := []Question{questionOf(f("a", "blocking"))}
	ans, _, err := ResolveSubmission(qs, map[string]Submitted{"a": {Labels: []string{"ตัดออก ใช้เฉพาะ ก.ย."}, Skipped: true}}, "preflight", "c")
	if err != nil || ans["a"].Via != "skip" {
		t.Fatalf("a skip must be recorded as a skip, not as a choice: %+v %v", ans, err)
	}
}

func TestInterpretText(t *testing.T) {
	q := questionOf(f("a", "blocking"))
	if o, ok := InterpretText(q, "ตัดออกครับ ใช้เฉพาะกันยายน"); !ok || string(o.Value) != `"exclude"` {
		t.Fatalf("exclude: %v %v", o, ok)
	}
	if o, ok := InterpretText(q, "นับรวมด้วยครับ"); !ok || string(o.Value) != `"include"` {
		t.Fatalf("include: %v %v", o, ok)
	}
	if _, ok := InterpretText(q, "ขอคิดก่อนนะ"); ok {
		t.Fatal("unrelated text must not match")
	}
}

func TestDisplayLenIgnoresThaiMarks(t *testing.T) {
	if n := DisplayLen("เดบิต/เครดิต"); n != 10 {
		t.Fatalf("got %d", n)
	}
}
