package runner

// AM2 Phase F end to end: the Expense Audit package through the real runner
// and sandbox image (AGENT_MARKET_IT=1). A month's claims, their receipts and
// the KB policy document in; the judgement cards answered with their
// defaults; every failed rule stored as an open run exception for a person.

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

const expenseFixtures = "backend/agent_market/fixtures/expense"

func (e *itEnv) setupExpenseInstall() am.Install {
	e.t.Helper()
	ctx := context.Background()
	if err := e.runner("reg").Register(ctx); err != nil {
		e.t.Fatal(err)
	}
	def, err := e.store.DefinitionBySlug(ctx, "sran.expense-audit")
	if err != nil {
		e.t.Fatal(err)
	}
	read := func(name string) string {
		b, err := os.ReadFile(filepath.Join(e.root, filepath.FromSlash(expenseFixtures), name))
		if err != nil {
			e.t.Fatal(err)
		}
		return string(b)
	}
	tx, _ := e.store.Pool.Begin(ctx)
	in, _, err := e.store.CreateInstall(ctx, tx, am.Install{TenantID: uuid.New(), UserID: uuid.New(), DefinitionID: def.ID,
		Slug: def.Slug, DefinitionVersion: def.Version, Checksum: def.Checksum, Title: "ตรวจค่าใช้จ่าย", BindingVersion: 1,
		BudgetCredits: 3_000_000}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for k, v := range map[string]string{"formats": `["xlsx"]`, "company": `"บริษัท สรานเทค โซลูชั่นส์ จำกัด"`,
		"company_tax_id": `"0105563012343"`, "policy": read("policy.json"), "holidays": read("holidays.json")} {
		if err := e.store.SetDefault(ctx, tx, in.ID, am.Default{Key: k, Value: json.RawMessage(v), Source: "install"}); err != nil {
			e.t.Fatal(err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		e.t.Fatal(err)
	}
	e.install = in
	return in
}

func (e *itEnv) expenseRun(files ...string) am.Run {
	e.t.Helper()
	ctx := context.Background()
	in := e.install
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Kind: am.KindRun, Message: "[AM2] ตรวจการเบิกเดือนตุลาคม", DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum,
		Formats: json.RawMessage(`["xlsx"]`)}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for _, rel := range files {
		data, err := os.ReadFile(filepath.Join(e.root, filepath.FromSlash(expenseFixtures), filepath.FromSlash(rel)))
		if err != nil {
			e.t.Fatal(err)
		}
		name := filepath.Base(rel)
		id := uuid.New()
		key := am.UploadKey(run.TenantID, run.InstallID, id, name)
		if err := e.files.Upload(ctx, key, strings.NewReader(string(data)), "", int64(len(data))); err != nil {
			e.t.Fatal(err)
		}
		rid := run.ID
		if _, err := e.store.CreateFile(ctx, e.store.Pool, am.File{ID: id, TenantID: run.TenantID, UserID: run.UserID,
			InstallID: run.InstallID, RunID: &rid, Name: name, Size: int64(len(data)), SHA256: fmt.Sprintf("%x", sha(data)),
			StorageKey: key, Status: "active"}); err != nil {
			e.t.Fatal(err)
		}
	}
	rid := run.ID
	_, _, _ = e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, &rid, "progress", am.Progress{RunID: run.ID.String()}, nil)
	return run
}

// answerWithDefaults closes every open card of a run with each question's default.
func (e *itEnv) answerWithDefaults(runID uuid.UUID) []string {
	e.t.Helper()
	ctx := context.Background()
	cards, _ := e.store.RunCards(ctx, runID)
	var keys []string
	all := map[string]am.Answer{}
	for _, c := range cards {
		if c.Status != "open" {
			continue
		}
		var qs []am.Question
		_ = json.Unmarshal(c.Questions, &qs)
		ans := map[string]am.Answer{}
		for _, q := range qs {
			keys = append(keys, q.Key)
			ans[q.Key] = am.Answer{Value: q.Default, Moment: "preflight", CardID: c.ID.String(), Via: "card", Code: q.Code}
			all[q.Key] = ans[q.Key]
		}
		ab, _ := json.Marshal(ans)
		if _, _, err := e.store.CloseCard(ctx, e.store.Pool, c.ID, "answered", ab, "card", nil); err != nil {
			e.t.Fatal(err)
		}
	}
	_ = e.store.MergeAnswers(ctx, e.store.Pool, runID, all)
	if ok, _ := e.store.Resume(ctx, e.store.Pool, runID); !ok {
		e.t.Fatal("resume")
	}
	sort.Strings(keys)
	return keys
}

func TestExpenseAuditLeavesEveryFailedRuleToAPerson(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupExpenseInstall()
	ctx := context.Background()
	files := []string{"claims_2569-10.xlsx", "policy_kb.docx"}
	receipts, _ := os.ReadDir(filepath.Join(e.root, filepath.FromSlash(expenseFixtures), "receipts"))
	for _, r := range receipts {
		files = append(files, "receipts/"+r.Name())
	}
	run := e.expenseRun(files...)
	r := e.runner("r1")
	got := e.drive(r, run.ID, 6*time.Minute, func(x am.Run) bool { return parked(x) || terminal(x) })
	if got.Status != am.RunAwaitingInput {
		t.Fatalf("the judgement calls are asked: %s %s", got.Status, got.StatusReason)
	}
	asked := e.answerWithDefaults(run.ID)
	want := []string{"expense_vague_purpose:C14", "expense_weekend_holiday:C07", "expense_weekend_holiday:C08"}
	if strings.Join(asked, ",") != strings.Join(want, ",") {
		t.Fatalf("questions: %v (want %v)", asked, want)
	}
	got = e.drive(r, run.ID, 6*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("after the answers: %s %s", got.Status, got.StatusReason)
	}
	if got.ModelUsed != nil {
		t.Fatalf("no model saw a digital receipt: %v", *got.ModelUsed)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 1 || arts[0].Format != "xlsx" {
		t.Fatalf("one audit workbook: %+v", arts)
	}

	rows, err := e.store.Pool.Query(ctx, `SELECT reason_code, severity, status, source_ref, choices
		FROM agent_market.agent_run_exceptions WHERE run_id = $1`, run.ID)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	codes := map[string][]string{} // claim -> review codes
	for rows.Next() {
		var code, sev, status string
		var ref, choices json.RawMessage
		if err := rows.Scan(&code, &sev, &status, &ref, &choices); err != nil {
			t.Fatal(err)
		}
		var s map[string]any
		_ = json.Unmarshal(ref, &s)
		var ch []map[string]any
		_ = json.Unmarshal(choices, &ch)
		switch {
		case sev == "review" && (status != "open" || len(ch) == 0):
			t.Fatalf("a review exception waits on a person with its choices: %s %s %s", code, status, choices)
		case sev == "info" && status != "noted":
			t.Fatalf("an info row is noted: %s %s", code, status)
		}
		for _, c := range ch {
			if v := fmt.Sprint(c["value"]); strings.Contains(strings.ToLower(v), "reject") {
				t.Fatalf("no automatic rejection is offered as a decision: %s", choices)
			}
		}
		if code == "over_claim_limit" && (s["claim"] != "C02" || s["limit_source"] != "policy_table") {
			t.Fatalf("C02's limit is the table's: %v", s)
		}
		claim, _ := s["claim"].(string)
		if sev == "review" {
			codes[claim] = append(codes[claim], code)
		}
	}
	for claim, want := range map[string]string{"C02": "over_claim_limit", "C04": "missing_receipt", "C06": "duplicate_claim",
		"C07": "weekend_or_holiday", "C08": "weekend_or_holiday", "C09": "prohibited_merchant",
		"C10": "vat_claim_without_full_tax_invoice", "C11": "wht_missing", "C12": "split_purchase", "C13": "split_purchase",
		"C14": "vague_purpose", "C16": "over_period_limit", "C17": "over_period_limit"} {
		found := false
		for _, c := range codes[claim] {
			found = found || c == want
		}
		if !found {
			t.Errorf("%s: %s is left to a person (got %v)", claim, want, codes[claim])
		}
	}
	for _, clean := range []string{"C01", "C03", "C05", "C15", "C18"} {
		if len(codes[clean]) > 0 {
			t.Errorf("%s passes every rule: %v", clean, codes[clean])
		}
	}
}
