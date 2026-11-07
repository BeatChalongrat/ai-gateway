package agentmarket

import (
	"sort"
	"strings"
	"testing"
)

// AM11b A — a person using an agent never sees credits or what a run costs: not where a run starts, not
// on a failure, not on a reminder, not in a refusal. Cost is the admin's business and has its page.
//
// This reads every sentence the server can say (the control plane's and the runner's, with their
// English) and every refusal message, and fails on one that names credits — except the accounting word
// (เดบิต/เครดิต, debit/credit: a ledger's columns are the work, not the price) and the few sentences only
// the admin app shows, each listed here with where it is shown.
//
// Amounts in baht are not looked for: an invoice's total, a policy's limit and a difference between two
// balances are what the agents work on.
func TestAM11bNoSentenceToAPersonNamesCredits(t *testing.T) {
	adminOnly := map[string]string{
		"งบเครดิตต่อคนต่อเดือน": "admin app › Agent Market › the audit trail's name of a setting",
	}
	adminMessages := map[string]string{
		"BAD_BUDGET": "the admin door's answer to a budget typed in the admin app",
	}
	names := func(th, en string) bool {
		th, en = strings.ToLower(th), strings.ToLower(en)
		return (strings.Contains(th, "เครดิต") && !strings.Contains(th, "เดบิต")) ||
			(strings.Contains(en, "credit") && !strings.Contains(en, "debit"))
	}
	var found []string
	for th, en := range bothPlanesEnglish(t) {
		if adminOnly[th] == "" && names(th, en) {
			found = append(found, th+"  ⇒  "+en)
		}
	}
	for code, th := range userMessages {
		if adminMessages[code] == "" && names(th, userMessagesEn[code]) {
			found = append(found, code+": "+th)
		}
	}
	for code, en := range reminderMailMessagesEn {
		if names("", en) {
			found = append(found, "mail message "+code+": "+en)
		}
	}
	sort.Strings(found)
	for _, f := range found {
		t.Errorf("a sentence to a person names credits: %s", f)
	}
	// the test can fail: the accounting word alone is let through, the price is not
	if names("อ่านครบแล้ว เดบิตกับเครดิตสมดุลดี", "debit and credit balance") || !names("รอบนี้ใช้ไป 12 เครดิต", "") || !names("", "No credits are charged.") {
		t.Fatal("the rule itself is wrong")
	}
}

// AM11b B — the model picker must not say one thing while the agent does another. Whatever a person
// picks is what runs; a model that cannot be used for this agent is not in the list — never offered and
// then quietly swapped; and a pick made while a model could be used is no pick once it cannot.
func TestAM11bTheMenuListsOnlyWhatCanRun(t *testing.T) {
	rm := RunnerModels{Primary: "qwen", Fallback: "deepseek", Vision: "qwen",
		SelfHosted: map[string]bool{"qwen": true, "typhoon": true},
		Catalog:    []CatalogModel{{ID: "qwen", Vision: true}, {ID: "typhoon"}, {ID: "deepseek"}, {ID: "gpt-4o", Vision: true}}}
	text := Manifest{Narrative: "model"}
	text.Capabilities.Required = []string{"model.text"}
	ids := func(p ModelPicker) string {
		var out []string
		for _, m := range p.Models {
			if !m.Allowed {
				t.Fatalf("a model in the list that cannot be picked: %+v", m)
			}
			out = append(out, m.ID)
		}
		return strings.Join(out, " ")
	}
	// this agent's data stays inside: the outside models are simply absent, and the menu knows why
	closed := MenuFor(Install{ModelChoice: "deepseek"}, text, Egress{Org: true, Agent: false, Effective: false, Words: Text("ผู้ดูแลตั้งให้ agent นี้ไม่ส่งเอกสารออกนอกองค์กร")}, rm)
	if got := ids(closed); got != "qwen typhoon" || !closed.OutsideAbsent || closed.BlockedReason.Empty() {
		t.Fatalf("closed: %q outside_absent=%v reason=%q", got, closed.OutsideAbsent, closed.BlockedReason)
	}
	// the pick of an outside model from before is no pick: the menu shows what will run
	if closed.Choice != ModelAuto {
		t.Fatalf("a stale pick is shown as chosen: %q", closed.Choice)
	}
	if ok, why := closed.ChoiceAllowed("deepseek"); ok || why.Empty() || strings.Contains(why.String(), "ไม่อยู่ในรายการโมเดล") {
		t.Fatalf("picking it is refused with the reason it is absent: %v %q", ok, why)
	}
	// its data may leave: every model that can do the job is a row, nothing is absent, the pick stands
	open := MenuFor(Install{ModelChoice: "deepseek"}, text, everyoneNamed(Egress{Org: true, Agent: true, Effective: true}, &rm), rm)
	if got := ids(open); got != "deepseek gpt-4o qwen typhoon" || open.OutsideAbsent || !open.BlockedReason.Empty() || open.Choice != "deepseek" {
		t.Fatalf("open: %q outside_absent=%v reason=%q choice=%q", got, open.OutsideAbsent, open.BlockedReason, open.Choice)
	}
}

// AM11b B — a model that cannot take an agent's question is not in its menu. Found by the walk: an
// in-house model with a 16,384-token window was listed for the report agent, picked, refused by the
// gateway (context_length_exceeded), and another model wrote the report.
func TestAM11bAModelWhoseWindowCannotTakeTheQuestionIsNotListed(t *testing.T) {
	rm := RunnerModels{Primary: "qwen", Vision: "qwen", SelfHosted: map[string]bool{"qwen": true, "small-8b": true, "mid": true, "unstated": true},
		Catalog: []CatalogModel{{ID: "qwen", Vision: true, Context: 131072}, {ID: "small-8b", Context: 16384}, {ID: "mid", Context: 32768}, {ID: "unstated"}}}
	text := Manifest{Narrative: "model"}
	text.Capabilities.Required = []string{"model.text"}
	p := MenuFor(Install{ModelChoice: "small-8b"}, text, Egress{}, rm)
	var ids []string
	for _, m := range p.Models {
		ids = append(ids, m.ID)
	}
	if got := strings.Join(ids, " "); got != "mid qwen unstated" {
		t.Fatalf("the menu: %q", got)
	}
	// a pick of it from before is no pick, and picking it now is refused
	if ok, _ := p.ChoiceAllowed("small-8b"); ok || p.Choice != ModelAuto {
		t.Fatalf("a model with a 16k window can still be chosen: allowed=%v choice=%q", ok, p.Choice)
	}
	// the runner's own configured model is taken as able, whatever its stated window
	rm.Catalog[0].Context = 8192
	if p := MenuFor(Install{}, text, Egress{}, rm); len(p.Models) == 0 || p.Models[1].ID != "qwen" {
		t.Fatalf("the runner's own model left the menu: %+v", p.Models)
	}
}
