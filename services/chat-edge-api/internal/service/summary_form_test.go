// FIX106 — what shipped of the summary work, after the recall probe said the
// verbatim form did not help: the extraction request is cut on a character
// boundary, carries placeholders as written, and a summary value that is not a
// string no longer hides the whole summary.
package service

import (
	"context"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func TestFIX106_ABodyCutsOnACharacterBoundaryAndStaysValidUTF8(t *testing.T) {
	long := strings.Repeat("ก", 700) // 3 bytes each
	body := (&ConversationStateService{}).buildExtractionPrompt(nil, []model.Message{{Role: "user", ContentMasked: long}}, "")
	if !utf8.ValidString(body) {
		t.Fatal("the body must never cut inside a character (it used to)")
	}
	if got := strings.Count(body, "ก"); got != extractionMessageLimit/3 {
		t.Fatalf("kept %d Thai characters, want %d: the budget stays 500 bytes", got, extractionMessageLimit/3)
	}
	short := strings.Repeat("a", extractionMessageLimit)
	if b := (&ConversationStateService{}).buildExtractionPrompt(nil, []model.Message{{Role: "user", ContentMasked: short}}, ""); strings.Contains(b, "[truncated]") {
		t.Fatal("a message within the limit is sent whole")
	}
}

func TestFIX106_APlaceholderInTheExistingStateReachesTheModelAsWritten(t *testing.T) {
	goal := "g"
	existing := &model.ConversationState{CurrentGoal: &goal, StructuredSummary: []byte(`{"key_context":"call <<PHONE_1>> & ask"}`)}
	body := (&ConversationStateService{}).buildExtractionPrompt(existing, []model.Message{{Role: "user", ContentMasked: "hi"}}, "")
	// The escapes json.Marshal would have written: a backslash, then u003c
	// for "<" and u0026 for "&" (built here so no escape sits in this file).
	esc := string(rune(92)) + "u00"
	if !strings.Contains(body, `<<PHONE_1>> & ask`) || strings.Contains(body, esc+"3c") || strings.Contains(body, esc+"26") {
		t.Fatalf("the existing state must carry the placeholder unescaped:\n%s", body)
	}
}

func TestFIX106_ASummaryValueThatIsAListNoLongerHidesTheWholeSummary(t *testing.T) {
	// Before FIX106 the renderer decoded structured_summary into
	// map[string]string: one non-string value failed the decode and every key
	// disappeared from the prompt.
	st := &fakeStates{row: &model.ConversationState{StructuredSummary: []byte(`{"key_context":["37 servers","- step 3 moves the bucket"],"progress":"agreed the steps"}`)}}
	svc := newConversationStateServiceWith(st, &fakeMessages{}, fakeConvs{}, &fakeGateway{})
	text, _ := svc.BuildStateContextWithSources(context.Background(), uuid.New(), uuid.New(), uuid.New())
	for _, want := range []string{"Key context: - 37 servers\n- step 3 moves the bucket", "Progress: agreed the steps"} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing %q:\n%s", want, text)
		}
	}
}
