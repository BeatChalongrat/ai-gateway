package runner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM11 (D) — what becomes of a form that carries no marker, once it has been read.

const am11Spec = `{"fields":{"company":{"label":"ชื่อบริษัท"},"period":{"label":"งวด"},"revenue":{"label":"รายได้"},
 "gross_profit":{"label":"กำไรขั้นต้น"},"total_expenses":{"label":"ค่าใช้จ่ายรวม"},"net_profit":{"label":"กำไรสุทธิ"}}}`

func blanks(labels ...string) []am.TemplateSlot {
	var out []am.TemplateSlot
	for i, l := range labels {
		out = append(out, am.TemplateSlot{Key: "table0.r" + string(rune('a'+i)) + "c1", Kind: "target", Label: l,
			Fillable: true, Required: true, Side: "right"})
	}
	return out
}

func am11SpecOf(t *testing.T) am.TemplateSpec {
	t.Helper()
	var spec am.TemplateSpec
	if err := json.Unmarshal([]byte(am11Spec), &spec); err != nil {
		t.Fatal(err)
	}
	return spec
}

func TestAFormIsRefusedOnlyWhenAModelReadItAndFoundTooLittle(t *testing.T) {
	spec := am11SpecOf(t)
	request := blanks("เลขที่ใบขอซื้อ", "วันที่ขอซื้อ", "ผู้ขอซื้อ", "แผนก", "ชื่อบริษัท", "ผู้ขายที่เสนอ", "วันที่ต้องการของ",
		"สถานที่ส่งของ", "เงื่อนไขการชำระเงิน", "เหตุผลที่ขอซื้อ")
	one := map[string]string{request[4].Key: "company"}

	// a model read the labels and the agent has a value for one of ten: refused, nothing is made
	if status, why := decideForm(request, one, "deepseek-v4-flash", nil, spec); status != am.FormRefused || why != am.RefusedTooFew {
		t.Fatalf("one of ten, read by a model: %s %q", status, why)
	}
	// the same form with no model to read it: nothing is known about what the agent could fill — the person maps it
	if status, why := decideForm(request, one, "", nil, spec); status != am.FormNeedsMapping || why != "" {
		t.Fatalf("one of ten, read by nobody: %s %q", status, why)
	}
	// a model that read it and found nothing at all
	if status, why := decideForm(request, map[string]string{}, "deepseek-v4-flash", nil, spec); status != am.FormRefused || why != am.RefusedTooFew {
		t.Fatalf("none of ten: %s %q", status, why)
	}
}

func TestAFormWithEveryBlankProposedIsFilledWithoutAQuestion(t *testing.T) {
	spec := am11SpecOf(t)
	memo := blanks("ชื่อบริษัท", "งวดบัญชี", "รายได้จากการขาย (บาท)", "กำไรขั้นต้น (บาท)", "ค่าใช้จ่ายรวม (บาท)", "กำไรสุทธิ (บาท)")
	fields := []string{"company", "period", "revenue", "gross_profit", "total_expenses", "net_profit"}
	all := map[string]string{}
	for i, s := range memo {
		all[s.Key] = fields[i]
	}
	if status, why := decideForm(memo, all, "deepseek-v4-flash", nil, spec); status != am.FormReady || why != "" {
		t.Fatalf("six of six: %s %q", status, why)
	}
	// a title and a note with an empty cell beside them need no decision: the form is still obvious
	extra := append(memo, am.TemplateSlot{Key: "Cover!C2", Kind: "target", Label: "Monthly board pack", Fillable: true, Side: "right"})
	if status, _ := decideForm(extra, all, "deepseek-v4-flash", nil, spec); status != am.FormReady {
		t.Fatalf("a title beside the fields made the form wait for a person: %s", status)
	}
	// from a reader that does not tell a blank from a title (an engine from before AM11), every cell needs a decision
	old := append(memo, am.TemplateSlot{Key: "Cover!C2", Kind: "target", Label: "Monthly board pack", Fillable: true})
	if status, _ := decideForm(old, all, "deepseek-v4-flash", nil, spec); status != am.FormNeedsMapping {
		t.Fatalf("an older engine's slot was skipped: %s", status)
	}
}

func TestAFormWithMostOfItsBlanksProposedWaitsForThePersonAndIsNotRefused(t *testing.T) {
	spec := am11SpecOf(t)
	cover := blanks("Company", "Period", "Revenue (THB)", "Gross profit (THB)", "Total expenses (THB)", "Net profit (THB)",
		"Headcount", "Cash at bank (THB)")
	fields := []string{"company", "period", "revenue", "gross_profit", "total_expenses", "net_profit"}
	six := map[string]string{}
	for i, f := range fields {
		six[cover[i].Key] = f
	}
	if status, why := decideForm(cover, six, "deepseek-v4-flash", nil, spec); status != am.FormNeedsMapping || why != "" {
		t.Fatalf("six of eight: %s %q", status, why)
	}
}

func TestAFileWithNothingToFillAndAFormTheFillerCannotUse(t *testing.T) {
	spec := am11SpecOf(t)
	if status, why := decideForm(nil, nil, "", nil, spec); status != am.FormRefused || why != am.RefusedNoFields {
		t.Fatalf("a letter: %s %q", status, why)
	}
	chart := []json.RawMessage{json.RawMessage(`{"key":"word/charts/chart1.xml","reason":"chart_not_updated","label":"กราฟ 1"}`)}
	if status, why := decideForm(blanks("ชื่อบริษัท"), nil, "", chart, spec); status != am.FormFailed || why != "chart_not_updated" {
		t.Fatalf("a form with a chart: %s %q", status, why)
	}
}

// A model that answers with an empty mapping has read the form and found nothing for this agent; an
// answer with no mapping in it is not a reading at all.
func TestWhoReadTheFormIsRecordedEvenWhenNothingWasProposed(t *testing.T) {
	answers := []string{`{"mapping":{}}`}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Model string `json:"model"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		a := answers[0]
		if len(answers) > 1 {
			answers = answers[1:]
		}
		b, _ := json.Marshal(map[string]any{"model": body.Model, "choices": []any{map[string]any{
			"message": map[string]any{"content": a}, "finish_reason": "stop"}}, "usage": map[string]any{"total_tokens": 10}})
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.Close)
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "deepseek-v4-flash", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	pkg := &Package{}
	pkg.Manifest.Capabilities.Required = []string{"model.text"}
	pkg.Manifest.Templates = json.RawMessage(am11Spec)
	rc := &runCtx{r: &Runner{Model: m, CreditsPerUSD: 1}, pkg: pkg, docsMayLeave: true}
	res := mustJSON(map[string]any{"slots": blanks("เลขที่ใบขอซื้อ", "ผู้ขอซื้อ", "แผนก"), "proposal": map[string]string{}})

	out, _, _ := rc.proposeMapping(context.Background(), res)
	var got struct {
		ReadBy     string            `json:"read_by"`
		ProposedBy string            `json:"proposed_by"`
		Proposal   map[string]string `json:"proposal"`
	}
	_ = json.Unmarshal(out, &got)
	if got.ReadBy != "deepseek-v4-flash" || got.ProposedBy != "" || len(got.Proposal) != 0 {
		t.Fatalf("an empty mapping: read_by %q, proposed_by %q, proposal %v", got.ReadBy, got.ProposedBy, got.Proposal)
	}

	answers = []string{`{"note":"I could not tell"}`}
	out, _, _ = rc.proposeMapping(context.Background(), res)
	got.ReadBy = ""
	_ = json.Unmarshal(out, &got)
	if got.ReadBy != "" {
		t.Fatalf("an answer with no mapping in it was taken as a reading of the form: %q", got.ReadBy)
	}
}
