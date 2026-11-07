package runner

import (
	"strings"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// A round on the web ends three ways. "Done — every value in the table and in the draft was checked"
// is true of one of them: a round that found nothing to compare, and a round whose person chose none,
// must not say it (found on the test stack: a round that opened no page said exactly that).
func TestAM12ARoundOnTheWebSaysWhatItCameTo(t *testing.T) {
	done := am.Text("เสร็จแล้วครับ")
	count := func(offers, sources, chosen int) map[string]any {
		return map[string]any{"offers": float64(offers), "sources": float64(sources), "chosen": float64(chosen)}
	}
	cases := []struct {
		name    string
		summary map[string]any
		th, en  []string // what the sentence must say
		notTH   []string // and what it must not
	}{
		{"a draft was made", count(2, 3, 1),
			[]string{"เสร็จแล้วครับ", "ทุกค่าในตารางและในร่างตรวจซ้ำ", "ยังไม่ได้ส่งอะไรให้ผู้ขายหรือให้ใคร"},
			[]string{"in the table and in the draft", "Nothing has been sent"}, []string{"ยังไม่พบตัวเลือก", "ยังไม่มีร่างเอกสาร"}},
		{"the person chose none", count(2, 3, 0),
			[]string{"เสร็จแล้วครับ", "ทุกค่าในตารางตรวจซ้ำ", "ยังไม่มีร่างเอกสาร เพราะยังไม่ได้เลือกตัวเลือก", "ยังไม่ได้ส่งอะไร"},
			[]string{"There is no draft, because no candidate was chosen", "Nothing has been sent"}, []string{"ในร่างตรวจซ้ำ", "ยังไม่พบตัวเลือก"}},
		{"pages were opened and none could be compared", count(0, 2, 0),
			[]string{"ยังไม่พบตัวเลือกที่นำมาเทียบได้", "เหตุผลของแต่ละแหล่งอยู่ในไฟล์ที่ส่งให้", "วางลิงก์", "ยังไม่ได้ส่งอะไร"},
			[]string{"no candidate that can be compared was found", "The reason for each source", "paste the link"}, []string{"เสร็จแล้วครับ", "ทุกค่าในตาราง", "ในร่าง"}},
		{"no page was opened at all", count(0, 0, 0),
			[]string{"ยังไม่พบตัวเลือกที่นำมาเทียบได้", "จึงไม่ได้เปิดหน้าใด", "วางลิงก์", "ยังไม่ได้ส่งอะไร"},
			[]string{"no candidate that can be compared was found", "so no page was opened", "paste the link"}, []string{"เสร็จแล้วครับ", "ทุกค่าในตาราง", "ในร่าง"}},
	}
	for _, c := range cases {
		said := webClosing(done, c.summary)
		th, en := said.In(am.Thai), said.In(am.English)
		for _, want := range c.th {
			if !strings.Contains(th, want) {
				t.Errorf("%s: the Thai does not say %q: %s", c.name, want, th)
			}
		}
		for _, not := range c.notTH {
			if strings.Contains(th, not) {
				t.Errorf("%s: the Thai says %q, which is not true of this round: %s", c.name, not, th)
			}
		}
		for _, want := range c.en {
			if !strings.Contains(en, want) {
				t.Errorf("%s: the English does not say %q: %s", c.name, want, en)
			}
		}
		if hasThai(en) {
			t.Errorf("%s: Thai left in the English: %s", c.name, en)
		}
	}
	// a package that does not count what it found is not said to have found nothing
	if th := webClosing(done, map[string]any{}).In(am.Thai); strings.Contains(th, "ยังไม่พบตัวเลือก") || !strings.Contains(th, "ตรวจซ้ำกับหน้าเว็บที่บันทึกไว้") {
		t.Errorf("no counts: %s", th)
	}
}
