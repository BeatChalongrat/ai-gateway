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
	if msg := am.CheckTemplateMapping(am.Thai, form.Slots, mapping, spec); msg != "" {
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

// An install that never said which files it wants (a one-click install: its
// set was copied from the manifest) is asked once — the same card as every
// other question, without being made to wait. The answer is the round's set
// and the install's setting, and the next round is not asked.
func TestAnInstallThatNeverSaidWhichFilesIsAskedOnceAndRemembered(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	ctx := context.Background()
	// the manifest's set, chosen by nobody
	if err := e.store.SetDefault(ctx, e.store.Pool, e.install.ID, am.Default{Key: "formats", Value: json.RawMessage(`["docx","pptx"]`), Source: "install"}); err != nil {
		t.Fatal(err)
	}
	r := e.runner("r1")
	files := []string{"sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx", "sample-data/งบประมาณ_2569.xlsx"}
	run := e.newRun("ปิดงบเดือนกันยายน", files...)
	got := e.drive(r, run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the round is not made to wait for the question: %s %s", got.Status, got.StatusReason)
	}
	cards, _ := e.store.RunCards(ctx, run.ID)
	if len(cards) != 1 || cards[0].Kind != "questions" || cards[0].Blocking || cards[0].Status != "open" {
		t.Fatalf("one open card that blocks nothing: %+v", cards)
	}
	var qs []am.Question
	_ = json.Unmarshal(cards[0].Questions, &qs)
	if len(qs) != 1 || qs[0].Code != am.OutputSetCode || qs[0].Severity != "cosmetic" || !qs[0].Rememberable || len(qs[0].Options) != 4 {
		t.Fatalf("the question: %+v", qs)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 2 {
		t.Fatalf("the standard set meanwhile (docx + pptx), got %d", len(arts))
	}

	// the person answers on the card: everything
	var all am.Option
	for _, o := range qs[0].Options {
		var fs []string
		_ = json.Unmarshal(o.Value, &fs)
		if len(fs) == 3 {
			all = o
		}
	}
	if all.Label == "" {
		t.Fatalf("no option offers all three files: %+v", qs[0].Options)
	}
	srv := e.api()
	code, body := post(t, srv, "/cards/"+cards[0].ID.String()+"/answer", map[string]any{"answers": map[string]any{
		qs[0].Key: map[string]any{"labels": []string{all.Label}}}})
	if code != 200 {
		t.Fatalf("answer: %d %v", code, body)
	}
	// it re-renders from the same figures (no compute step runs again) and the table joins the files
	var job am.Run
	jobs, _ := e.store.Pool.Query(ctx, `SELECT id FROM agent_market.agent_runs WHERE parent_run_id = $1 AND kind = 'render' ORDER BY created_at DESC LIMIT 1`, run.ID)
	for jobs.Next() {
		_ = jobs.Scan(&job.ID)
	}
	jobs.Close()
	if job.ID.String() == "00000000-0000-0000-0000-000000000000" {
		t.Fatal("answering after the files were delivered queued no re-render")
	}
	if j := e.drive(r, job.ID, 4*time.Minute, terminal); j.Status != am.RunSucceeded {
		t.Fatalf("re-render: %s %s", j.Status, j.StatusReason)
	}
	formats := map[string]bool{}
	arts, _ = e.store.RunArtifacts(ctx, run.ID)
	for _, a := range arts {
		formats[a.Format] = true
	}
	if !formats["docx"] || !formats["pptx"] || !formats["xlsx"] {
		t.Fatalf("the round's files after the answer: %v", formats)
	}
	if n := countSteps(e.steps(run.ID), "compute"); n != 1 {
		t.Fatalf("compute ran %d times", n)
	}
	// remembered: the install's setting says it now, as an answer
	defs, _ := e.store.ListDefaults(ctx, e.install.ID)
	var setting, remembered *am.Default
	for i := range defs {
		switch defs[i].Key {
		case "formats":
			setting = &defs[i]
		case "answers." + am.OutputSetCode:
			remembered = &defs[i]
		}
	}
	var set []string
	if setting != nil {
		_ = json.Unmarshal(setting.Value, &set)
	}
	if setting == nil || setting.Source != "answer" || len(set) != 3 || remembered == nil {
		t.Fatalf("the install's formats setting and the remembered answer: %+v %+v", setting, remembered)
	}

	// the next round is not asked, and makes that set from the start
	next, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: e.install.ID, TenantID: e.install.TenantID, UserID: e.install.UserID,
		Kind: am.KindRun, Message: "ปิดงบเดือนกันยายน", DefinitionVersion: e.install.DefinitionVersion, Checksum: e.install.Checksum,
		Formats: setting.Value}, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range files {
		e.addFile(next, f)
	}
	if got := e.drive(r, next.ID, 4*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("next round: %s %s", got.Status, got.StatusReason)
	}
	if cards, _ := e.store.RunCards(ctx, next.ID); len(cards) != 0 {
		t.Fatalf("the next round was asked again: %d cards", len(cards))
	}
	if arts, _ := e.store.RunArtifacts(ctx, next.ID); len(arts) != 3 {
		t.Fatalf("the next round makes the chosen set: %d files", len(arts))
	}
}

// The reading of a form runs beside its round. A mapping that became ready
// after the round's render step looked for it is filled when the round
// finishes — once.
func TestAFormMappedWhileTheRoundRenderedIsFilledWhenItFinishes(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInvoiceInstall()
	ctx := context.Background()
	r := e.runner("r1")
	run := e.invoiceRun()
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded', finished_at = now() WHERE id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}
	jobs := func() int {
		var n int
		_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs WHERE parent_run_id = $1 AND kind = 'render'
			AND params->>'form_only' = 'true'`, run.ID).Scan(&n)
		return n
	}
	// no form: nothing to do
	r.fillFormLeftBehind(ctx, run.ID)
	if jobs() != 0 {
		t.Fatal("a round without a form queued a fill")
	}
	f := e.templateFile("xlsx_named_ranges.xlsx")
	form := &am.RoundForm{FileID: f.ID, Name: f.Name, Kind: "xlsx", SHA256: f.SHA256, Status: am.FormNeedsMapping}
	_ = e.store.SetRoundForm(ctx, e.store.Pool, run.ID, form)
	r.fillFormLeftBehind(ctx, run.ID)
	if jobs() != 0 {
		t.Fatal("a form still waiting for its mapping was filled")
	}
	form.Status, form.Mapping = am.FormReady, map[string]string{"CompanyName": "company"}
	_ = e.store.SetRoundForm(ctx, e.store.Pool, run.ID, form)
	r.fillFormLeftBehind(ctx, run.ID)
	r.fillFormLeftBehind(ctx, run.ID)
	if n := jobs(); n != 1 {
		t.Fatalf("a mapped form left behind is filled once, got %d jobs", n)
	}
	form.Fills = 1
	form.Status = am.FormFilled
	_ = e.store.SetRoundForm(ctx, e.store.Pool, run.ID, form)
	r.fillFormLeftBehind(ctx, run.ID)
	if n := jobs(); n != 1 {
		t.Fatalf("a form already filled is not filled again, got %d jobs", n)
	}
}

// A card that asks without making the round wait can be answered while the
// files are being made. What the answer changes is made when the round
// finishes — once, and only what the round lacks.
func TestAnAnswerGivenWhileTheFilesWereMadeIsMadeWhenTheRoundFinishes(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	ctx := context.Background()
	r := e.runner("r1")
	run := e.newRun("ปิดงบเดือนกันยายน")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded', finished_at = now() WHERE id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}
	renders := func() (n int, formats, why string) {
		rows, _ := e.store.Pool.Query(ctx, `SELECT COALESCE(params->>'formats', ''), COALESCE(params->>'why', '') FROM agent_market.agent_runs
			WHERE parent_run_id = $1 AND kind = 'render'`, run.ID)
		defer rows.Close()
		for rows.Next() {
			_ = rows.Scan(&formats, &why)
			n++
		}
		return n, strings.Join(strings.Fields(formats), ""), why
	}
	read := map[string]am.Answer{am.OutputSetCode: {Value: json.RawMessage(`["docx","pptx"]`), Via: "default"}}
	made := []string{"docx", "pptx"}
	// nothing was said meanwhile
	_ = e.store.MergeAnswers(ctx, e.store.Pool, run.ID, read)
	r.answersLeftBehind(ctx, run.ID, read, made)
	if n, _, _ := renders(); n != 0 {
		t.Fatal("no answer arrived: nothing to make")
	}
	// the person chose all three while the two were being made
	_ = e.store.MergeAnswers(ctx, e.store.Pool, run.ID, map[string]am.Answer{
		am.OutputSetCode: {Value: json.RawMessage(`["docx","pptx","xlsx"]`), Via: "card", Code: am.OutputSetCode}})
	r.answersLeftBehind(ctx, run.ID, read, made)
	r.answersLeftBehind(ctx, run.ID, read, made)
	if n, formats, why := renders(); n != 1 || formats != `["xlsx"]` || why != "output_set" {
		t.Fatalf("the table alone, once: %d %s %s", n, formats, why)
	}
}
