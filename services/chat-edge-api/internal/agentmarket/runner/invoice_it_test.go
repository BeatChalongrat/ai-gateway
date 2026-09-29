package runner

// AM2 Phase D end to end: the Invoice Processing package through the real
// runner, the real sandbox image and a fake gateway (AGENT_MARKET_IT=1).

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

const e2Docs = "backend/agent_market/fixtures/e2/docs"

func (e *itEnv) setupInvoiceInstall() am.Install {
	e.t.Helper()
	ctx := context.Background()
	if err := e.runner("reg").Register(ctx); err != nil {
		e.t.Fatal(err)
	}
	def, err := e.store.DefinitionBySlug(ctx, "sran.invoice-processing")
	if err != nil {
		e.t.Fatal(err)
	}
	tx, _ := e.store.Pool.Begin(ctx)
	in, _, err := e.store.CreateInstall(ctx, tx, am.Install{TenantID: uuid.New(), UserID: uuid.New(), DefinitionID: def.ID,
		Slug: def.Slug, DefinitionVersion: def.Version, Checksum: def.Checksum, Title: "ทะเบียนซื้อ", BindingVersion: 1,
		BudgetCredits: 3_000_000}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for k, v := range map[string]string{"formats": `["xlsx"]`, "company": `"บริษัท สรานเทค โซลูชั่นส์ จำกัด"`,
		"company_tax_id": `"0105563012343"`, "company_branch": `"00000"`} {
		_ = e.store.SetDefault(ctx, tx, in.ID, am.Default{Key: k, Value: json.RawMessage(v), Source: "install"})
	}
	if err := tx.Commit(ctx); err != nil {
		e.t.Fatal(err)
	}
	e.install = in
	return in
}

func (e *itEnv) invoiceRun(files ...string) am.Run {
	e.t.Helper()
	ctx := context.Background()
	in := e.install
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Kind: am.KindRun, Message: "[AM2] ทำทะเบียนซื้อ", DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum,
		Formats: json.RawMessage(`["xlsx"]`)}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for _, name := range files {
		data, err := os.ReadFile(filepath.Join(e.root, filepath.FromSlash(e2Docs), name))
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
	_, _, _ = e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, &rid, am.Progress{RunID: run.ID.String()}, nil)
	return run
}

func (e *itEnv) indexRows() int {
	var n int
	_ = e.store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM agent_market.document_index WHERE install_id = $1`,
		e.install.ID).Scan(&n)
	return n
}

func TestInvoiceRunRecordsThenCatchesTheDuplicate(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInvoiceInstall()
	ctx := context.Background()
	// 1 · a clean tax invoice: no question, a register, the document indexed
	run := e.invoiceRun("d01_clean_full_tax_invoice.pdf")
	got := e.drive(e.runner("r1"), run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("clean invoice: %s %s", got.Status, got.StatusReason)
	}
	if cards, _ := e.store.RunCards(ctx, run.ID); len(cards) != 0 {
		t.Fatalf("the clean invoice asks nothing: %d cards", len(cards))
	}
	steps := e.steps(run.ID)
	if countSteps(steps, "vision:") != 0 {
		t.Fatal("a digital PDF is never sent to a vision model")
	}
	for _, s := range steps {
		if s.Name == "narrative" && (s.Kind != "compute" || s.ModelID != "") {
			t.Fatalf("the invoice agent's summary is written by rules: %+v", s)
		}
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 1 || arts[0].Format != "xlsx" {
		t.Fatalf("one register workbook: %+v", arts)
	}
	if got.ModelUsed != nil {
		t.Fatalf("no model saw a digital document: %v", *got.ModelUsed)
	}
	if n := e.indexRows(); n != 1 {
		t.Fatalf("the recorded document joins the duplicate index: %d rows", n)
	}
	// 2 · the same invoice in a new run: a duplicate card; "not a duplicate" records it
	run2 := e.invoiceRun("d01_clean_full_tax_invoice.pdf")
	e.drive(e.runner("r1"), run2.ID, 4*time.Minute, parked)
	cards, _ := e.store.RunCards(ctx, run2.ID)
	if len(cards) != 1 {
		t.Fatalf("one card: %d", len(cards))
	}
	var qs []am.Question
	_ = json.Unmarshal(cards[0].Questions, &qs)
	if len(qs) != 1 || !strings.HasPrefix(qs[0].Key, "e2_duplicate:") {
		t.Fatalf("the duplicate question: %+v", qs)
	}
	ans := map[string]am.Answer{qs[0].Key: {Value: json.RawMessage(`"include"`), Moment: "preflight", CardID: cards[0].ID.String(),
		Via: "card", Code: "e2_duplicate"}}
	ab, _ := json.Marshal(ans)
	if _, _, err := e.store.CloseCard(ctx, e.store.Pool, cards[0].ID, "answered", ab, "card", nil); err != nil {
		t.Fatal(err)
	}
	_ = e.store.MergeAnswers(ctx, e.store.Pool, run2.ID, ans)
	if ok, _ := e.store.Resume(ctx, e.store.Pool, run2.ID); !ok {
		t.Fatal("resume")
	}
	got2 := e.drive(e.runner("r1"), run2.ID, 4*time.Minute, terminal)
	if got2.Status != am.RunSucceeded {
		t.Fatalf("answered: %s %s", got2.Status, got2.StatusReason)
	}
	if n := e.indexRows(); n != 2 {
		t.Fatalf("the second run's document is indexed too: %d rows", n)
	}
}

func TestScanIsReadAgainByTheSelfHostedVisionModelOnly(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInvoiceInstall()
	ctx := context.Background()
	// not self-hosted: no page image leaves
	r := e.runner("r1")
	r.Model.VisionModel = "outside-vision"
	run := e.invoiceRun("s01_scan300_full_tax_invoice.pdf")
	e.drive(r, run.ID, 5*time.Minute, func(x am.Run) bool { return parked(x) || terminal(x) })
	if n := countSteps(e.steps(run.ID), "vision:"); n != 0 {
		t.Fatalf("a model outside the organisation never sees a document: %d vision steps", n)
	}
	before := e.calls.Load()
	// self-hosted: one vision step, the run records the model
	r2 := e.runner("r2")
	r2.Model.VisionModel = "fake-vision"
	r2.Model.SelfHosted["fake-vision"] = true
	run2 := e.invoiceRun("s01_scan300_full_tax_invoice.pdf")
	got := e.drive(r2, run2.ID, 5*time.Minute, func(x am.Run) bool { return parked(x) || terminal(x) })
	steps := e.steps(run2.ID)
	if n := countSteps(steps, "vision:"); n != 1 {
		t.Fatalf("one vision step for the scan: %d", n)
	}
	if e.calls.Load() == before {
		t.Fatal("the vision model was called")
	}
	if got.ModelUsed == nil || *got.ModelUsed != "fake-vision" || got.ModelSelfHosted == nil || !*got.ModelSelfHosted {
		t.Fatal("the run records which model saw the document and that it runs in the organisation")
	}
	for _, s := range steps {
		if s.Name == "vision:s01_scan300_full_tax_invoice.pdf" {
			var v visionReading
			_ = json.Unmarshal(s.Output, &v)
			if v.Pages != 1 || v.Fields["total"] != "12850.70" {
				t.Fatalf("vision reading: %+v", v)
			}
		}
	}
	_ = ctx
}
