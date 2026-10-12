package agentmarket

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// AM16 — a question that shows a model's reading says which model read the page.
//
// The engine's question said "the in-house model read … from the image" whoever had read it — also
// when an admin had let the agent's documents leave and a model outside read the page, while the run's
// own card said the page had been sent out. The engine now names the model outside
// (backend/agent_market/tests/test_am16_reader.py). The server does not link the Python engine, and it
// is the server that reads a card's sentences before a person is sent them: a model's name is Latin
// words nobody taught it, and a sentence it cannot read reaches a person as "…". So the real cards —
// the tax-invoice agent's one-scan case, the page read by google/gemini-2.5-flash, in Thai and in
// English (am16_reader_table_test.go, written by scripts/probes/AM16/dump_reader_cards.sh) — go through
// the thread's own door here and must come out as they went in.
func TestAQuestionThatNamesAModelOutsideReadsAsWritten(t *testing.T) {
	manifest, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", "invoice_processing", "agent.json"))
	if err != nil {
		t.Fatal(err)
	}
	AddPackageEnglish(manifest)
	var doc packageDoc
	if err := json.Unmarshal([]byte(readerOutsideCards), &doc); err != nil {
		t.Fatal(err)
	}
	texts, refused, _ := readAsWritten(t, doc)
	if texts == 0 || refused != 0 {
		t.Fatalf("%d sentences read, %d refused", texts, refused)
	}
	named := map[Lang]bool{}
	for _, f := range doc.Findings {
		var fd Finding
		if err := json.Unmarshal(f.Finding, &fd); err != nil {
			t.Fatal(err)
		}
		said := fd.Question
		for _, o := range fd.Options {
			said += "\n" + o.Label + "\n" + o.Description
		}
		// the refusal: a page read outside is never said to have been read in the organisation
		for _, wrong := range []string{"โมเดลในองค์กร", "in-house", "inside the organisation"} {
			if strings.Contains(said, wrong) {
				t.Errorf("%s: a card of a page read outside says %q:\n%s", f.Case, wrong, said)
			}
		}
		if strings.Contains(said, "“google/gemini-2.5-flash”") {
			named[caseLang(f.Case)] = true
		}
	}
	if !named[Thai] || !named[English] {
		t.Fatalf("the model that read the page is not named in both languages: %v", named)
	}
}
