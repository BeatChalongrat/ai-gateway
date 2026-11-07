package runner

import (
	"strings"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM11b — carried from AM12: did the platform's prompt-echo guard cut pieces out of the other agents'
// answers? It did, out of the report agent's commentary. The guard removes from an answer every run of
// 40 characters that repeats the system message; the report agent's instructions (AGENT.md, the system
// message) printed as an example the sentence “ค่าใช้จ่ายในการขายและบริหารสูงกว่างบ
// {operating_expenses_var_budget_pct}”, and a commentary that said the same thing in the same words came
// back as "…ค่าใช้จ่ายในการขายและบริหารที่:abs}" — the figure gone, half its name printed in the executive
// summary. On live, 4 of 27 commentaries a model wrote (1–6 Oct 2026).
//
// The package's instructions no longer print a sentence a commentary writes (version 1.8.0;
// backend/tests/test_am11b_instructions_echo.py holds every package to it with the guard itself). This
// test is the net under every installed version: a commentary with a figure's name cut in half is no
// answer — whatever cut it.
func TestAM11bACommentaryWithAFigureNameCutInHalfIsNoAnswer(t *testing.T) {
	cv := computedView{
		Figures: map[string]figureView{
			"operating_expenses_var_budget_pct": {Text: "10.5%", Cite: "c120", Label: "ค่าใช้จ่ายในการขายและบริหาร ต่างจากงบประมาณ (%)"},
			"net_profit_var_budget_pct":         {Text: "-3.2%", Cite: "c140", Label: "กำไรสุทธิ ต่างจากงบประมาณ (%)"},
			"revenue":                           {Text: "12,480,300", Cite: "c31", Label: "รายได้ งวดนี้ (บาท)"},
		},
		QuotableIDs: []string{"5300-01"},
	}
	// as the model wrote it — used
	whole := `{"summary":"กำไรสุทธิต่ำกว่างบประมาณ {net_profit_var_budget_pct:abs} ปัจจัยหลักคือค่าใช้จ่ายในการขายและบริหารที่สูงกว่างบ {operating_expenses_var_budget_pct:abs}"}`
	if _, err := checkNarrative(am.Thai, whole, cv); err != nil {
		t.Fatalf("a commentary that names its figures whole: %v", err)
	}
	bad := []string{
		// as it came back from the gateway on live (2026-10-06 22:42Z): 44 characters gone
		`{"summary":"กำไรสุทธิต่ำกว่างบประมาณ {net_profit_var_budget_pct:abs} ปัจจัยหลักคือค่าใช้จ่ายในการขายและบริหารที่:abs}"}`,
		// the same, in a list
		`{"summary":"ภาพรวมของงวด รายได้ {revenue}","movements":["ค่าใช้จ่ายในการขายและบริหารที่}"]}`,
		// a name that was never closed, and one with its ending written twice
		`{"summary":"รายได้ {revenue ของงวดนี้"}`,
		`{"summary":"รายได้ {revenue} ค่าใช้จ่ายลดลง {operating_expenses_var_budget_pct:abs:abs}"}`,
	}
	for _, s := range bad {
		_, err := checkNarrative(am.Thai, s, cv)
		if err == nil || !strings.Contains(err.Error(), "เขียนไม่ครบ") {
			t.Fatalf("a figure's name cut in half was taken for an answer: %s → %v", s, err)
		}
	}
}
