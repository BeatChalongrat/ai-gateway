package service

import (
	"context"
	"testing"
)

// FIX101 Part A — the context ceiling comes from data, and the data is
// treated as ADVERTISED rather than as a working limit.
//
// The owner's conversation e493ca28 was refused by Typhoon at 16,413
// input tokens against a real 16,384 cap while the app reported it
// 11.39 % full. The app was budgeting against 120,000: Typhoon is in no
// hard-coded map, so it inherited defaultContextLimit, whose comment
// claimed the estimator's overshoot would keep such a request inside the
// provider's cap. Seven times too large is not a rounding error.

// The measured windows, each established by the provider's own refusal
// sentence through this gateway on 2026-09-10. These are the numbers the
// catalogue now carries, so they are the numbers the rule must handle.
var measuredWindows = map[string]int{
	"Typhoon-S-ThaiLLM-8B-Instruct":         16384,
	"OpenThaiGPT-ThaiLLM-8B-Instruct-v7.2":  16384,
	"Pathumma-ThaiLLM-qwen3-8b-think-3.0.0": 16384,
	"THaLLE-0.2-ThaiLLM-8B-fa":              16384,
	"qwen3.5-9b":                            32768,
	"qwen3.6-35b-a3b":                       32768,
	"qwen3.8-27b-fp8":                       131072,
}

func lookupOf(m map[string]int) ContextWindowLookup {
	return func(_ context.Context, id string) int { return m[id] }
}

// The one invariant everything else exists to serve: whatever we budget
// against must be SMALLER than what the provider will actually accept.
func TestFIX101_WorkingLimitAlwaysFitsInsideTheRealWindow(t *testing.T) {
	for id, advertised := range measuredWindows {
		got := workingLimitFrom(advertised)
		if got >= advertised {
			t.Errorf("%s: working %d is not inside the real window %d", id, got, advertised)
		}
		// And the headroom is the documented 5–10 %, not an accident.
		frac := float64(advertised-got) / float64(advertised)
		if frac < 0.05 || frac > 0.15 {
			t.Errorf("%s: headroom %.1f%% is outside the documented 5–10 %% convention (%d → %d)",
				id, frac*100, advertised, got)
		}
	}
}

func TestFIX101_HeadroomIsAppliedToTheNumberThatArrives(t *testing.T) {
	for _, c := range []struct{ advertised, want int }{
		{16384, 14500},
		{32768, 29000},
		{131072, 117500},
		{128000, 115000},
		{200000, 180000},
		{1000000, 900000},
		{8192, 7000},
	} {
		if got := workingLimitFrom(c.advertised); got != c.want {
			t.Errorf("workingLimitFrom(%d) = %d, want %d", c.advertised, got, c.want)
		}
	}
}

func TestFIX101_CatalogueBeatsTheSeedMap(t *testing.T) {
	ctx := context.Background()
	// gpt-4o IS in the seed map at 120000 (a working limit somebody
	// typed). An operator whose deployment serves a smaller gpt-4o, or
	// who simply knows better, edits the catalogue — and that has to win,
	// or the admin console is a lie.
	if got := GetModelContextLimitWith(ctx, lookupOf(map[string]int{"gpt-4o": 32768}), "gpt-4o"); got != 29000 {
		t.Errorf("catalogue 32768 → %d, want 29000 (the map's 120000 must not win)", got)
	}
	// And upwards too: a "whichever is smaller" rule would have silently
	// ignored this.
	if got := GetModelContextLimitWith(ctx, lookupOf(map[string]int{"gpt-4": 200000}), "gpt-4"); got != 180000 {
		t.Errorf("catalogue 200000 → %d, want 180000 (the map's 7500 must not win)", got)
	}
}

func TestFIX101_SeedMapAnswersWhenTheCatalogueDoesNot(t *testing.T) {
	ctx := context.Background()
	empty := lookupOf(map[string]int{})
	// Exact hit.
	if got := GetModelContextLimitWith(ctx, empty, "gpt-4o"); got != 120000 {
		t.Errorf("seed exact = %d, want 120000", got)
	}
	// Longest prefix still wins — the map values are ALREADY working
	// limits, so no headroom may be applied to them a second time.
	if got := GetModelContextLimitWith(ctx, empty, "claude-sonnet-4-20250514"); got != 190000 {
		t.Errorf("seed prefix = %d, want 190000 (unmodified, it is already a working limit)", got)
	}
	// A gateway that is down is the same as a catalogue that is silent.
	if GetModelContextLimitWith(ctx, nil, "gpt-4o") != 120000 {
		t.Error("a nil lookup must fall through to the seed map, not to the default")
	}
}

// The regression that this whole round is about.
func TestFIX101_UnknownModelIsNoLongerTreatedAsAFlagship(t *testing.T) {
	ctx := context.Background()
	got := GetModelContextLimitWith(ctx, lookupOf(map[string]int{}), "some-model-nobody-pinned")
	if got == 120000 {
		t.Fatal("an unknown model is being budgeted at 120,000 again — this is exactly what refused the owner's conversation at 11 % reported use")
	}
	if got != 14500 {
		t.Fatalf("unknown model → %d, want 14500 (16,384 with headroom: the smallest window measured on a model serving this deployment)", got)
	}
	// It must fit inside the smallest window we have actually seen, or
	// "conservative" is a word rather than a property.
	if got >= smallestObservedWindow {
		t.Fatalf("the default %d does not fit inside the smallest observed window %d", got, smallestObservedWindow)
	}
}

// "auto" is a router sentinel that STAYS in the conversation row — 221
// of the owner's conversations carry it — and the router picks the
// answering model per turn, after the budget is already decided. The
// catalogue has nothing to say about it (the guard is in
// handler.ModelHandler.AdvertisedContextWindow, tested there), so it
// lands on the conservative default.
//
// This is the round's biggest behaviour change and it is deliberate: an
// auto conversation used to be budgeted at 120,000 and is now budgeted
// at 14,500, because four of the seven models the router can choose
// refuse at 16,384 and nothing knows which one will answer.
func TestFIX101_AutoSentinelLandsOnTheConservativeDefault(t *testing.T) {
	ctx := context.Background()
	// The catalogue is silent for the sentinel.
	got := GetModelContextLimitWith(ctx, lookupOf(measuredWindows), AutoModelID)
	if got != defaultContextLimit {
		t.Fatalf("auto → %d, want the default %d", got, defaultContextLimit)
	}
	// And that default fits inside every model the router could pick.
	for id, advertised := range measuredWindows {
		if got >= advertised {
			t.Fatalf("auto budgets %d, which does not fit inside %s's %d", got, id, advertised)
		}
	}
}

// The owner's incident, replayed through the fixed code with the
// measured catalogue: the app must decide to trim well before the
// provider decides to refuse.
func TestFIX101_TyphoonIncidentNowTrimsBeforeItOverflows(t *testing.T) {
	ctx := context.Background()
	const (
		realCap          = 16384 // provider's own sentence
		incidentSystem   = 4838  // measured system prefix, that turn
		incidentTools    = 11 * perToolTokens
		incidentHistory  = 11001 // estimated history at the first refusal
		upstreamReported = 16413 // what the provider counted
	)
	b := CalculateBudgetWith(ctx, lookupOf(measuredWindows), "Typhoon-S-ThaiLLM-8B-Instruct",
		incidentSystem, 0, 0, 0, incidentTools)

	if b.TotalBudget >= realCap {
		t.Fatalf("budgeting against %d for a model that refuses at %d", b.TotalBudget, realCap)
	}
	// Everything we would ever put on the wire, at the moment the budget
	// is fully spent, still fits.
	worst := b.SystemPromptTokens + b.ToolReserve + b.OutputReserve + b.MessagesRemaining
	if worst > realCap {
		t.Fatalf("a full budget serialises to %d tokens against a %d cap", worst, realCap)
	}
	threshold := int(float64(b.MessagesRemaining) * DefaultCompactTriggerRatio)
	if threshold >= incidentHistory {
		t.Fatalf("threshold %d is not reached by the %d-token history that was refused: the app would still have sent it",
			threshold, incidentHistory)
	}
	t.Logf("Typhoon: total %d, output reserve %d, messages %d, trims at %d — the history that upstream counted as %d tokens crosses it %.1fx over",
		b.TotalBudget, b.OutputReserve, b.MessagesRemaining, threshold, upstreamReported,
		float64(incidentHistory)/float64(threshold))
}

// Before: the app believed 120,000 for all three incident models. After:
// three different numbers, each the model's own.
func TestFIX101_EachModelGetsItsOwnCeiling(t *testing.T) {
	ctx := context.Background()
	lookup := lookupOf(measuredWindows)
	seen := map[int]string{}
	for id := range measuredWindows {
		got := GetModelContextLimitWith(ctx, lookup, id)
		if got == 120000 {
			t.Errorf("%s is still on the flagship default", id)
		}
		seen[got] = id
	}
	if len(seen) != 3 {
		t.Errorf("expected three distinct ceilings across the seven self-hosted models, got %d: %v", len(seen), seen)
	}
}
