package agentmarket

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// AM23 — the kinds of file an agent makes.

func am23Manifest(t *testing.T, pkg string) Manifest {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", pkg, "agent.json"))
	if err != nil {
		t.Fatal(err)
	}
	m, err := ParseManifest(raw)
	if err != nil {
		t.Fatal(err)
	}
	AddPackageEnglish(raw) // (its labels' English, as the platform learns them reading the definition)
	return m
}

// §1.1: the settings page took any file set, and a bank reconciliation whose set named Word alone finished
// "done" with no file. The set names only what the agent makes, and a refusal says, by name, what it makes.
func TestTheFileSetNamesOnlyWhatTheAgentMakes(t *testing.T) {
	r := rulesOf(am23Manifest(t, "bank_reconciliation"))
	m := r.outputs
	var other []string
	for _, f := range []string{"docx", "pptx", "xlsx", "pdf", "xls"} {
		if !m.FormatAllowed(f) {
			other = append(other, f)
		}
	}
	if len(other) == 0 {
		t.Fatal("the test needs a kind of file the agent does not make")
	}
	bad, _ := json.Marshal(other[:1])
	if _, code, why := r.check("formats", bad); code != "BAD_SETTING" || why.Empty() {
		t.Fatalf("%s is refused: %q %q", bad, code, why.In(Thai))
	} else {
		th, en := why.In(Thai), why.In(English)
		if !strings.Contains(th, "ทำได้:") || !strings.Contains(en, "It makes:") || !strings.Contains(en, "Excel") {
			t.Fatalf("the refusal names what the agent makes, in both languages:\n%s\n%s", th, en)
		}
		if !strings.Contains(th, T(Thai, outputWordOr(other[0]))) {
			t.Fatalf("the refusal names the kind of file refused: %s", th)
		}
	}
	if kept, code, _ := r.check("formats", json.RawMessage(`["xlsx"]`)); code != "" || string(kept) != `["xlsx"]` {
		t.Fatalf("the workbook is kept: %s %s", kept, code)
	}
	for _, v := range []string{`[]`, `"xlsx"`, `["xlsx","xls"]`} {
		if _, code, _ := r.check("formats", json.RawMessage(v)); code == "" {
			t.Fatalf("%s is refused", v)
		}
	}
	if _, _, why := r.check("formats", json.RawMessage(`["xls"]`)); !strings.Contains(why.In(Thai), ".xls") {
		t.Fatalf("a kind of file the platform has no word for is named by its extension: %s", why.In(Thai))
	}
}

func outputWordOr(f string) wording {
	if w, ok := outputWords[f]; ok {
		return w
	}
	return wording("." + f)
}

// §3.2: the neutral line on a document whose words a model drafted is the organisation's to switch off — on by
// default, changed by an admin on the org settings, and every change in the audit log with its old and new value.
func TestTheNeutralLineIsTheOrganisationsSwitchOnByDefaultAndEveryChangeIsLogged(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	org := orgOf(t, e.mustAdmin(uiAdmin, 200, "GET", "/org", nil))
	if org["assistant_line"] != true {
		t.Fatalf("on by default: %v", org["assistant_line"])
	}
	_ = log.take()
	after := orgOf(t, e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{"assistant_line": false}))
	if after["assistant_line"] != false {
		t.Fatalf("switched off: %v", after["assistant_line"])
	}
	x, ok := find(log.take(), "agent_market.org.assistant_line")
	if !ok || x.Details["from"] != true || x.Details["to"] != false || x.Details["setting"] != "assistant_line" {
		t.Fatalf("the change is logged with its old and new value: %+v", x)
	}
	if lbl, _ := x.Details["label"].(string); !strings.Contains(lbl, "ผู้ช่วย AI") {
		t.Fatalf("label = %q", lbl)
	}
	if o, err := e.store.OrgSettingsFor(context.Background(), DefaultOrg); err != nil || o.AssistantLine {
		t.Fatalf("kept off: %v %v", o.AssistantLine, err)
	}
	// a change that is no change is not logged
	e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{"assistant_line": false})
	if _, ok := find(log.take(), "agent_market.org.assistant_line"); ok {
		t.Fatal("logged a value that did not change")
	}
}

// §3.2: migration 013 adds the switch, on for every organisation, and down takes it away. A control plane deployed
// before the runner has applied it (the runner migrates at its start) keeps reading the organisation, with the
// line on — what every document did before the switch existed — and so does a change made in a locked row.
func TestMigration013AddsTheSwitchAndTheOrganisationIsReadWithoutIt(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.mustDo(200, "GET", "/admin/org", nil) // the organisation exists
	col := func() int {
		return e.countRows(`SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market'
			AND table_name = 'org_settings' AND column_name = 'assistant_line'`)
	}
	if col() != 1 {
		t.Fatal("013 adds the column")
	}
	down, err := migrationFS.ReadFile("migrations/013_am23.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatal(err)
	}
	if col() != 0 || e.countRows(`SELECT count(*) FROM agent_market.schema_migrations WHERE version = '013_am23'`) != 0 {
		t.Fatal("down takes it away")
	}
	o, err := e.store.OrgSettingsFor(ctx, DefaultOrg)
	if err != nil || !o.AssistantLine {
		t.Fatalf("the organisation is read without the column, the line on: %v %v", o.AssistantLine, err)
	}
	tx, err := e.store.Pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if o, err := e.store.orgSettingsQ(ctx, tx, DefaultOrg, true); err != nil || !o.AssistantLine {
		t.Fatalf("a locked read too: %v %v", o.AssistantLine, err)
	}
	if _, err := tx.Exec(ctx, `UPDATE agent_market.org_settings SET name = name WHERE org_id = $1`, DefaultOrg); err != nil {
		t.Fatalf("the transaction a locked read ran in is still usable: %v", err)
	}
	_ = tx.Rollback(ctx)
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"retention_years": 6}) // every other setting still changes
	did, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(did) != 1 || did[0] != "013_am23" || col() != 1 {
		t.Fatalf("up again: %v %v", did, err)
	}
	if o, _ := e.store.OrgSettingsFor(ctx, DefaultOrg); !o.AssistantLine {
		t.Fatal("on for every organisation")
	}
}

// §1.4 and Part 4 fixture 7: a PDF is how a document prints. A PDF with no document to print cannot be declared —
// the manifest is refused — so it is impossible, not handled: every agent's PDF prints a document it makes, the
// four whose job is the workbook print the workbook, and the financial report its report.
func TestAPDFWithoutADocumentToPrintCannotBeDeclared(t *testing.T) {
	base := `{"schema_version":"agent.market.v1","id":"x","version":"1.0.0","name":"x","python_package":"x",
		"permissions":{"default":"read_only"},"outputs":%s}`
	for _, outs := range []string{
		`[{"format":"xlsx"},{"format":"pdf","of":"docx"}]`, // a report it does not make
		`[{"format":"pdf"}]`, // nothing at all: the report is assumed, and not made
		`[{"format":"xlsx"},{"format":"pdf","of":"pdf"}]`,
		`[{"format":"xlsx","of":"docx"}]`, // only a PDF prints another document
	} {
		if _, err := ParseManifest(json.RawMessage(strings.Replace(base, "%s", outs, 1))); err == nil {
			t.Fatalf("refused: %s", outs)
		}
	}
	if m, err := ParseManifest(json.RawMessage(strings.Replace(base, "%s", `[{"format":"xlsx"},{"format":"pdf","of":"xlsx"}]`, 1))); err != nil || m.PDFOf() != "xlsx" {
		t.Fatalf("a PDF of the workbook: %v %q", err, m.PDFOf())
	}
	want := map[string]string{"bank_reconciliation": "xlsx", "expense_audit": "xlsx", "invoice_processing": "xlsx", "po_matching": "xlsx",
		"financial_report": "docx"}
	for pkg, of := range want {
		m := am23Manifest(t, pkg)
		if !m.FormatAllowed("pdf") || m.PDFOf() != of || !m.FormatAllowed(of) {
			t.Fatalf("%s: its PDF prints %q (want %q)", pkg, m.PDFOf(), of)
		}
	}
}

// §1.3: the four agents whose job is the workbook make a report, a deck and a PDF of the workbook when asked; the
// workbook alone is still what a round nobody changed makes.
func TestTheFourWorkbookAgentsOfferEveryFormatAndDefaultToTheWorkbook(t *testing.T) {
	for _, pkg := range []string{"bank_reconciliation", "expense_audit", "invoice_processing", "po_matching"} {
		m := am23Manifest(t, pkg)
		if got := strings.Join(m.OutputFormats(), ","); got != "xlsx,docx,pptx,pdf" {
			t.Fatalf("%s makes %s", pkg, got)
		}
		if got := strings.Join(m.DefaultFormats(), ","); got != "xlsx" {
			t.Fatalf("%s defaults to %s — defaults do not change", pkg, got)
		}
		for _, o := range m.Outputs {
			if l := LabelFromPackage(o.Label, NoText); l.EmptyIn(Thai) || l.EmptyIn(English) || l.In(English) == l.In(Thai) {
				t.Fatalf("%s: %s has a label in each language: %q / %q", pkg, o.Format, l.In(Thai), l.In(English))
			}
		}
	}
}

// §1.3: offering more files does not make a round ask which files it makes. The four agents whose job is the
// workbook are never asked (a report, a deck, a PDF are asked for); the financial report, whose round makes two
// files by default, still is.
func TestAOneFileAgentIsNeverAskedWhichFilesItsRoundMakes(t *testing.T) {
	copied := []Default{{Key: "formats", Value: json.RawMessage(`["xlsx"]`), Source: "install"}}
	for _, pkg := range []string{"bank_reconciliation", "expense_audit", "invoice_processing", "po_matching"} {
		if f := OutputSetFinding(Thai, am23Manifest(t, pkg), copied, []string{"xlsx"}); f != nil {
			t.Fatalf("%s asks which files: %+v", pkg, f)
		}
	}
	fr := am23Manifest(t, "financial_report")
	if f := OutputSetFinding(Thai, fr, []Default{{Key: "formats", Value: json.RawMessage(`["docx","pptx"]`), Source: "install"}}, []string{"docx", "pptx"}); f == nil {
		t.Fatal("the financial report still asks")
	}
}

// §1.5: a refusal asked of a finished round is answered with one tap — picked, the smaller deck is made, with its
// threshold; the workbook alone makes nothing (the round has it). The round stays finished either way.
func TestAnAnswerToARefusalMakesWhatWasPickedAndTheWorkbookAloneNothing(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run := e.finishedRun(in, "2026-09", "")
	if err := e.store.SetFormats(ctx, e.store.Pool, run.ID, []string{"xlsx"}); err != nil {
		t.Fatal(err)
	}
	q := Question{Key: "output_format", Code: "file_refused", Severity: "cosmetic", Header: "ไฟล์ที่ขอ", Question: "37 รายการเกินสไลด์",
		Options: []Option{{Label: "ไฟล์ Excel", Value: json.RawMessage(`{"formats":["xlsx"]}`)},
			{Label: "สไลด์ 20 รายการ", Value: json.RawMessage(`{"formats":["pptx"],"deck_over":"200"}`)}},
		Default: json.RawMessage(`{"formats":["xlsx"]}`)}
	card := func() Card {
		qs, _ := json.Marshal([]Question{q})
		c, err := e.store.CreateCard(ctx, e.store.Pool, Card{RunID: run.ID, InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
			Moment: "run", Kind: "output_format", Blocking: false, Questions: qs})
		if err != nil {
			t.Fatal(err)
		}
		return c
	}
	jobs := func() []string {
		rows, _ := e.store.Pool.Query(ctx, `SELECT params::text FROM agent_market.agent_runs WHERE parent_run_id = $1 AND kind = 'render' ORDER BY created_at`, run.ID)
		defer rows.Close()
		var out []string
		for rows.Next() {
			var p string
			_ = rows.Scan(&p)
			out = append(out, p)
		}
		return out
	}
	c1 := card()
	if code, body := e.do("POST", "/cards/"+c1.ID.String()+"/answer", map[string]any{"answers": map[string]any{
		"output_format": map[string]any{"labels": []string{"ไฟล์ Excel"}}}}); code != 200 {
		t.Fatalf("answer: %d %v", code, body)
	}
	if got := jobs(); len(got) != 0 {
		t.Fatalf("the workbook alone makes nothing: %v", got)
	}
	c2 := card()
	if code, body := e.do("POST", "/cards/"+c2.ID.String()+"/answer", map[string]any{"answers": map[string]any{
		"output_format": map[string]any{"labels": []string{"สไลด์ 20 รายการ"}}}}); code != 200 {
		t.Fatalf("answer: %d %v", code, body)
	}
	got := jobs()
	if len(got) != 1 || !strings.Contains(got[0], `"deck_over": "200"`) || !strings.Contains(got[0], "pptx") {
		t.Fatalf("the smaller deck, with its threshold: %v", got)
	}
	if r, _ := e.store.RunByID(ctx, run.ID); r.Status != RunSucceeded {
		t.Fatalf("the round stays finished: %s", r.Status)
	}
}
