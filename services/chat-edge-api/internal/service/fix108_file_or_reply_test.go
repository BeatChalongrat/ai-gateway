// FIX108 Part B — when an answer becomes a file.
package service

import (
	"context"
	"strings"
	"testing"
)

func TestFIX108_TheFileOrReplyLayerStatesTheCriteriaAndKeepsRequestedFiles(t *testing.T) {
	for _, want := range []string{
		"Answer in the chat by default.",
		"The user asks for a file or names a format",
		"Then make exactly that file.",
		"typically more than 15 lines",
		"likely to edit, reuse or share outside this conversation",
		"stand on its own without the conversation around it",
		`even when the user says "write" (เขียน): a short essay`,
	} {
		if !strings.Contains(fileOrReplyBlock, want) {
			t.Fatalf("prompt_file_or_reply.txt lost %q", want)
		}
	}
}

func TestFIX108_TheWebPromptCarriesItRightAfterTheFormatRule(t *testing.T) {
	pc := newPromptCompiler(stubPrefsReader{block: ""}, nil, nil)
	out, _ := pc.Build(context.Background(), CompileContext{})
	f, o := strings.Index(out, "<file_or_reply>"), strings.Index(out, "<output_format>")
	if f < 0 || o < 0 || f < o {
		t.Fatalf("want <file_or_reply> after <output_format>: file=%d format=%d", f, o)
	}
}

// The plan contracts no longer claim every "generate" request: they point at
// the layer that says when a file is called for.
func TestFIX108_ThePlanContractsNoLongerClaimEveryGenerateRequest(t *testing.T) {
	for name, text := range map[string]string{"excel": excelCapabilitiesPrompt, "doc": docCapabilitiesPrompt} {
		if strings.Contains(text, "For ANY edit / generate") || strings.Contains(text, "For ANY create/edit/generate") {
			t.Fatalf("%s contract still says every generate request gets a plan", name)
		}
		if !strings.Contains(text, "see <file_or_reply> for when that is") {
			t.Fatalf("%s contract does not point at <file_or_reply>", name)
		}
	}
}
