package runner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

const am8Spec = `{"fields":{"company":{"label":"ชื่อบริษัท"},"revenue":{"label":"รายได้"},"net_profit":{"label":"กำไรสุทธิ"}},
 "lists":{"lines":{"label":"งบกำไรขาดทุน","columns":{"label":"รายการ","current":"งวดนี้"}}}}`

func am8Slots() []am.TemplateSlot {
	return []am.TemplateSlot{
		{Key: "CompanyName", Kind: "name", Label: "CompanyName", Fillable: true, Context: "ชื่อลูกค้า"},
		{Key: "Form!B4", Kind: "target", Label: "ยอดขายรวม", Fillable: true},
		{Key: "revenue", Kind: "marker", Label: "revenue", Fillable: true},
		{Key: "PL", Kind: "table", Label: "PL", Fillable: true, Columns: []string{"A", "B"}},
	}
}

// AM8: the model proposes what code may not guess (which slot of the customer's form is which field);
// every pair is checked and the ones the check refuses are dropped; the person still confirms all of it.
func TestTheModelProposesTheMappingAndOnlyValidPairsAreKept(t *testing.T) {
	answer := `{"mapping":{"CompanyName":"company","Form!B4":"revenue","PL":"lines","PL.A":"lines.label","PL.B":"lines.current",
		"revenue":"net_profit","Nowhere":"company","Form!B4.x":"lines.label"}}`
	var asked string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Model    string `json:"model"`
			Messages []struct {
				Content string `json:"content"`
			} `json:"messages"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		asked = body.Messages[len(body.Messages)-1].Content
		b, _ := json.Marshal(map[string]any{"model": body.Model, "choices": []any{map[string]any{
			"message": map[string]any{"content": answer}, "finish_reason": "stop"}}, "usage": map[string]any{"total_tokens": 10}})
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.Close)
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "deepseek-v4-flash", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	pkg := &Package{}
	pkg.Manifest.Capabilities.Required = []string{"model.text"}
	pkg.Manifest.Templates = json.RawMessage(am8Spec)
	rc := &runCtx{r: &Runner{Model: m, CreditsPerUSD: 1}, pkg: pkg, docsMayLeave: true}
	res := mustJSON(map[string]any{"slots": am8Slots(), "proposal": map[string]string{"revenue": "revenue"}})
	out, _, model := rc.proposeMapping(context.Background(), res)
	if model != "deepseek-v4-flash" {
		t.Fatalf("the model that proposed: %q", model)
	}
	var got struct {
		Proposal   map[string]string `json:"proposal"`
		ProposedBy string            `json:"proposed_by"`
	}
	_ = json.Unmarshal(out, &got)
	want := map[string]string{"CompanyName": "company", "Form!B4": "revenue", "PL": "lines", "PL.A": "lines.label",
		"PL.B": "lines.current", "revenue": "revenue"}
	if len(got.Proposal) != len(want) {
		t.Fatalf("proposal %v, want %v", got.Proposal, want)
	}
	for k, v := range want {
		if got.Proposal[k] != v {
			t.Fatalf("%s: %q, want %q (the identity a slot already had is kept; an unknown slot is dropped)", k, got.Proposal[k], v)
		}
	}
	if got.ProposedBy != "deepseek-v4-flash" {
		t.Fatalf("the screen says who proposed it: %q", got.ProposedBy)
	}
	if !strings.Contains(asked, "ยอดขายรวม") || !strings.Contains(asked, "ชื่อลูกค้า") || strings.Contains(asked, `"key":"revenue"`) {
		t.Fatalf("the model reads each open slot's label and context (not the ones already mapped):\n%s", asked)
	}
}

func TestNoModelThatMaySeeTheDocumentNoProposal(t *testing.T) {
	m := &ModelClient{BaseURL: "http://127.0.0.1:1", APIKey: "k", Primary: "deepseek-v4-flash", Timeout: time.Second, HTTP: http.DefaultClient}
	pkg := &Package{}
	pkg.Manifest.Capabilities.Required = []string{"model.text"}
	pkg.Manifest.Templates = json.RawMessage(am8Spec)
	rc := &runCtx{r: &Runner{Model: m}, pkg: pkg, docsMayLeave: false}
	res := mustJSON(map[string]any{"slots": am8Slots(), "proposal": map[string]string{"revenue": "revenue"}})
	out, credits, model := rc.proposeMapping(context.Background(), res)
	if model != "" || credits != 0 || string(out) != string(res) {
		t.Fatalf("documents may not leave and no model is in-house: nothing is sent, the screen starts from identity: %s %v %q", out, credits, model)
	}
}

func TestATemplateWithABlockingSlotCannotBeSaved(t *testing.T) {
	var spec am.TemplateSpec
	_ = json.Unmarshal([]byte(am8Spec), &spec)
	slots := append(am8Slots(), am.TemplateSlot{Key: "ppt/charts/chart1.xml", Kind: "refused", Label: "กราฟ 1", Blocking: true})
	why := am.CheckTemplateMapping(slots, map[string]string{"revenue": "revenue"}, spec)
	if !strings.Contains(why, "แบบฟอร์มนี้ใช้ไม่ได้") || !strings.Contains(why, "กราฟ 1") {
		t.Fatalf("a template that would deliver a chart with old numbers is refused before work: %q", why)
	}
}

func TestADeliveredTemplateCountsTheSlotsItLacks(t *testing.T) {
	arts := []am.Artifact{
		{Format: "xlsx", Validation: json.RawMessage(`{"passed":true,"checks":[]}`)},
		{Format: "template", Validation: json.RawMessage(`{"passed":false,"checks":[],"unfilled":[{"slot":"a"},{"slot":"b"}]}`)},
	}
	if n := unfilledTemplateSlots(arts); n != 2 {
		t.Fatalf("unfilled: %d", n)
	}
	arts[1].Validation = json.RawMessage(`{"passed":true,"checks":[],"unfilled":[]}`)
	if n := unfilledTemplateSlots(arts); n != 0 {
		t.Fatalf("a passed template lacks nothing: %d", n)
	}
}
