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
