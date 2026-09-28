package runner

// AM5 phase E end to end: the bank reconciliation on the real runner and the
// real sandbox image (AGENT_MARKET_IT=1). One month that ties, one that must
// not — and the same input twice.

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

const bankFixtures = "backend/agent_market/packages/bank_reconciliation/evals/fixtures"

func (e *itEnv) setupBankInstall() am.Install {
	e.t.Helper()
	ctx := context.Background()
	if err := e.runner("reg").Register(ctx); err != nil {
		e.t.Fatal(err)
	}
	def, err := e.store.DefinitionBySlug(ctx, "sran.bank-reconciliation")
	if err != nil {
		e.t.Fatal(err)
	}
	tx, _ := e.store.Pool.Begin(ctx)
	in, _, err := e.store.CreateInstall(ctx, tx, am.Install{TenantID: uuid.New(), UserID: uuid.New(), DefinitionID: def.ID,
		Slug: def.Slug, DefinitionVersion: def.Version, Checksum: def.Checksum, Title: "กระทบยอดธนาคาร", BindingVersion: 1,
		BudgetCredits: 3_000_000}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for k, v := range map[string]string{"formats": `["xlsx"]`, "company": `"บริษัท สยามตัวอย่างการค้า จำกัด"`,
		"bank_name": `"ธนาคารกสิกรไทย"`, "bank_account_number": `"123-4-56789-0"`} {
		_ = e.store.SetDefault(ctx, tx, in.ID, am.Default{Key: k, Value: json.RawMessage(v), Source: "install"})
	}
	if err := tx.Commit(ctx); err != nil {
		e.t.Fatal(err)
	}
	e.install = in
	return in
}

func (e *itEnv) bankRun(files ...string) am.Run {
	e.t.Helper()
	ctx := context.Background()
	in := e.install
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Kind: am.KindRun, Message: "[AM5] กระทบยอดธนาคาร", DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum,
		Formats: json.RawMessage(`["xlsx"]`)}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for _, name := range files {
		data, err := os.ReadFile(filepath.Join(e.root, filepath.FromSlash(bankFixtures), name))
		if err != nil {
			e.t.Fatal(err)
		}
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

// computedOf is the run's computed table as the runner stored it.
func (e *itEnv) computedOf(run uuid.UUID) []byte {
	e.t.Helper()
	for _, s := range e.steps(run) {
		if s.Name != "compute" || s.Status != "succeeded" {
			continue
		}
		var o struct {
			Computed blobRef `json:"computed"`
		}
		if err := json.Unmarshal(s.Output, &o); err != nil {
			e.t.Fatal(err)
		}
		body, _, _, err := e.files.Download(context.Background(), o.Computed.Key)
		if err != nil {
			e.t.Fatal(err)
		}
		defer body.Close()
		var buf strings.Builder
		b := make([]byte, 1<<16)
		for {
			n, err := body.Read(b)
			buf.Write(b[:n])
			if err != nil {
				break
			}
		}
		return []byte(buf.String())
	}
	e.t.Fatal("the run has no computed table")
	return nil
}

func TestAReconciliationThatTiesSucceeds(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	ctx := context.Background()
	run := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	got := e.drive(e.runner("r1"), run.ID, 5*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("a month that ties: %s %s", got.Status, got.StatusReason)
	}
	if cards, _ := e.store.RunCards(ctx, run.ID); len(cards) != 0 {
		t.Fatalf("the clean month asks nothing: %d cards", len(cards))
	}
	outs := e.outputsOf(run.ID)
	if len(outs) != 1 || outs[0]["draft"] == true {
		t.Fatalf("one files card, not a draft: %v", outs)
	}
	sum := outs[0]["summary"].(map[string]any)
	if sum["tied"] != true || sum["adjusted_bank"] != "1538519.65" || sum["adjusted_book"] != "1538519.65" || sum["difference"] != "0.00" {
		t.Fatalf("the two adjusted balances: %v", sum)
	}
	if sum["matched"].(float64) != 10 || sum["by_rule"].(float64) != 10 || sum["by_person"].(float64) != 0 {
		t.Fatalf("ten pairs, each by a rule of the exact tier: %v", sum)
	}
	gate := outs[0]["gate"].(map[string]any)
	if gate["passed"] != true || !strings.HasPrefix(gate["what"].(string), "ลงตัว") {
		t.Fatalf("the gate: %v", gate)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 1 || arts[0].Format != "xlsx" {
		t.Fatalf("the working paper: %+v", arts)
	}
	var g *am.Step
	for _, s := range e.steps(run.ID) {
		if s.Name == "gate" {
			s := s
			g = &s
		}
	}
	if g == nil || g.Status != "succeeded" || g.Kind != "check" {
		t.Fatalf("the gate is a step of the run: %+v", g)
	}
	// no step of the round asked a model anything (the runner itself lists the
	// gateway's models when it registers; that is not this round)
	for _, s := range e.steps(run.ID) {
		if s.Kind == "ai" || s.ModelID != "" {
			t.Fatalf("step %s used a model", s.Name)
		}
	}
	readable(t, "the thread", e.visibleThread())

	// the same two files again: the same computed table, byte for byte
	again := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	if done := e.drive(e.runner("r1"), again.ID, 5*time.Minute, terminal); done.Status != am.RunSucceeded {
		t.Fatalf("again: %s %s", done.Status, done.StatusReason)
	}
	a, b := e.computedOf(run.ID), e.computedOf(again.ID)
	if string(a) != string(b) {
		t.Fatalf("the same input twice: %d bytes and %d bytes differ", len(a), len(b))
	}
	var c struct {
		Match struct {
			Groups map[string][]json.RawMessage `json:"groups"`
		} `json:"match"`
	}
	_ = json.Unmarshal(a, &c)
	if len(c.Match.Groups["matched"]) != 10 || len(c.Match.Groups["missing"]) != 3 || len(c.Match.Groups["extra"]) != 4 {
		t.Fatalf("the four groups: %d matched %d missing %d extra %d suspect", len(c.Match.Groups["matched"]),
			len(c.Match.Groups["missing"]), len(c.Match.Groups["extra"]), len(c.Match.Groups["suspect"]))
	}
}

func TestAReconciliationThatDoesNotTieIsNotCalledASuccess(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupBankInstall()
	ctx := context.Background()
	run := e.bankRun("kbank_sep_page_missing.xlsx", "ledger_sep.xlsx")
	got := e.drive(e.runner("r1"), run.ID, 5*time.Minute, terminal)
	if got.Status == am.RunSucceeded {
		t.Fatal("a reconciliation whose adjusted balances differ was called a success")
	}
	if got.Status != am.RunFailed || got.StatusReason != "not_tied" {
		t.Fatalf("%s %s", got.Status, got.StatusReason)
	}
	text, f := lastFailure(t, &failEnv{t: t, store: e.store, install: e.install}, got)
	if !strings.HasPrefix(text, "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท") || f["what"] != "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท" {
		t.Fatalf("it says so: %q %v", text, f)
	}
	if f["can_retry"] != false || !strings.Contains(f["next"].(string), "รายการเดินบัญชีฉบับเต็ม") {
		t.Fatalf("and what to do next: %v", f)
	}
	// the working paper is delivered, as a draft
	outs := e.outputsOf(run.ID)
	if len(outs) != 1 || outs[0]["draft"] != true {
		t.Fatalf("the draft: %v", outs)
	}
	if g := outs[0]["gate"].(map[string]any); g["passed"] != false || g["what"] != "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท" {
		t.Fatalf("the gate on the files card: %v", g)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 1 {
		t.Fatalf("the working paper: %+v", arts)
	}
	// nobody is told "เสร็จแล้ว"
	for _, s := range e.visibleThread() {
		if strings.Contains(s, "เสร็จแล้วครับ") {
			t.Fatalf("the round says it is done: %q", s)
		}
	}
	// what a person must look at is recorded, and the round cannot be signed
	exc, _ := e.store.RunExceptions(ctx, run.ID)
	if len(exc) == 0 {
		t.Fatal("the break in the statement's balance is an exception for a person")
	}
	srv := e.api()
	if code, body := post(t, srv, "/runs/"+run.ID.String()+"/review", map[string]any{}); code != 409 {
		t.Fatalf("signing a reconciliation that does not tie: %d %v", code, body)
	}
	readable(t, "a round that does not tie", e.visibleThread())
}

func TestAPairAPersonConfirmedNamesThatPerson(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	in := e.setupBankInstall()
	ctx := context.Background()
	// the person who will answer has a name in the organisation
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_settings (org_id) VALUES ('default') ON CONFLICT DO NOTHING`); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_members (org_id, email, user_id, display_name)
		VALUES ('default', 'am5-it@example.test', $1, 'สมหญิง ผู้จัดทำ')`, in.UserID); err != nil {
		t.Fatal(err)
	}
	srv := e.api()
	run := e.bankRun("kbank_sep_candidates.xlsx", "ledger_sep_candidates.xlsx")
	e.drive(e.runner("r1"), run.ID, 5*time.Minute, parked)
	cards, _ := e.store.RunCards(ctx, run.ID)
	if len(cards) != 1 {
		t.Fatalf("one card: %d", len(cards))
	}
	var qs []am.Question
	_ = json.Unmarshal(cards[0].Questions, &qs)
	if len(qs) != 2 || qs[0].Code != "e3_candidate" || qs[0].DefaultLabel != "ยังไม่แน่ใจ" {
		t.Fatalf("two proposed pairs, neither matched by default: %+v", qs)
	}
	answers := map[string]any{}
	for _, q := range qs {
		answers[q.Key] = map[string]any{"labels": []string{"เป็นรายการเดียวกัน"}}
	}
	if code, body := post(t, srv, "/cards/"+cards[0].ID.String()+"/answer", map[string]any{"answers": answers}); code != 200 {
		t.Fatalf("answer: %d %v", code, body)
	}
	got := e.drive(e.runner("r1"), run.ID, 5*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("confirmed, it ties: %s %s", got.Status, got.StatusReason)
	}
	var c struct {
		Match struct {
			Groups struct {
				Matched []struct {
					Pass, Tier, Confirmed string
					ConfirmedBy           string `json:"confirmed_by"`
				} `json:"matched"`
			} `json:"groups"`
		} `json:"match"`
	}
	if err := json.Unmarshal(e.computedOf(run.ID), &c); err != nil {
		t.Fatal(err)
	}
	byPerson := 0
	for _, m := range c.Match.Groups.Matched {
		switch {
		case m.Tier == "exact" && (m.Confirmed != "rule" || m.ConfirmedBy != ""):
			t.Fatalf("an exact pair is a rule's: %+v", m)
		case m.Tier != "exact" && (m.Confirmed != "person" || m.ConfirmedBy != "สมหญิง ผู้จัดทำ" || m.Pass == ""):
			t.Fatalf("a pair below exact names its pass, its tier and who confirmed it: %+v", m)
		}
		if m.Confirmed == "person" {
			byPerson++
		}
	}
	if byPerson != 2 || len(c.Match.Groups.Matched) != 12 {
		t.Fatalf("%d pairs, %d by a person", len(c.Match.Groups.Matched), byPerson)
	}
}
