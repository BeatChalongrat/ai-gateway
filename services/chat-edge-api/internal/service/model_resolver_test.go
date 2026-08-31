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

// catalogReasoningChecker is the catalog-injection fixture (models-
// catalog step 3): reasoning capability comes from the catalog entries'
// capability strings, exactly like the live ModelHandler.ReasoningCapable
// checker derives it from the gateway catalog — NOT from the static
// isReasoningCapableID set.
func catalogReasoningChecker() func(string) bool {
	capable := map[string]bool{}
	for _, m := range config.DefaultModels {
		for _, c := range m.Capabilities {
			if c == "reasoning" {
				capable[m.ID] = true
			}
		}
	}
	return func(id string) bool { return capable[id] }
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
// with a valid key here), while a simple turn defaults to Claude Sonnet
// (Fix 4) rather than the gpt-4o-mini fast tier.
func TestAutoThinkingPrefersWorkingReasoningModel(t *testing.T) {
	ids := catalogIDs()

	complexMsg := "Analyze step by step the tradeoffs between microservices and a monolith"
	prefer := isComplexQuery(complexMsg, false, false, 1)
	if !prefer {
		t.Fatal("expected complex query to be flagged complex")
	}
	got := ResolveAutoModelWithReasoning(complexMsg, false, false, 1, ids, prefer, catalogReasoningChecker())
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
	// Fix 4 — the non-reasoning default is now Claude Sonnet 4 (200K
	// context + strong follow-up handling) instead of the gpt-4o-mini
	// fast tier, since claude-sonnet-4-20250514 is the catalog's Claude.
	// It happens to be reasoning-capable, but reasoning stays OFF for a
	// simple turn — the reasoning param is gated by the conversation's
	// reasoning setting (preferReasoning here is false), not by the
	// model id, so the default model being capable doesn't make it think.
	gotSimple := ResolveAutoModelWithReasoning(simpleMsg, false, false, 1, ids, preferSimple, catalogReasoningChecker())
	if gotSimple != "claude-sonnet-4-20250514" {
		t.Fatalf("simple auto turn resolved to %q, want claude-sonnet-4-20250514", gotSimple)
	}
}

// TestPreferredDefaultModel checks the Fix 4 preference: Claude Sonnet
// wins when present; otherwise the resolver falls back to the gpt fast
// tier so catalogs without Claude keep the legacy behaviour.
func TestPreferredDefaultModel(t *testing.T) {
	withClaude := []string{"gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet-20241022"}
	if got := ResolveAutoModelWithReasoning("hi", false, false, 1, withClaude, false, nil); got != "claude-3-5-sonnet-20241022" {
		t.Fatalf("with Claude in catalog, simple turn = %q, want claude-3-5-sonnet-20241022", got)
	}
	noClaude := []string{"gpt-4o", "gpt-4o-mini"}
	if got := ResolveAutoModelWithReasoning("hi", false, false, 1, noClaude, false, nil); got != "gpt-4o-mini" {
		t.Fatalf("without Claude, simple turn = %q, want gpt-4o-mini (legacy fast tier)", got)
	}
}

// TestCatalogCheckerRecognisesDbOnboardedModel is the step-3 regression
// proof: deepseek/deepseek-r1 is reasoning-capable in model_configs but
// was NEVER in the static set, so the old resolver could not prefer it.
// With a catalog-backed checker injected it wins a reasoning-enabled
// turn; with the static fallback (nil) it does not.
func TestCatalogCheckerRecognisesDbOnboardedModel(t *testing.T) {
	ids := []string{"gpt-4o-mini", "deepseek/deepseek-r1"}
	catalog := func(id string) bool { return id == "deepseek/deepseek-r1" }

	got := ResolveAutoModelWithReasoning("analyze this step by step", false, false, 1, ids, true, catalog)
	if got != "deepseek/deepseek-r1" {
		t.Fatalf("catalog-backed resolve = %q, want deepseek/deepseek-r1", got)
	}
	if isReasoningCapableID("deepseek/deepseek-r1") {
		t.Fatal("precondition broken: deepseek/deepseek-r1 unexpectedly joined the static set")
	}
	gotStatic := ResolveAutoModelWithReasoning("analyze this step by step", false, false, 1, ids, true, nil)
	if gotStatic == "deepseek/deepseek-r1" {
		t.Fatalf("static fallback resolved %q — it should not know this model", gotStatic)
	}
}
