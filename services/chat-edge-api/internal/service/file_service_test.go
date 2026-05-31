package service

import (
	"strings"
	"testing"
	"unicode/utf8"
)

// TestTruncatedFileEntry_NoPanicTightBudget reproduces the C1 audit bug:
// a long header with the budget landing in the gap between the old guard
// marker (19 bytes) and the slice marker (23 bytes) used to drive the
// slice high-bound negative and panic. The helper must never panic and
// must return valid UTF-8.
func TestTruncatedFileEntry_NoPanicTightBudget(t *testing.T) {
	header := "### File: " + strings.Repeat("ก", 40) + " (1 KB, uploaded 1m ago)\n"
	body := strings.Repeat("เนื้อหาภาษาไทยยาวมาก ", 500)

	// Sweep every budget around the danger zone (header ± marker lengths).
	for remaining := 0; remaining <= len(header)+len(fileTruncationMarker)+8; remaining++ {
		func() {
			defer func() {
				if r := recover(); r != nil {
					t.Fatalf("panic at remaining=%d: %v", remaining, r)
				}
			}()
			entry, ok := truncatedFileEntry(header, body, remaining)
			if ok {
				if len(entry) > remaining {
					t.Fatalf("remaining=%d: entry len %d exceeds budget", remaining, len(entry))
				}
				if !utf8.ValidString(entry) {
					t.Fatalf("remaining=%d: entry is not valid UTF-8", remaining)
				}
			}
		}()
	}
}

// TestTruncatedFileEntry_RuneBoundary verifies a truncated body is cut on
// a rune boundary (no invalid half-rune reaches the prompt).
func TestTruncatedFileEntry_RuneBoundary(t *testing.T) {
	header := "### File: a.txt\n"
	body := strings.Repeat("世界", 1000) // 3-byte runes

	// Budget big enough to truncate but not fit everything.
	remaining := len(header) + len(fileTruncationMarker) + 100
	entry, ok := truncatedFileEntry(header, body, remaining)
	if !ok {
		t.Fatal("expected ok=true with ample header room")
	}
	if !utf8.ValidString(entry) {
		t.Fatal("truncated entry is not valid UTF-8 — rune was split")
	}
	if !strings.HasSuffix(entry, fileTruncationMarker) {
		t.Fatal("expected truncation marker suffix")
	}
}

// TestTruncatedFileEntry_FitsVerbatim verifies a small entry is returned
// unchanged when it fits the budget.
func TestTruncatedFileEntry_FitsVerbatim(t *testing.T) {
	header := "### File: a.txt\n"
	body := "hello"
	entry, ok := truncatedFileEntry(header, body, 10_000)
	if !ok {
		t.Fatal("expected ok=true")
	}
	if entry != header+body+"\n\n" {
		t.Fatalf("expected verbatim entry, got %q", entry)
	}
	if strings.Contains(entry, "[content truncated]") {
		t.Fatal("small entry should not be truncated")
	}
}
