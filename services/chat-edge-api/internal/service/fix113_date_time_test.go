package service

// FIX113 Part A — the date/time block's fixed pieces are one asset and the
// web's rendered block is byte for byte what it was.

import (
	"os"
	"strings"
	"testing"
	"time"
)

func TestFIX113_TheDateTimeBlockIsByteForByteWhatItWas(t *testing.T) {
	pc := &PromptCompiler{}
	at := time.Date(2026, 9, 14, 13, 15, 0, 0, time.UTC) // 20:15 in Bangkok
	got := pc.dateTime(CompileContext{CurrentTime: at})
	const was = "Current date and time: 2026-09-14 (Monday) 20:15, Asia/Bangkok (UTC+7). " +
		"Treat this as the present moment when answering anything time-relative (today, this year, recent, latest, etc.)."
	if got != was {
		t.Fatalf("the date/time block changed:\n%s", got)
	}
	raw, err := os.ReadFile("prompt_date_time.txt")
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(strings.ReplaceAll(string(raw), "\r\n", "\n")), "\n")
	if len(lines) != 2 || lines[0] != dateTimeLead || lines[1] != dateTimeRule {
		t.Fatalf("the asset must hold the lead and the rule and nothing else; got %q", lines)
	}
	if strings.Contains(strings.Join(lines, "\n"), "Bangkok") {
		t.Fatal("the zone is each surface's own and must not be in the shared asset")
	}
}
