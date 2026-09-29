package agentmarket

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// AM6 — an agent's name reaches a person whole. A one-click install is named
// after the package's short name, and that name is what a notification, an
// e-mail draft's subject and the thread say: a character the person-text
// rule does not read (the live walkthrough's "ใบกำกับภาษี ↔ PO") is withheld,
// and the draft said "ใบกำกับภาษี …". Every package's name and short name
// must pass the rule as they are written.
func TestEveryPackagesNameReachesAPersonWhole(t *testing.T) {
	files, _ := filepath.Glob(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", "*", "agent.json"))
	if len(files) < 5 {
		t.Fatalf("the packages were not found: %v", files)
	}
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var m struct {
			Name      string `json:"name"`
			ShortName string `json:"short_name"`
		}
		if err := json.Unmarshal(raw, &m); err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		for _, name := range []string{m.Name, m.ShortName} {
			if name == "" {
				continue
			}
			if got := LabelFromPackage(name, Text("agent ของคุณ")).String(); got != name {
				t.Errorf("%s: %q reaches a person as %q", filepath.Base(filepath.Dir(f)), name, got)
			}
		}
	}
}
