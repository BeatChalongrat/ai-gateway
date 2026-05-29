package service

import (
	"strings"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
)

// catalogIDs mirrors the operator's default catalog so the Auto
// Thinking Mode assertions exercise the real ordering (o3-mini ahead
// of the keyless Claude row).
func catalogIDs() []string {
	out := make([]string, 0, len(config.DefaultModels))
	for _, m := range config.DefaultModels {
		out = append(out, m.ID)
	}
	return out
}

func TestIsComplexQuery(t *testing.T) {
	cases := []struct {
		name         string
		message      string
		hasFiles     bool
		hasImages    bool
		messageCount int
		want         bool
	}{
		{"greeting", "hello there", false, false, 1, false},
		{"thai greeting", "สวัสดีครับ", false, false, 1, false},
		{"short lookup", "what time is it", false, false, 1, false},
		{"analyze english", "Analyze the tradeoffs between microservices and monolith", false, false, 1, true},
		{"thai analyze", "ช่วยวิเคราะห์ข้อดีข้อเสียของสองทางเลือกนี้", false, false, 1, true},
		{"code block", "fix this:\n```go\nfunc main(){}\n```", false, false, 1, true},
		{"three questions", "why? how? when?", false, false, 1, true},
		{"has image", "what's this", false, true, 1, true},
		{"has files", "summarize", true, false, 1, true},
		{"long convo", "ok", false, false, 31, true},
		{"long message", strings.Repeat("word ", 120), false, false, 1, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := isComplexQuery(c.message, c.hasFiles, c.hasImages, c.messageCount)
			if got != c.want {
				t.Fatalf("isComplexQuery(%q)=%v, want %v", c.message, got, c.want)
			}
		})
	}
}

// TestAutoThinkingPrefersWorkingReasoningModel is the core Auto
// Thinking Mode contract: a complex turn resolves to a reasoning model
// (o3-mini — the first reasoning-capable id in the catalog and the one
// with a valid key here), while a simple turn stays on the fast tier.
func TestAutoThinkingPrefersWorkingReasoningModel(t *testing.T) {
	ids := catalogIDs()

	complexMsg := "Analyze step by step the tradeoffs between microservices and a monolith"
	prefer := isComplexQuery(complexMsg, false, false, 1)
	if !prefer {
		t.Fatal("expected complex query to be flagged complex")
	}
	got := ResolveAutoModelWithReasoning(complexMsg, false, false, 1, ids, prefer)
	if got != "o3-mini" {
		t.Fatalf("complex auto turn resolved to %q, want o3-mini", got)
	}
	if !isReasoningCapableID(got) {
		t.Fatalf("resolved model %q is not reasoning-capable", got)
	}

	simpleMsg := "hello there"
	preferSimple := isComplexQuery(simpleMsg, false, false, 1)
	if preferSimple {
		t.Fatal("expected simple query NOT to be flagged complex")
	}
	gotSimple := ResolveAutoModelWithReasoning(simpleMsg, false, false, 1, ids, preferSimple)
	if isReasoningCapableID(gotSimple) {
		t.Fatalf("simple auto turn resolved to reasoning model %q, want a fast model", gotSimple)
	}
	if gotSimple != "gpt-4o-mini" {
		t.Fatalf("simple auto turn resolved to %q, want gpt-4o-mini", gotSimple)
	}
}
