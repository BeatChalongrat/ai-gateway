package runner

import (
	"strings"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM21 §6.1: the model is told the layouts' limits before it writes, in the prompt — and an engine
// from before this card, which sends none, leaves the prompt as it was.
func TestTheModelIsToldTheLayoutsLimitsBeforeItWrites(t *testing.T) {
	cv := computedView{PeriodLabel: "กันยายน 2569", Figures: map[string]figureView{"revenue": {Text: "12,480,300", Cite: "c61", Label: "รายได้"}},
		WritingLimits: map[string]int{"summary_chars": 480, "bullet_chars": 110, "bullets_max": 4, "sentence_chars": 160}}
	p := narrativePrompt(am.Thai, cv)
	for _, want := range []string{"summary ไม่เกิน 480", "รายการละไม่เกิน 110 ตัวอักษร", "ไม่เกิน 4 รายการ"} {
		if !strings.Contains(p, want) {
			t.Fatalf("the prompt does not tell the model %q:\n%s", want, p)
		}
	}
	// the limits come before the final instruction, so the model reads them with the task
	if strings.Index(p, "summary ไม่เกิน 480") > strings.Index(p, "เขียนคำอธิบายตามรูปแบบ JSON") {
		t.Fatal("the limits are after the instruction")
	}
	old := computedView{PeriodLabel: "กันยายน 2569", Figures: cv.Figures}
	if q := narrativePrompt(am.Thai, old); strings.Contains(q, "ความยาวที่เอกสารรับได้") {
		t.Fatal("an engine that sends no limits must leave the prompt as it was")
	}
}

// AM21 §2: a file the renderer refused (its content fits no layout) fails the run naming the section,
// in the run's language, like any other failed check.
func TestAFormatTheRendererRefusedFailsTheRunNamingTheSection(t *testing.T) {
	var r refusedFormat
	r.Format, r.Name = "pptx", "deck_v1.pptx"
	r.Check.Label, r.Check.Detail = "เนื้อหาทุกส่วนพอดีกับเลย์เอาต์ที่เลือก", "ส่วน “ข้อสังเกต” ยาวเกินทุกเลย์เอาต์ในแค็ตตาล็อก (item 2000 > 165) จึงไม่เขียนไฟล์"
	lines := refusedLines(am.Thai, []refusedFormat{r})
	if len(lines) != 1 || !strings.HasPrefix(lines[0], "สไลด์: ") || !strings.Contains(lines[0], "ข้อสังเกต") {
		t.Fatalf("got %q", lines)
	}
	if got := refusedLines(am.Thai, nil); len(got) != 0 {
		t.Fatalf("nothing refused, nothing failed: %q", got)
	}
}
