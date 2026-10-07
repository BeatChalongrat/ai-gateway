package agentmarket

import (
	"encoding/json"
	"strings"
	"testing"
)

// AM13 — the other four agents do what they are asked: an order for the table, and nothing else.
//
// Two rules the platform holds for them, so that no package has to:
//
//   - a table has one order. Two directives of one group are never both given to a renderer: of two
//     asked in the same words one is applied and the other is said, by name, not to have been; asked in
//     a later message, the new one takes the place of the one in force;
//   - an agent whose round starts from what a person types (procurement) is typed a request — a thing
//     to find, how many, within what budget. None of that is a request about its documents, and none of
//     it is ever answered with “cannot be done as asked”.

var registerAsks = InstructionSpec{
	Can: []Directive{
		{ID: "sort_by_seller", Label: "เรียงทะเบียนตามผู้ขาย", Group: "order"},
		{ID: "sort_by_date", Label: "เรียงทะเบียนตามวันที่เอกสาร", Group: "order"},
		{ID: "sort_by_amount", Label: "เรียงทะเบียนจากยอดรวมสูงสุด", Group: "order"},
		{ID: "wide_print", Label: "พิมพ์แนวนอน"},
	},
	Cannot: []Directive{{ID: "split_by_seller", Label: "แยกชีตตามผู้ขาย", Why: "สมุดงานมีทะเบียนชุดเดียว"}},
}

func TestAM13TwoOrdersOfOneTableAreNeverBothApplied(t *testing.T) {
	// the model's order is not the package's; three of one group and one of none
	raw := json.RawMessage(`{"apply": ["sort_by_amount", "wide_print", "sort_by_date", "sort_by_seller"], "cannot": []}`)
	got := ReadAsked(registerAsks, "เรียงตามผู้ขาย เรียงตามวันที่ เรียงจากยอดมาก พิมพ์แนวนอน", raw)
	if strings.Join(got.Apply, " ") != "sort_by_seller wide_print" {
		t.Fatalf("one of the group — the first the package lists — and what is of no group: %v", got.Apply)
	}
	if len(got.Instead) != 2 || got.Instead[0] != (AskedInstead{ID: "sort_by_date", Kept: "sort_by_seller"}) ||
		got.Instead[1] != (AskedInstead{ID: "sort_by_amount", Kept: "sort_by_seller"}) {
		t.Fatalf("each that gave way, with what it gave way to: %+v", got.Instead)
	}
	said := AskedOneAtATime(registerAsks, got.Instead)
	if len(said) != 2 {
		t.Fatalf("one sentence for each: %d", len(said))
	}
	th, en := said[0].In(Thai), said[0].In(English)
	for _, want := range []string{"“เรียงทะเบียนตามวันที่เอกสาร” ทำตามที่ขอไม่ได้เพราะ", "“เรียงทะเบียนตามผู้ขาย”", "ทีละอย่าง"} {
		if !strings.Contains(th, want) {
			t.Fatalf("the Thai names what was not done, what was, and why — missing %q in %q", want, th)
		}
	}
	if en == "" || en == th || !strings.Contains(en, "cannot be done as asked because") || !strings.Contains(en, "one at a time") {
		t.Fatalf("the sentence has its English: %q", en)
	}

	// asked alone, nothing gives way and nothing is said
	alone := ReadAsked(registerAsks, "เรียงตามวันที่", json.RawMessage(`{"apply": ["sort_by_date"], "cannot": []}`))
	if strings.Join(alone.Apply, " ") != "sort_by_date" || len(alone.Instead) != 0 || len(AskedOneAtATime(registerAsks, alone.Instead)) != 0 {
		t.Fatalf("one order asked is one order applied: %+v", alone)
	}

	// words that asked only for two orders asked something: they are not "nothing" even if the kept
	// one were later dropped
	if (Asked{Instead: []AskedInstead{{ID: "sort_by_date", Kept: "sort_by_seller"}}}).Nothing() {
		t.Fatal("a request that gave way is still a request that was made")
	}
}

func TestAM13WhatIsAskedLaterTakesThePlaceOfTheOrderInForce(t *testing.T) {
	for _, c := range []struct{ inForce, asked, want string }{
		{"sort_by_amount", "sort_by_date", "sort_by_date"},                       // the table was by amount; it is by date
		{"sort_by_amount wide_print", "sort_by_date", "wide_print sort_by_date"}, // what is of no group stays
		{"sort_by_date", "wide_print", "sort_by_date wide_print"},                // nothing of its group asked: it stays
		{"sort_by_date wide_print", "sort_by_date", "wide_print sort_by_date"},   // asked again: once
		{"", "sort_by_seller", "sort_by_seller"},
		{"sort_by_seller sort_by_amount", "sort_by_date", "sort_by_date"}, // (a list from before this rule: both go)
		{"gone_from_the_package sort_by_date", "wide_print", "gone_from_the_package sort_by_date wide_print"},
	} {
		got := registerAsks.Merge(strings.Fields(c.inForce), strings.Fields(c.asked))
		if strings.Join(got, " ") != c.want {
			t.Errorf("in force [%s], asked [%s]: got %v, want [%s]", c.inForce, c.asked, got, c.want)
		}
	}
	// (before AM13 the two lists were simply joined: a renderer was handed two orders for one table)
}

var buyingAsks = InstructionSpec{
	Can:    []Directive{{ID: "sort_by_price", Label: "เรียงตัวเลือกจากราคาต่ำสุด"}},
	Cannot: []Directive{{ID: "pick_best", Label: "บอกว่าตัวเลือกใดดีที่สุด", Why: "agent นี้ไม่ตัดสินว่าตัวเลือกใดดีที่สุด การเลือกเป็นของคน"}},
}

func TestAM13WhatAPersonAsksAnAgentToFindIsNeverRefusedAsARequestAboutItsDocuments(t *testing.T) {
	const typed = "หาเก้าอี้สำนักงาน 10 ตัว งบไม่เกิน 5,000 บาทต่อตัว ส่งกรุงเทพ เรียงจากถูกสุด แล้วบอกด้วยว่าเจ้าไหนดีที่สุด"
	// a model that took the budget and the place for requests about the document
	raw := json.RawMessage(`{"apply": ["sort_by_price"], "cannot": [{"id": "other", "words": "งบไม่เกิน 5,000 บาทต่อตัว"},
		{"id": "other", "words": "ส่งกรุงเทพ"}, {"id": "pick_best", "words": "บอกด้วยว่าเจ้าไหนดีที่สุด"}]}`)
	read := ReadAsked(buyingAsks, typed, raw)
	if len(read.Cannot) != 3 {
		t.Fatalf("as read, the model's three: %+v", read.Cannot)
	}
	got := read.OfARequest()
	if strings.Join(got.Apply, " ") != "sort_by_price" {
		t.Fatalf("what can be asked of the documents is still applied: %v", got.Apply)
	}
	if len(got.Cannot) != 1 || got.Cannot[0].ID != "pick_best" || got.Cannot[0].Words != "บอกด้วยว่าเจ้าไหนดีที่สุด" {
		t.Fatalf("only what the package lists as not possible is left to be said: %+v", got.Cannot)
	}
	for _, line := range AskedNotDone(buyingAsks, nil, got.Cannot) {
		if strings.Contains(line.In(Thai), "งบไม่เกิน") || strings.Contains(line.In(Thai), "ส่งกรุงเทพ") {
			t.Fatalf("the request itself was answered with a refusal: %s", line.In(Thai))
		}
	}
	if len(read.Cannot) != 3 {
		t.Fatal("OfARequest changed the value it was called on")
	}
	// only a request: nothing is asked of the documents, and nothing is said
	only := ReadAsked(buyingAsks, "หาเก้าอี้ 10 ตัว งบ 5,000", json.RawMessage(`{"apply": [], "cannot": [{"id": "other", "words": "งบ 5,000"}]}`)).OfARequest()
	if !only.Nothing() {
		t.Fatalf("a request and nothing else asks nothing of the documents: %+v", only)
	}

	// the model is told, in the user turn — the system message is the same for every agent and holds no JSON
	system, user := AskedPrompt(buyingAsks, typed, true)
	plainSystem, plainUser := AskedPrompt(buyingAsks, typed)
	if system != plainSystem || strings.ContainsAny(system, "{}") {
		t.Fatal("the system message changed, or holds a shape the echo guard would cut out of an answer")
	}
	if !strings.Contains(user, "เริ่มงานจากข้อความนี้เอง") || strings.Contains(plainUser, "เริ่มงานจากข้อความนี้เอง") {
		t.Fatal("an agent that starts from a request says so to the model, and only such an agent")
	}
	if !strings.HasSuffix(user, plainUser) {
		t.Fatal("the rest of the question is the same question")
	}
}

func TestAM13ADirectivesGroupIsReadFromTheManifest(t *testing.T) {
	var m Manifest
	if err := json.Unmarshal([]byte(`{"instructions": {"can": [
		{"id": "sort_by_amount", "label": "เรียงจากยอดมาก", "group": "order"},
		{"id": "sort_by_date", "label": "เรียงตามวันที่", "group": "order"}], "cannot": []}}`), &m); err != nil {
		t.Fatal(err)
	}
	if !m.TakesInstructions() || m.Instructions.Can[0].Group != "order" || m.Instructions.Can[1].Group != "order" {
		t.Fatalf("the group of each directive: %+v", m.Instructions)
	}
}
