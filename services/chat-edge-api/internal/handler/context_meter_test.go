// FIX102 Part B — the meter never reports a request that cannot exist.
package handler

import (
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// The numbers are the ones FIX102's own probe measured on Typhoon: a
// 3,944-token messages budget, a 3,352 trigger.
func compiled(history, sent, total int, compacted bool) *service.CompiledContext {
	return &service.CompiledContext{
		Budget:          service.ContextBudget{MessagesRemaining: 3944},
		HistoryTokens:   history,
		SentTokens:      sent,
		ThresholdTokens: 3352,
		MessagesTotal:   total,
		Compacted:       compacted,
	}
}

func TestFIX102_QuietAndApproachingAreUnchanged(t *testing.T) {
	// Before any trim sent == loaded, so the ratio is what it always was.
	m := contextMeter(compiled(970, 970, 2, false))
	if m.Trimming || m.OverBudget || m.UsedRatio < 0.245 || m.UsedRatio > 0.247 {
		t.Fatalf("turn 1: %+v", m)
	}
	m = contextMeter(compiled(2253, 2253, 4, false))
	if m.EstRemaining != 1 {
		t.Fatalf("turn 2 must count down to the trim: %+v", m)
	}
}

func TestFIX102_TrimmingRoutinelyNeverReadsAbove100(t *testing.T) {
	// Turn 12 of the probe: 13,422 tokens stored, 2,854 sent. The old meter
	// reported 340 %.
	m := contextMeter(compiled(13422, 2854, 24, true))
	if m.UsedRatio > 1 {
		t.Fatalf("reported %.0f %% of what will be sent", m.UsedRatio*100)
	}
	if !m.Trimming {
		t.Fatal("a compacting conversation must say it is trimming")
	}
	if m.OverBudget {
		t.Fatal("2,854 of 3,944 is not over budget")
	}
	if m.EstRemaining != 0 {
		t.Fatal("there is nothing to count down to once trimming is routine")
	}
}

func TestFIX102_OverBudgetIsTheOnlyCaseAbove1AndItIsClamped(t *testing.T) {
	// One enormous paste: the newest message always ships, even alone.
	m := contextMeter(compiled(9000, 9000, 2, false))
	if m.UsedRatio != 1 || !m.OverBudget {
		t.Fatalf("%+v", m)
	}
}
