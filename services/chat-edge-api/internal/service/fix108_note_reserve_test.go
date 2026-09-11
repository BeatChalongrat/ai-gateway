// FIX108 Part A — the context note's reserve is measured from the note.
package service

import (
	"strings"
	"testing"
)

// Every note the asset can render — every head variant and status, counts
// from one digit to seven — fits what the compiler takes off the budget for
// it, including when it is estimated as the tail of a system prefix.
func TestFIX108_EveryRenderedNoteFitsTheReserve(t *testing.T) {
	prefixes := []string{"", "S", "SS", "SSS", strings.Repeat("You are SRAN Chat. ", 40)}
	biggest := 0
	for _, head := range []int{0, 2} {
		for _, status := range []string{SummaryComplete, SummaryPartial, SummaryFailed, SummaryOff, SummaryPending} {
			for count := 1; count <= contextNoteMaxCount; count = count*10 + 9 {
				note := compactionContextNote(count, count, head, status)
				if n := EstimateTokens(note); n > biggest {
					biggest = n
				}
				for _, prefix := range prefixes {
					grew := EstimateTokens(prefix+note) - EstimateTokens(prefix)
					if grew > contextNoteReserve {
						t.Fatalf("head=%d status=%s count=%d: the note adds %d tokens to the prefix, the reserve is %d", head, status, count, grew, contextNoteReserve)
					}
				}
			}
		}
	}
	// Measured, not padded: the reserve is the largest note plus the one
	// token of rounding, nothing more.
	if contextNoteReserve != biggest+1 {
		t.Fatalf("reserve %d, largest note %d: the reserve should be the largest note + 1", contextNoteReserve, biggest)
	}
	t.Logf("context note reserve: %d tokens (largest note %d)", contextNoteReserve, biggest)
}

// The routing hint for an auto turn carries the same allowance.
func TestFIX108_TheRoutingHintUsesTheMeasuredReserve(t *testing.T) {
	h := &RoutingHints{}
	cc := &CompiledContext{Compacted: true}
	applyContextHints(h, cc)
	if h.MinContextTokens != contextNoteReserve {
		t.Fatalf("hint %d, reserve %d", h.MinContextTokens, contextNoteReserve)
	}
}
