package agentmarket

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM22 §2.4 — two labels that could take one value. A form that prints "ยอดตามธนาคาร" and "ยอดตามบัญชี" offers
// two blanks either of which could take a balance; which is which is the person's to say. A field the manifest says
// one slot takes ("once") given to two slots is refused at the save, naming both — never resolved by the code.

func am22Spec(t *testing.T, pkg string) TemplateSpec {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", pkg, "agent.json"))
	if err != nil {
		t.Fatal(err)
	}
	var m struct {
		Templates TemplateSpec `json:"templates"`
	}
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m.Templates
}

func TestAValueOneSlotTakesGivenToTwoSlotsIsRefusedNamingBoth(t *testing.T) {
	for pkg, field := range map[string]string{"bank_reconciliation": "bank_balance", "expense_audit": "claimed_thb", "po_matching": "value_passed"} {
		spec := am22Spec(t, pkg)
		if _, once := spec.Once(field); !once {
			t.Fatalf("%s: %s is a field one slot takes", pkg, field)
		}
		slots := []TemplateSlot{
			{Key: "S!E8", Kind: "target", Label: "ยอดตามธนาคาร", Fillable: true, Required: true},
			{Key: "S!E9", Kind: "target", Label: "ยอดตามบัญชี", Fillable: true, Required: true},
			{Key: "S!C3", Kind: "target", Label: "ชื่อบริษัท", Fillable: true, Required: true},
			{Key: "S!C4", Kind: "target", Label: "ชื่อบริษัท (ภาษาอังกฤษ)", Fillable: true, Required: true},
		}
		both := map[string]string{"S!E8": field, "S!E9": field}
		msg := CheckTemplateMapping(Thai, slots, both, spec)
		if !strings.Contains(msg, "ยอดตามธนาคาร") || !strings.Contains(msg, "ยอดตามบัญชี") || !strings.Contains(msg, "ใส่ได้ช่องเดียว") {
			t.Fatalf("%s: refused, naming both: %q", pkg, msg)
		}
		if en := CheckTemplateMapping(English, slots, both, spec); !strings.Contains(en, "take the same value") || strings.ContainsFunc(stripQuoted(en), isThai) {
			t.Fatalf("%s: the English refusal: %q", pkg, en)
		}
		if msg := CheckTemplateMapping(Thai, slots, map[string]string{"S!E8": field}, spec); msg != "" {
			t.Fatalf("%s: one slot is fine: %q", pkg, msg)
		}
		// a name may be printed twice on a form (a header and a signature block): only "once" fields are held to one
		if msg := CheckTemplateMapping(Thai, slots, map[string]string{"S!C3": "company", "S!C4": "company"}, spec); msg != "" {
			t.Fatalf("%s: the company's name in two places: %q", pkg, msg)
		}
	}
}

// stripQuoted takes out the customer's own labels (“…”), which stay as they are in any language.
func stripQuoted(s string) string {
	var b strings.Builder
	in := false
	for _, r := range s {
		switch {
		case r == '“':
			in = true
		case r == '”':
			in = false
		case !in:
			b.WriteRune(r)
		}
	}
	return b.String()
}

// Every money figure of the three new forms is a field one slot takes: a balance or a total written into two
// blanks of one form is the wrong-cell failure this card is about.
func TestEveryMoneyFieldOfTheThreeNewFormsIsOneSlots(t *testing.T) {
	for _, pkg := range []string{"bank_reconciliation", "expense_audit", "po_matching"} {
		spec := am22Spec(t, pkg)
		if len(spec.Fields) < 8 || len(spec.Lists) == 0 {
			t.Fatalf("%s: the templates block is there: %d fields, %d lists", pkg, len(spec.Fields), len(spec.Lists))
		}
		for k, raw := range spec.Fields {
			var f struct {
				Label string `json:"label"`
				Once  bool   `json:"once"`
			}
			_ = json.Unmarshal(raw, &f)
			if strings.Contains(f.Label, "(บาท)") && !f.Once && k != "advance_cleared" {
				t.Errorf("%s: %s (%s) is money and is not a field one slot takes", pkg, k, f.Label)
			}
		}
	}
}

// AM22 §2.4 — a round with nothing to put in a form: no file, and the form's card says why, in the package's words.
func TestAFormTheRoundHadNothingForSaysWhyOnItsCard(t *testing.T) {
	f := RoundForm{FileID: uuid.New(), Name: "ใบกระทบยอด.xlsx", Kind: "xlsx", Status: FormFailed, Why: FormNothingThisRound,
		WhyText: "รอบนี้ไม่มียอดคงเหลือของฝั่งธนาคารหรือฝั่งบัญชี จึงไม่มีงบกระทบยอดให้กรอกลงแบบฟอร์ม"}
	th := FormText(f).In(Thai)
	if !strings.Contains(th, "ไม่ได้กรอก ใบกระทบยอด.xlsx ในรอบนี้") || !strings.Contains(th, "รอบนี้ไม่มียอดคงเหลือ") || strings.Contains(th, "ใช้แบบฟอร์ม") {
		t.Fatalf("the card says the form was not filled and why — not that it cannot be used: %q", th)
	}
	if en := FormText(f).In(English); !strings.Contains(en, "was not filled in this run") {
		t.Fatalf("English: %q", en)
	}
	p := FormItemPayload(uuid.New(), f)
	if p.Reason != FormNothingThisRound || !strings.Contains(p.Why.In(Thai), "รอบนี้ไม่มียอดคงเหลือ") {
		t.Fatalf("the card's reason and why: %q %q", p.Reason, p.Why.In(Thai))
	}
}
