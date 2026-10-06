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
