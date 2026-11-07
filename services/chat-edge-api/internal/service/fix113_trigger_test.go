package service

// FIX113 Part B — what a client sends as the compaction trigger is confined
// to the ledger vocabulary.

import "testing"

func TestFIX113_CompactionTriggerFromClient_IsConfinedToTheLedgerVocabulary(t *testing.T) {
	cases := map[string]string{
		"auto":       CompactionTriggerAuto,
		"AUTO":       CompactionTriggerAuto,
		" auto ":     CompactionTriggerAuto,
		"manual":     CompactionTriggerManual,
		"":           CompactionTriggerManual, // absent → what the endpoint always recorded
		"background": CompactionTriggerManual, // no client causes the background pass
		"bogus":      CompactionTriggerManual,
		"auto; drop": CompactionTriggerManual,
	}
	for raw, want := range cases {
		if got := CompactionTriggerFromClient(raw); got != want {
			t.Errorf("%q → %q, want %q", raw, got, want)
		}
	}
}
