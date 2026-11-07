package runner

import (
	"strings"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM5 — an upgrade that is not taken says why. Phase C took the engine's
// names out of what a person reads about an upgrade's self-test, and took
// the reason with them: "ผลทดสอบ…ต่างจากที่รุ่นนี้กำหนดไว้ 1 จุด" (the full
// integration suite, TestPinnedInstallRunsItsOwnEngine…, went red on it).
// What differed is said in words — the period, questions that should not
// have been asked — and figures are counted, because a figure's name is the
// engine's; the names and values stay on the run for the run page.

func TestAnUpgradeThatIsNotTakenSaysWhy(t *testing.T) {
	ps := []expectProblem{
		{Words: am.Text("งวดของผลทดสอบไม่ตรงกับที่รุ่นนี้กำหนด (2030-01)"), Detail: "period 2026-09, expected 2030-01"},
		{Words: am.Text("ถาม 2 คำถาม ทั้งที่ข้อมูลตัวอย่างไม่ควรมีคำถาม"), Detail: "2 cards"},
		{Figure: true, Detail: "net_profit = 1.00 (คาด 2.00)"},
		{Figure: true, Detail: "total_expenses ไม่มีในผลคำนวณ"},
	}
	got := upgradeRefused("9.9.0", ps).String()
	for _, want := range []string{"ยังไม่อัปเกรดเป็นรุ่น 9.9.0", "2030-01", "ถาม 2 คำถาม", "ตัวเลขสรุป 2 ค่าไม่ตรงกับที่รุ่นนี้กำหนด", "ยังใช้รุ่นเดิมอยู่", "แจ้งปัญหา"} {
		if !strings.Contains(got, want) {
			t.Fatalf("%q is not said: %s", want, got)
		}
	}
	for _, never := range []string{"net_profit", "total_expenses", "expected", "cards", "คาด 2.00"} {
		if strings.Contains(got, never) {
			t.Fatalf("%q reached the person: %s", never, got)
		}
	}
	if am.LooksTechnical(got) || !am.ReadsForPerson(got) {
		t.Fatalf("reads as technical: %s", got)
	}
	// the run keeps everything, names and values
	kept := expectDetail(ps)
	for _, want := range []string{"net_profit = 1.00", "total_expenses", "2030-01", "2 cards"} {
		if !strings.Contains(kept, want) {
			t.Fatalf("the run keeps %q: %s", want, kept)
		}
	}
	// figures only: still a sentence, still no name
	only := upgradeRefused("2.0.0", []expectProblem{{Figure: true, Detail: "documents = 3 (คาด 4)"}}).String()
	if !strings.Contains(only, "ตัวเลขสรุป 1 ค่าไม่ตรงกับที่รุ่นนี้กำหนด") || strings.Contains(only, "documents") {
		t.Fatal(only)
	}
}
