// FIX107 Part B — two model-facing texts become canonical assets.
package service

import (
	"strings"
	"testing"
)

// The web prompt must not move when its format rule becomes a file: these are
// the bytes the Go literal held before FIX107.
func TestFIX107_TheWebOutputFormatLayerIsByteForByteWhatItWas(t *testing.T) {
	const before = `<output_format>
- Choose the format that fits the content; do not default to bullet points.
- Use a markdown table for structured or comparative data — comparisons, test results, feature lists, status updates, anything with 2+ attributes across multiple items.
- Use a numbered list for sequential steps, prose for explanations and discussion, and code blocks for code.
</output_format>`
	if outputFormatBlock != before {
		t.Fatalf("the web's output format layer changed:\n%q\nwant\n%q", outputFormatBlock, before)
	}
	if got := (&PromptCompiler{}).outputFormat(CompileContext{}); got != before {
		t.Fatalf("the compiled layer is not the asset: %q", got)
	}
}

func TestFIX107_TheContextNoteAssetHasEverySectionAndFillsEveryPlaceholder(t *testing.T) {
	for _, name := range []string{"OPEN", "HEAD_KEPT", "HEAD_DROPPED", "SUMMARY_COMPLETE", "SUMMARY_PARTIAL", "SUMMARY_FAILED", "SUMMARY_OFF", "CLOSE"} {
		if strings.TrimSpace(contextNoteParts[name]) == "" {
			t.Fatalf("prompt_context_note.txt has no %s section", name)
		}
	}
	for _, status := range []string{SummaryComplete, SummaryPartial, SummaryFailed, SummaryOff, SummaryPending} {
		for _, head := range []int{0, 2} {
			note := compactionContextNote(7, 3, head, status)
			if strings.ContainsAny(note, "{}") {
				t.Fatalf("%s/head=%d: a placeholder was left unfilled:\n%s", status, head, note)
			}
			if !strings.HasPrefix(note, "\n\n<context_note>\n") || !strings.HasSuffix(note, "\n</context_note>") {
				t.Fatalf("%s/head=%d: the note lost its tags:\n%s", status, head, note)
			}
		}
	}
}

// The owner's chat: 1 of 18 messages in view, the first exchange out of view,
// a summary written. The note must say the earliest message in view is not the
// start, and what to do when asked about the start.
func TestFIX107_WhenTheStartIsOutOfViewTheNoteSaysSo(t *testing.T) {
	note := compactionContextNote(17, 1, 0, SummaryComplete)
	for _, want := range []string{
		"17 earlier messages are not included here",
		"You can see only the most recent 1 message, so the earliest message you can see is not where the conversation began.",
		"it is a summary, not the messages themselves",
		"Never present a message you can see as the first one",
		"For anything else, answer normally and do not mention this note.",
	} {
		if !strings.Contains(note, want) {
			t.Fatalf("missing %q in:\n%s", want, note)
		}
	}
	if kept := compactionContextNote(17, 4, 2, SummaryComplete); strings.Contains(kept, "not where the conversation began") {
		t.Fatalf("with the first exchange in view the note must not say the start is missing:\n%s", kept)
	}
}
