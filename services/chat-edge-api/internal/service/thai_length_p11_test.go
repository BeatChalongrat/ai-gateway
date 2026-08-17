package service

// P1-1 — message length is counted in RUNES, not bytes. Thai is 3 bytes/rune
// in UTF-8, so the old byte-based `len(message) > 500` flagged a ~167-char Thai
// message as complex and mis-sized the low/medium complexity hint. These tests
// pin the rune-based behavior (and guard English from regressing).

import (
	"strings"
	"testing"
)

func thaiRunes(n int) string { return strings.Repeat("ก", n) }    // 1 rune, 3 bytes each
func enChars(n int) string   { return strings.Repeat("a", n) }

func TestIsComplexQuery_ThaiUsesRunesNotBytes(t *testing.T) {
	// 200 Thai runes = 600 bytes. Byte-based code wrongly flagged this complex
	// (600 > 500); rune-based correctly does not (200 <= 500), no other signal.
	if isComplexQuery(thaiRunes(200), false, false, 0) {
		t.Fatal("200-rune Thai message should NOT be complex (rune count = 200)")
	}
	// 600 runes genuinely exceeds 500 → complex.
	if !isComplexQuery(thaiRunes(600), false, false, 0) {
		t.Fatal("600-rune Thai message should be complex")
	}
}

func TestIsComplexQuery_EnglishUnchanged(t *testing.T) {
	if isComplexQuery(enChars(600), false, false, 0) != true {
		t.Fatal("600-char English should be complex (unchanged)")
	}
	if isComplexQuery(enChars(200), false, false, 0) != false {
		t.Fatal("200-char English with no signal should not be complex")
	}
}

func TestIsComplexQuery_ShortThaiGreetingNotComplex(t *testing.T) {
	if isComplexQuery("ไง", false, false, 0) {
		t.Fatal(`"ไง" should not be complex`)
	}
}

func TestBuildRoutingHints_MidLengthThaiIsLowNotMedium(t *testing.T) {
	// 30 Thai runes = 90 bytes. Old byte-based low-gate (<=80) failed (90 > 80)
	// → "medium"; rune-based passes (30 <= 80) → "low".
	h := BuildRoutingHints(thaiRunes(30), false, false, 0, false, 0)
	if h.Complexity != "low" {
		t.Fatalf("30-rune Thai turn: complexity=%q, want low", h.Complexity)
	}
}

func TestBuildRoutingHints_LongThaiIsHigh(t *testing.T) {
	// 600 runes → isComplexQuery true → complexity "high".
	h := BuildRoutingHints(thaiRunes(600), false, false, 0, false, 0)
	if h.Complexity != "high" {
		t.Fatalf("600-rune Thai turn: complexity=%q, want high", h.Complexity)
	}
}
