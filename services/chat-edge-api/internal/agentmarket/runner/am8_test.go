package runner

import (
	"strings"
	"testing"
)

// AM8: a narrative names every figure by its key. The audit found "SG&A over budget 10.5%" cited to the
// cost of sales, because the report matched the printed "10.5%" to the first figure that printed the same.
func TestANarrativeNamesItsFiguresAndTypesNone(t *testing.T) {
	cv := computedView{
		Figures: map[string]figureView{
			"operating_expenses_var_budget_pct": {Text: "10.5%", Cite: "c120", Label: "ค่าใช้จ่ายในการขายและบริหาร ต่างจากงบประมาณ (%)"},
			"cost_of_sales_var_prior_pct":       {Text: "10.5%", Cite: "c110", Label: "ต้นทุนขาย เปลี่ยนแปลงจากงวดก่อน (%)"},
			"revenue":                           {Text: "12,480,300", Cite: "c31", Label: "รายได้ งวดนี้ (บาท)"},
		},
		Quotable:    map[string]string{"operating_expenses_var_budget_pct": "10.5%", "revenue": "12,480,300"},
		QuotableIDs: []string{"5300-01"},
	}
	ok := []string{
		`{"summary":"รายได้ {revenue} บาท ค่าใช้จ่ายในการขายและบริหารสูงกว่างบ {operating_expenses_var_budget_pct}"}`,
		`{"summary":"ต้นทุนขายลดลง {cost_of_sales_var_prior_pct:abs} ใน 3 เดือน ปี 2569 บัญชี 5300-01"}`,
	}
	for _, s := range ok {
		if _, err := checkNarrative(s, cv); err != nil {
			t.Fatalf("a narrative that names its figures is used: %s: %v", s, err)
		}
	}
	bad := map[string]string{
		// the trap itself: a figure typed in its printed form — which "10.5%" is it?
		`{"summary":"ค่าใช้จ่ายในการขายและบริหารสูงกว่างบ 10.5%"}`: "อย่าพิมพ์ตัวเลขเอง",
		`{"summary":"รายได้ 12,480,300 บาท"}`:                        "อย่าพิมพ์ตัวเลขเอง",
		`{"summary":"รายได้ {revenue_m} บาท"}`:                       "อ้างตัวเลขที่ไม่มีในตาราง",
		`{"summary":"ภาพรวม","watch":["ใช้เกินงบ 47,000 บาท"]}`:       "อย่าพิมพ์ตัวเลขเอง",
	}
	for s, why := range bad {
		_, err := checkNarrative(s, cv)
		if err == nil || !strings.Contains(err.Error(), why) {
			t.Fatalf("%s: want refused (%s), got %v", s, why, err)
		}
	}
}

func TestTheNarrativePromptListsFiguresByKeyWithWhatTheyAre(t *testing.T) {
	cv := computedView{PeriodLabel: "กันยายน 2569", Figures: map[string]figureView{
		"operating_expenses_var_budget_pct": {Text: "10.5%", Label: "ค่าใช้จ่ายในการขายและบริหาร ต่างจากงบประมาณ (%)"},
	}}
	p := narrativePrompt(cv)
	if !strings.Contains(p, "{operating_expenses_var_budget_pct} = 10.5% · ค่าใช้จ่ายในการขายและบริหาร ต่างจากงบประมาณ (%)") {
		t.Fatalf("the prompt names each figure by its key, its printed text and what it is:\n%s", p)
	}
	if strings.Contains(p, "คัดลอกตรงตัวอักษร") {
		t.Fatal("the model is not asked to copy figures any more")
	}
}

// AM8: the quality line says what was asserted — files opened again, figures read back — not a count of
// checks that counted one of them three times.
func TestTheQualityLineSaysWhatWasAsserted(t *testing.T) {
	o := renderOut{Artifacts: []renderedArt{
		{Quality: []byte(`{"passed":true,"checks":[{"ok":true,"values":61},{"ok":true}]}`)},
		{Quality: []byte(`{"passed":true,"checks":[{"ok":true,"values":40}]}`)},
	}}
	if got := qualitySummary(o).String(); got != "เปิดไฟล์ตรวจซ้ำ 2 ไฟล์ · ตัวเลข 101 ค่าตรงกับที่คำนวณ" {
		t.Fatalf("%q", got)
	}
	o.Artifacts[1].Quality = []byte(`{"passed":false,"checks":[{"ok":false,"values":40}]}`)
	if got := qualitySummary(o).String(); got != "เปิดไฟล์ตรวจซ้ำ 2 ไฟล์ · ไม่ผ่าน 1 เรื่อง" {
		t.Fatalf("%q", got)
	}
}
