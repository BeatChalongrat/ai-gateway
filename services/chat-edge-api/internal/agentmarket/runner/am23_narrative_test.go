package runner

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"unicode"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM23 §3.3: what makes a commentary read machine-made, held by the validator. The points are AM22's own (its model run
// over every eval case, scripts/probes/AM22/results/model): a point that only says again what the round's facts say is
// padding; one that adds what the reader can use is not.

func am23Facts() computedView {
	return computedView{
		NarrativeFacts: []narrativeFact{
			{Heading: "การจับคู่", Lines: []string{"จับคู่ได้ {matched} คู่ โดยกฎ 10 คู่ โดยคน 2 คู่",
				"รายการกระทบยอด {items} รายการ: ขาด 3 เกิน 4 น่าสงสัย 0 ร่างรายการปรับปรุง 4 รายการ"}},
			{Heading: "Matching", Lines: []string{"Matched {matched} pairs: by rule 10, by a person 0",
				"Reconciling items {items}: missing 3, extra 4, suspect 0, draft adjusting entries 4"}},
		},
		Figures: map[string]figureView{"matched": {Text: "10", Cite: "n1", Label: "จำนวนคู่ที่จับได้"},
			"items": {Text: "7", Cite: "n2", Label: "จำนวนรายการกระทบยอด"}},
	}
}

func TestAPointThatOnlyRestatesTheFactsIsPaddingAndOneThatAddsIsNot(t *testing.T) {
	cv := am23Facts()
	known := knownToTheReader(cv, "")
	for _, c := range []struct {
		l     am.Lang
		part  string
		point string
		pad   bool
	}{
		{am.Thai, "movements", "รายการกระทบยอดมี {items} รายการ แยกเป็นขาด 3 รายการ เกิน 4 รายการ และน่าสงสัย 0 รายการ", true},
		{am.Thai, "movements", "จับคู่ได้ {matched} คู่ เป็นการจับคู่โดยกฎทั้งหมด ไม่มีคู่ที่คนยืนยัน", true},
		{am.Thai, "watch", "รายการขาด 3 รายการเป็นเช็คที่ผู้รับยังไม่นำไปขึ้นเงิน ขอ statement เดือนถัดไปมายืนยัน", false},
		{am.English, "movements", "10 pairs were matched, all of them by rule and none by a person.", true},
		{am.English, "watch", "Two outstanding cheques are older than sixty days; confirm with the payee before the close.", false},
		// a next step is something to do: never padding, however short
		{am.Thai, "next_steps", "ตรวจร่างรายการปรับปรุง", false},
		{am.English, "next_steps", "Review the draft adjusting entries.", false},
	} {
		if got := isPadding(c.l, c.part, c.point, known); got != c.pad {
			t.Errorf("%s %s %q: padding=%v (novelty %d)", c.l, c.part, c.point, got, novelty(c.l, c.point, known))
		}
	}
}

func am23Answer(t *testing.T, v map[string]any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestPaddingIsAskedForOnceWithItsPlaceNeverCut(t *testing.T) {
	cv := am23Facts()
	ans := am23Answer(t, map[string]any{"summary": "Matched {matched} pairs; {items} items are still open.",
		"movements": []string{"10 pairs were matched, all of them by rule and none by a person.",
			"Two outstanding cheques are older than sixty days; confirm with the payee before the close."},
		"watch": []string{}, "next_steps": []string{"Review the draft adjusting entries."}})
	v, err := checkNarrative(am.English, ans, cv)
	var soft *tooLongErr
	if !errors.As(err, &soft) || !strings.Contains(err.Error(), "point 1 of movements only restates") || strings.Contains(err.Error(), "point 2") {
		t.Fatalf("the padded point is named and sent back, the other is not: %v", err)
	}
	if len(v["movements"].([]any)) != 2 {
		t.Fatal("nothing is cut")
	}
}

func TestEveryAgentsHedgesAreRefusedInBothLanguagesWithWhatToWriteInstead(t *testing.T) {
	for _, pkg := range []string{"bank_reconciliation", "expense_audit", "invoice_processing", "po_matching", "financial_report", "procurement"} {
		m := am23Manifest(t, pkg)
		th, en := 0, 0
		for _, h := range m.NarrativeHedges {
			if strings.IndexFunc(h, func(r rune) bool { return unicode.Is(unicode.Thai, r) }) >= 0 {
				th++
			} else {
				en++
			}
			cv := am23Facts()
			cv.NarrativeHedges = m.NarrativeHedges
			ans := am23Answer(t, map[string]any{"summary": "Matched {matched} pairs.", "movements": []string{},
				"watch": []string{"The cheques of {items} items " + h + " before the close of the period."}, "next_steps": []string{}})
			_, err := checkNarrative(am.English, ans, cv)
			var soft *tooLongErr
			if err == nil || errors.As(err, &soft) || !strings.Contains(err.Error(), "is not something a reader can do") {
				t.Fatalf("%s: %q is refused, not asked for softly: %v", pkg, h, err)
			}
		}
		if th == 0 || en == 0 {
			t.Fatalf("%s declares its hedges in both languages: %d Thai, %d English", pkg, th, en)
		}
	}
}

func TestEachPartsHeadingIsWrittenFromWhatItFound(t *testing.T) {
	cv := am23Facts()
	cv.WritingLimits = map[string]int{"heading_chars": 40}
	cv.HeadingsGeneric = map[string]string{"summary": "Summary", "movements": "Key points", "watch": "What to look at", "next_steps": "What to do next"}
	base := map[string]any{"summary": "Matched {matched} pairs; {items} items are still open.",
		"movements": []string{"Two outstanding cheques are older than sixty days; confirm with the payee before the close."},
		"watch":     []string{}, "next_steps": []string{"Review the draft adjusting entries."}}
	with := func(h map[string]any) string {
		v := map[string]any{}
		for k, x := range base {
			v[k] = x
		}
		if h != nil {
			v["headings"] = h
		}
		return am23Answer(t, v)
	}
	// none, or the generic ones: each part that found something is asked for its own
	_, err := checkNarrative(am.English, with(nil), cv)
	for _, part := range []string{"summary", "movements", "next_steps"} {
		if err == nil || !strings.Contains(err.Error(), "the heading of "+part) {
			t.Fatalf("no heading for %s: %v", part, err)
		}
	}
	if strings.Contains(err.Error(), "the heading of watch") {
		t.Fatal("a part that found nothing may keep its generic heading")
	}
	_, err = checkNarrative(am.English, with(map[string]any{"summary": "summary", "movements": "Key points", "next_steps": "Two cheques to confirm"}), cv)
	if err == nil || !strings.Contains(err.Error(), "the heading of summary") || !strings.Contains(err.Error(), "the heading of movements") ||
		strings.Contains(err.Error(), "the heading of next_steps") {
		t.Fatalf("the generic heading, whatever its case, is not one: %v", err)
	}
	// written from what was found, within the title: kept with the answer
	v, err := checkNarrative(am.English, with(map[string]any{"summary": "{items} items still open", "movements": "Two cheques over sixty days",
		"next_steps": "Two cheques to confirm"}), cv)
	if err != nil {
		t.Fatal(err)
	}
	if hs := v["headings"].(map[string]string); hs["summary"] != "{items} items still open" {
		t.Fatalf("kept: %v", hs)
	}
	// too long for the title: asked again; a figure the table does not have, or a typed one: refused as in a sentence
	_, err = checkNarrative(am.English, with(map[string]any{"summary": strings.Repeat("a long heading ", 4), "movements": "Two cheques",
		"next_steps": "Two cheques to confirm"}), cv)
	if err == nil || !strings.Contains(err.Error(), "the heading of summary is 59 characters, over 40") {
		t.Fatalf("too long: %v", err)
	}
	_, err = checkNarrative(am.English, with(map[string]any{"summary": "{nothing} still open", "movements": "Two cheques",
		"next_steps": "Two cheques to confirm"}), cv)
	var soft *tooLongErr
	if err == nil || errors.As(err, &soft) {
		t.Fatalf("a figure the table does not have is refused: %v", err)
	}
	// a package whose documents print no heading (no generic ones in its table) is not asked for any
	cv.HeadingsGeneric = nil
	if _, err := checkNarrative(am.English, with(nil), cv); err != nil {
		t.Fatalf("not asked: %v", err)
	}
}

func TestTheModelIsToldTheHeadingsLimit(t *testing.T) {
	cv := computedView{WritingLimits: map[string]int{"summary_chars": 480, "bullet_chars": 110, "bullets_max": 4, "items_max": 20, "heading_chars": 60}}
	if !strings.Contains(writingLimitsLine(am.English, cv), "each heading up to 60") || !strings.Contains(writingLimitsLine(am.Thai, cv), "หัวข้อแต่ละหัวข้อไม่เกิน 60") {
		t.Fatal(writingLimitsLine(am.English, cv))
	}
}
