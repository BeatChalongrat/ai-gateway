package runner

// AM2 Phase E end to end: a customer's workbook is inspected by the runner,
// its mapping saved on the install, and every run fills it — input cells
// only — and has the converter recalculate its formulas (AGENT_MARKET_IT=1).

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

func (e *itEnv) templateFile(name string) am.File {
	e.t.Helper()
	ctx := context.Background()
	data, err := os.ReadFile(filepath.Join(e.root, "backend", "agent_market", "fixtures", "templates", name))
	if err != nil {
		e.t.Fatal(err)
	}
	id := uuid.New()
	key := am.UploadKey(e.install.TenantID, e.install.ID, id, name)
	if err := e.files.Upload(ctx, key, bytes.NewReader(data), "", int64(len(data))); err != nil {
		e.t.Fatal(err)
	}
	f, err := e.store.CreateFile(ctx, e.store.Pool, am.File{ID: id, TenantID: e.install.TenantID, UserID: e.install.UserID,
		InstallID: e.install.ID, Name: name, Size: int64(len(data)), SHA256: fmt.Sprintf("%x", sha(data)), StorageKey: key, Status: "active"})
	if err != nil {
		e.t.Fatal(err)
	}
	return f
}

func TestCustomerWorkbookIsFilledAndRecalculated(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInvoiceInstall()
	conv := startConverter(t)
	ctx := context.Background()
	r := e.runner("r1")
	r.Converter = conv

	// 1 · the mapping screen's data: slots, the model's proposal (AM8: code matches no synonyms; the
	// pairs the check refuses — a formula cell, a slot that does not exist — are dropped), a first version
	f := e.templateFile("xlsx_named_ranges.xlsx")
	params, _ := json.Marshal(map[string]any{"file_id": f.ID, "template": true})
	job, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: e.install.ID, TenantID: e.install.TenantID,
		UserID: e.install.UserID, Kind: am.KindMappingPreview, Params: params, DefinitionVersion: e.install.DefinitionVersion,
		Checksum: e.install.Checksum}, "")
	if err != nil {
		t.Fatal(err)
	}
	if got := e.drive(r, job.ID, 3*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("inspect: %s %s", got.Status, got.StatusReason)
	}
	var out struct {
		Slots      []am.TemplateSlot `json:"slots"`
		Proposal   map[string]string `json:"proposal"`
		ProposedBy string            `json:"proposed_by"`
		Diff       struct {
			First bool `json:"first"`
		} `json:"diff"`
	}
	steps := e.steps(job.ID)
	_ = json.Unmarshal(steps[len(steps)-1].Output, &out)
	if out.Proposal["CompanyName"] != "company" || out.Proposal["Subtotal"] != "subtotal_thb" || !out.Diff.First || out.ProposedBy == "" {
		t.Fatalf("proposal: %+v", out)
	}
	if _, ok := out.Proposal["NoSuchSlot"]; ok {
		t.Fatal("a slot the form does not have is never proposed")
	}
	if _, ok := out.Proposal["GrandTotal"]; ok {
		t.Fatal("a formula cell is never proposed")
	}

	// 2 · the person confirms (plus the table's columns) and it is saved on the install
	mapping := out.Proposal
	mapping["LinesTable"], mapping["LinesTable.A"], mapping["LinesTable.B"], mapping["LinesTable.C"] =
		"lines", "lines.description", "lines.quantity", "lines.amount"
	var spec am.TemplateSpec
	_ = json.Unmarshal(r.Engine.Packages["sran.invoice-processing"].Manifest.Templates, &spec)
	if msg := am.CheckTemplateMapping(out.Slots, mapping, spec); msg != "" {
		t.Fatalf("mapping: %s", msg)
	}
	saved, _ := json.Marshal(am.TemplateDefault{FileID: f.ID, Name: f.Name, Kind: "xlsx", SHA256: f.SHA256, Version: 1,
		Slots: out.Slots, Mapping: mapping})
	if err := e.store.SetDefault(ctx, e.store.Pool, e.install.ID, am.Default{Key: "template", Value: saved, Source: "settings"}); err != nil {
		t.Fatal(err)
	}

	// 3 · a run fills it; the formula cell comes back computed
	run := e.invoiceRun("d01_clean_full_tax_invoice.pdf")
	if got := e.drive(r, run.ID, 5*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("run: %s %s", got.Status, got.StatusReason)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	var tpl *am.Artifact
	for i := range arts {
		if arts[i].Format == "template" {
			tpl = &arts[i]
		}
	}
	if tpl == nil || !strings.HasSuffix(tpl.Name, "_template_v1.xlsx") {
		t.Fatalf("the filled template is delivered next to the register: %+v", arts)
	}
	var q struct {
		Passed bool `json:"passed"`
		Checks []struct {
			Name   string `json:"name"`
			OK     bool   `json:"ok"`
			Detail string `json:"detail"`
		} `json:"checks"`
	}
	_ = json.Unmarshal(tpl.Validation, &q)
	recalc := false
	for _, c := range q.Checks {
		t.Logf("template %s ok=%v: %s", c.Name, c.OK, c.Detail)
		recalc = recalc || (c.Name == "template.recalculated" && strings.Contains(c.Detail, "LibreOffice"))
	}
	if !q.Passed || !recalc {
		t.Fatalf("the template's gate: %+v", q)
	}
	body, _, _, err := e.files.Download(ctx, tpl.StorageKey)
	if err != nil {
		t.Fatal(err)
	}
	data, _ := io.ReadAll(body)
	body.Close()
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		t.Fatal(err)
	}
	var sheet string
	for _, zf := range zr.File {
		if zf.Name == "xl/worksheets/sheet1.xml" {
			rc, _ := zf.Open()
			b, _ := io.ReadAll(rc)
			rc.Close()
			sheet = string(b)
		}
	}
	// B7 = B5 + B6 keeps its formula and now carries the computed total (12,850.70 for d01) — in OUR
	// filled file (openpyxl's plain <f>), not LibreOffice's re-export (<f aca="false">): only the
	// computed values come from LibreOffice, the customer's workbook is otherwise delivered as filled
	b7 := regexp.MustCompile(`<c r="B7"[^>]*>.*?</c>`).FindString(sheet)
	if !strings.Contains(b7, "<f>B5+B6</f>") || !strings.Contains(b7, "<v>12850.7</v>") {
		t.Fatalf("B7 after recalculation: %s", b7)
	}
}
