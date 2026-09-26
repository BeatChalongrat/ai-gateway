package agentmarket

import (
	"encoding/json"
	"testing"
)

// AM2 — a question that takes a typed value (an E2 money / tax ID / date card):
// the words typed in the card's own box are the answer, as {"typed": …}, and are
// never sent to keyword or model interpretation. A question without an input
// keeps the old path (free text → interpreting).
func TestATypedValueIsTheAnswerNotAnInterpretation(t *testing.T) {
	money := Question{Key: "e2_low_confidence:d1:total", Code: "e2_low_confidence", Severity: "blocking",
		Options: []Option{{Label: "ถูกต้อง 12,850.70", Value: json.RawMessage(`{"accept":"12850.70"}`)},
			{Label: "ตัดเอกสารนี้ออก", Value: json.RawMessage(`"exclude"`)}},
		Input: json.RawMessage(`{"kind":"money"}`)}
	plain := Question{Key: "period", Code: "period", Severity: "blocking",
		Options: []Option{{Label: "ก.ย.", Value: json.RawMessage(`"2026-09"`)}, {Label: "ส.ค.", Value: json.RawMessage(`"2026-08"`)}}}

	answers, texts, err := ResolveSubmission([]Question{money, plain}, map[string]Submitted{
		money.Key: {Text: " 12,580.70 "},
		plain.Key: {Text: "เดือนกันยายน"},
	}, "preflight", "card-1")
	if err != nil {
		t.Fatal(err)
	}
	a, ok := answers[money.Key]
	if !ok {
		t.Fatalf("typed value not taken as the answer; texts=%v", texts)
	}
	if string(a.Value) != `{"typed":"12,580.70"}` || a.Via != "typed" || a.Label != "12,580.70" {
		t.Fatalf("typed answer = %s via %s label %q", a.Value, a.Via, a.Label)
	}
	if _, sent := texts[money.Key]; sent {
		t.Fatal("a typed value went to interpretation")
	}
	if texts[plain.Key] != "เดือนกันยายน" {
		t.Fatalf("a question without an input must keep the free-text path: %v", texts)
	}

	// the card's "something else" box sends the words as a label that is not an option
	answers, _, _ = ResolveSubmission([]Question{money}, map[string]Submitted{money.Key: {Labels: []string{"12,850.00"}}}, "preflight", "c")
	if string(answers[money.Key].Value) != `{"typed":"12,850.00"}` {
		t.Fatalf("box answer = %s", answers[money.Key].Value)
	}
	// clicking an option is still the option
	answers, _, _ = ResolveSubmission([]Question{money}, map[string]Submitted{money.Key: {Labels: []string{"ตัดเอกสารนี้ออก"}}}, "preflight", "c")
	if string(answers[money.Key].Value) != `"exclude"` {
		t.Fatalf("option answer = %s", answers[money.Key].Value)
	}
	// a finding's input reaches the card question
	f := Finding{Key: "k", Code: "c", Severity: "blocking", Header: "ยอดรวม", Question: "q?",
		Options: money.Options, Default: json.RawMessage(`"exclude"`), Input: json.RawMessage(`{"kind":"money"}`)}
	if q := questionOf(f); string(q.Input) != `{"kind":"money"}` {
		t.Fatalf("input lost on the way to the card: %s", q.Input)
	}
}
