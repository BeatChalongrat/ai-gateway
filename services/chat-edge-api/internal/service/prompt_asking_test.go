package service

import (
	"strings"
	"testing"
)

// FIX73 — the web path has no question card (the desktop bridge does), so
// the model asks in prose there. What keeps that rare is the action_rules
// layer: ask only about the user's preference or intent (scope, audience,
// format, destination), never about what can be looked up or read, and
// address the request before asking. Removing any of these lines fails here.
func TestActionRules_AskOnlyAboutPreferenceOrIntent(t *testing.T) {
	pc := &PromptCompiler{}
	got := pc.actionRules(CompileContext{})
	for _, want := range []string{
		"Ask at most one clarifying question per response",
		"Ask ONLY about a matter of the user's preference or intent that you cannot determine yourself",
		"the scope, the audience, the format, or the destination of the work",
		"NEVER ask about something you could look up, read from the conversation or the attached files, or reasonably assume",
		"Try to address even an ambiguous request before asking",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("action_rules missing %q:\n%s", want, got)
		}
	}
}
