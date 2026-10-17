package agentmarket

import (
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
