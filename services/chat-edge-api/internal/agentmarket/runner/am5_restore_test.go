package runner

import (
	"encoding/json"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM5 — a document brought back from "เอกสารที่ยังไม่ได้รวม" whose question has
// to be answered after all: the question is asked again. Every other
// question is still asked once per run.

func cardOf(status string, keys ...string) am.Card {
	var qs []am.Question
	for _, k := range keys {
		qs = append(qs, am.Question{Key: k})
	}
	b, _ := json.Marshal(qs)
	return am.Card{Status: status, Questions: b}
}

func TestAQuestionTakenBackIsAskedAgain(t *testing.T) {
	answers := map[string]am.Answer{"e2_check_failed:a:vat_7_percent": {Value: json.RawMessage(`"accept_as_printed"`)}}
	cards := []am.Card{
		cardOf("answered", "e2_check_failed:a:vat_7_percent"),
		cardOf("superseded", "e2_field_missing:b:seller_tax_id"), // brought back: its answer was taken off the run
		cardOf("open", "e2_low_confidence:c:total"),
	}
	asked, open := askedKeys(cards, answers)
	if !asked["e2_check_failed:a:vat_7_percent"] || !asked["e2_low_confidence:c:total"] {
		t.Fatalf("a question is asked once per run: %v", asked)
	}
	if asked["e2_field_missing:b:seller_tax_id"] {
		t.Fatalf("the question of the document that came back is asked again: %v", asked)
	}
	if !open["e2_low_confidence:c:total"] || len(open) != 1 {
		t.Fatalf("open: %v", open)
	}
	// once it has been asked again, it is asked
	cards = append(cards, cardOf("open", "e2_field_missing:b:seller_tax_id"))
	if asked, _ = askedKeys(cards, answers); !asked["e2_field_missing:b:seller_tax_id"] {
		t.Fatal("asked again once, not for ever")
	}
	// a card superseded by a re-ask whose answer is on the run stays asked
	cards = []am.Card{cardOf("superseded", "period_mixed")}
	if asked, _ = askedKeys(cards, map[string]am.Answer{"period_mixed": {Value: json.RawMessage(`"exclude"`)}}); !asked["period_mixed"] {
		t.Fatal("an answered question is never asked twice")
	}
}
