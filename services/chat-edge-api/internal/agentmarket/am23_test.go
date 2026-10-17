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
