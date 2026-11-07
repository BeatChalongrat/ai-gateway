package runner

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM23 §1.1 — every file the engine was asked for comes back made, refused with its reason, or left out with why.
// An engine that leaves one out with no word is a render that failed: a bank reconciliation asked for Word alone
// once finished "done" with no file.
func TestAFileTheEngineWasAskedForComesBackOrIsAccountedFor(t *testing.T) {
	art := func(format, kind string) json.RawMessage {
		b, _ := json.Marshal(map[string]string{"format": format, "kind": kind, "name": "x." + format})
		return b
	}
	if got := unaccounted([]string{"xlsx", "docx"}, []json.RawMessage{art("xlsx", "table")}, nil, nil); strings.Join(got, ",") != "docx" {
		t.Fatalf("a docx asked for and neither made nor refused is unaccounted for: %v", got)
	}
	if got := unaccounted([]string{"xlsx", "docx"}, []json.RawMessage{art("xlsx", "table")}, []refusedFormat{{Format: "docx"}}, nil); len(got) != 0 {
		t.Fatalf("a refused file is accounted for: %v", got)
	}
	if got := unaccounted([]string{"docx"}, nil, nil, []notMadeFormat{{Format: "docx", Why: "nothing_chosen"}}); len(got) != 0 {
		t.Fatalf("a file left out with why is accounted for: %v", got)
	}
	if got := unaccounted([]string{"docx"}, nil, nil, []notMadeFormat{{Format: "docx"}}); len(got) != 1 {
		t.Fatalf("a file left out with no why is not: %v", got)
	}
	if got := unaccounted([]string{"xlsx"}, []json.RawMessage{art("template", "template")}, nil, nil); len(got) != 1 {
		t.Fatalf("a filled form is not the workbook: %v", got)
	}
}

// am23Manifest is a package's manifest as the runner reads it.
func am23Manifest(t *testing.T, pkg string) am.Manifest {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "..", "backend", "agent_market", "packages", pkg, "agent.json"))
	if err != nil {
		t.Fatal(err)
	}
	m, err := am.ParseManifest(raw)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

// §1.6 and Part 4 fixture 4: a .xls is never written. Asked for, the answer is one sentence in either language — the
// 65,536 rows, no frozen header row, no print area the working paper depends on — and the .xlsx made instead. And a
// person typing "xlsx" is not asking for a .xls.
func TestAnXLSIsNeverWrittenAndTheReasonIsOneSentence(t *testing.T) {
	said := xlsSaid()
	for _, l := range []am.Lang{am.Thai, am.English} {
		s := said.In(l)
		if !strings.Contains(s, "65,536") || !strings.Contains(s, ".xlsx") || strings.Count(s, ".") > 4 {
			t.Fatalf("%s: %q", l, s)
		}
	}
	for _, typed := range []string{"ขอไฟล์ xls", "send it as .xls please", "ขอเป็น excel 97"} {
		if !asksForXLS(typed) {
			t.Fatalf("asks for a .xls: %q", typed)
		}
	}
	for _, typed := range []string{"ขอไฟล์ xlsx", "the .xlsx is fine", "excel"} {
		if asksForXLS(typed) {
			t.Fatalf("does not ask for a .xls: %q", typed)
		}
	}
	for _, pkg := range []string{"bank_reconciliation", "expense_audit", "invoice_processing", "po_matching", "financial_report", "procurement"} {
		if m := am23Manifest(t, pkg); m.FormatAllowed("xls") || m.FormatAllowed("doc") {
			t.Fatalf("%s declares a kind of file it never writes", pkg)
		}
	}
}
