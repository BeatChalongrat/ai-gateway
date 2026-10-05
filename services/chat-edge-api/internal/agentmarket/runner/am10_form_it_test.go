package runner

// AM10 E end to end (AGENT_MARKET_IT=1): a form dropped after a round is read
// by the runner, mapped by the person, filled for that round and re-read —
// and neither the install nor the next round carries it.

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

func TestAFormDroppedIntoARoundIsFilledForThatRoundOnly(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInvoiceInstall()
	conv := startConverter(t)
	ctx := context.Background()
	r := e.runner("r1")
	r.Converter = conv

	// 1 · a round with no form anywhere: the register alone
	run := e.invoiceRun("d01_clean_full_tax_invoice.pdf")
	if got := e.drive(r, run.ID, 5*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("run: %s %s", got.Status, got.StatusReason)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	for _, a := range arts {
		if a.Format == "template" {
			t.Fatalf("a form nobody gave was filled: %+v", a)
		}
	}

	// 2 · the person drops their workbook into that round (what the composer's message does)
	f := e.templateFile("xlsx_named_ranges.xlsx")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_files SET run_id = $2, status = 'form' WHERE id = $1`, f.ID, run.ID); err != nil {
		t.Fatal(err)
	}
	done, _ := e.store.RunByID(ctx, run.ID)
	form := &am.RoundForm{FileID: f.ID, Name: f.Name, Kind: "xlsx", SHA256: f.SHA256, Status: am.FormInspecting}
	if err := e.store.SyncFormItem(ctx, e.store.Pool, done, form); err != nil {
		t.Fatal(err)
	}
	if err := e.store.SetRoundForm(ctx, e.store.Pool, run.ID, form); err != nil {
		t.Fatal(err)
	}
	params, _ := json.Marshal(map[string]any{"file_id": f.ID, "template": true, "run_id": run.ID})
	job, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: e.install.ID, TenantID: e.install.TenantID,
		UserID: e.install.UserID, Kind: am.KindMappingPreview, Params: params, DefinitionVersion: e.install.DefinitionVersion,
		Checksum: e.install.Checksum}, "")
	if err != nil {
		t.Fatal(err)
	}
	if got := e.drive(r, job.ID, 3*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("reading the form: %s %s", got.Status, got.StatusReason)
	}
	form, err = e.store.RoundForm(ctx, e.store.Pool, run.ID)
	if err != nil || form == nil {
		t.Fatalf("the round lost its form: %v", err)
	}
	// the model proposed what it could; the table's columns are the person's to say → the mapping screen
	if form.Status != am.FormNeedsMapping || len(form.Slots) == 0 || form.Proposal["CompanyName"] != "company" || form.ProposedBy == "" {
		t.Fatalf("after reading: status %s, %d slots, proposal %v, by %q", form.Status, len(form.Slots), form.Proposal, form.ProposedBy)
	}
	if _, ok := form.Proposal["GrandTotal"]; ok {
		t.Fatal("a formula cell is never proposed")
	}
	// nothing was filled on a guess
	arts, _ = e.store.RunArtifacts(ctx, run.ID)
	for _, a := range arts {
		if a.Format == "template" {
			t.Fatal("the form was filled before the person confirmed its mapping")
		}
	}

	// 3 · the person confirms (plus the table's columns): saved on the round, and filled
	mapping := map[string]string{}
	for k, v := range form.Proposal {
		mapping[k] = v
	}
	mapping["LinesTable"], mapping["LinesTable.A"], mapping["LinesTable.B"], mapping["LinesTable.C"] =
		"lines", "lines.description", "lines.quantity", "lines.amount"
	var spec am.TemplateSpec
	_ = json.Unmarshal(r.Engine.Packages["sran.invoice-processing"].Manifest.Templates, &spec)
	if msg := am.CheckTemplateMapping(form.Slots, mapping, spec); msg != "" {
		t.Fatalf("mapping: %s", msg)
	}
	form.Mapping, form.Status = mapping, am.FormReady
	if err := e.store.SetRoundForm(ctx, e.store.Pool, run.ID, form); err != nil {
		t.Fatal(err)
	}
	fill, err := r.formFillJob(ctx, done)
	if err != nil {
		t.Fatal(err)
	}
	if got := e.drive(r, fill.ID, 5*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("filling the form: %s %s", got.Status, got.StatusReason)
	}
	arts, _ = e.store.RunArtifacts(ctx, run.ID)
	var tpl *am.Artifact
	registers := 0
	for i := range arts {
		switch arts[i].Format {
		case "template":
			tpl = &arts[i]
		case "xlsx":
			registers++
		}
	}
	if tpl == nil || !strings.HasSuffix(tpl.Name, "_template_v1.xlsx") {
		t.Fatalf("the filled form is delivered on the round: %+v", arts)
	}
	if registers != 1 {
		t.Fatalf("filling the form made the register again (%d registers)", registers)
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
	reread := false
	for _, c := range q.Checks {
		t.Logf("form %s ok=%v: %s", c.Name, c.OK, c.Detail)
		reread = reread || c.Name == "template.every_slot"
	}
	if !q.Passed || !reread {
		t.Fatalf("the filled form was opened again and every mapped slot holds its value: %+v", q)
	}
	form, _ = e.store.RoundForm(ctx, e.store.Pool, run.ID)
	if form.Status != am.FormFilled || form.ArtifactID == nil || *form.ArtifactID != tpl.ID || form.Fills != 1 {
		t.Fatalf("the round's form after the fill: %+v", form)
	}
	var itemStatus string
	_ = e.store.Pool.QueryRow(ctx, `SELECT payload->>'status' FROM agent_market.agent_thread_items WHERE id = $1`, *form.ItemID).Scan(&itemStatus)
	if itemStatus != am.FormFilled {
		t.Fatalf("the thread's form item says %q", itemStatus)
	}

	// 4 · for that round only: the install has no form, and the next round fills none
	defs, _ := e.store.ListDefaults(ctx, e.install.ID)
	if _, ok := am.SavedTemplate(defs); ok {
		t.Fatal("the round's form became the install's")
	}
	next := e.invoiceRun("d02_classic_hyphen_taxid.pdf")
	got := e.drive(r, next.ID, 5*time.Minute, terminal)
	if !terminalOrWaiting(got) {
		t.Fatalf("next round: %s %s", got.Status, got.StatusReason)
	}
	if nf, _ := e.store.RoundForm(ctx, e.store.Pool, next.ID); nf != nil {
		t.Fatalf("the next round carries the form: %+v", nf)
	}
	arts, _ = e.store.RunArtifacts(ctx, next.ID)
	for _, a := range arts {
		if a.Format == "template" {
			t.Fatalf("the next round filled the last round's form: %+v", a)
		}
	}
}

func terminalOrWaiting(r am.Run) bool {
	return am.IsTerminal(r.Status) || r.Status == am.RunAwaitingInput
}

// A form whose every slot has a proposal the check accepts needs no person;
// one open slot, one blocking slot or a refused pair does.
func TestOnlyAnObviousFormIsFilledWithoutAsking(t *testing.T) {
	spec := am.TemplateSpec{}
	_ = json.Unmarshal([]byte(`{"fields":{"company":{"label":"บริษัท"},"total_thb":{"label":"ยอดรวม"}}}`), &spec)
	slots := []am.TemplateSlot{{Key: "Company", Kind: "name", Label: "บริษัท", Fillable: true}, {Key: "Total", Kind: "name", Label: "ยอดรวม", Fillable: true},
		{Key: "Formula", Kind: "name", Label: "สูตร", Fillable: false}}
	all := map[string]string{"Company": "company", "Total": "total_thb"}
	if !formObvious(slots, all, spec) {
		t.Fatal("every fillable slot proposed and accepted: nothing to ask")
	}
	if formObvious(slots, map[string]string{"Company": "company"}, spec) {
		t.Fatal("an open slot is the person's to decide")
	}
	if formObvious(slots, map[string]string{"Company": "company", "Total": "salary"}, spec) {
		t.Fatal("a pair the mapping check refuses is not obvious")
	}
	blocked := append([]am.TemplateSlot{}, slots...)
	blocked[2].Blocking = true
	if formObvious(blocked, all, spec) {
		t.Fatal("a form with a blocking slot is not filled")
	}
	if formObvious(nil, nil, spec) {
		t.Fatal("a form with no slot at all is not obvious")
	}
}
