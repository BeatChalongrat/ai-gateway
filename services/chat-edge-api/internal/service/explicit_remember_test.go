package service

import (
	"encoding/json"
	"os"
	"testing"
)

// FIX130 — the explicit-remember table is shared with the desktop host
// (apps/desktop/test/explicit-remember.test.ts reads the same file).
func TestExplicitRememberRequestTable(t *testing.T) {
	raw, err := os.ReadFile("testdata/explicit_remember_cases.json")
	if err != nil {
		t.Fatalf("read table: %v", err)
	}
	var table struct {
		Cases []struct {
			Text     string `json:"text"`
			Explicit bool   `json:"explicit"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &table); err != nil {
		t.Fatalf("parse table: %v", err)
	}
	if len(table.Cases) < 20 {
		t.Fatalf("table too small: %d rows", len(table.Cases))
	}
	for _, c := range table.Cases {
		if got := ExplicitRememberRequest(c.Text); got != c.Explicit {
			t.Errorf("ExplicitRememberRequest(%q) = %v, want %v", c.Text, got, c.Explicit)
		}
	}
}
