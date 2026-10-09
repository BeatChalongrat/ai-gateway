package agentmarket

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"testing"
)

// AM14 — a package's engine says what it says in the language of the run: its sentences are in its
// manifest, in Thai and in English (backend/agent_market/engine/words.py). This plane reads everything
// an engine hands to a thread before a person is sent it, and in English shows only words the product's
// own sentences use (reads.go) — so an English sentence of a package that this plane did not know would
// reach a person as "…". The rule is held on the real thing: every finding, summary, gate, quality check
// and assumption of the packages' own eval sets, each case run in English
// (am14_packages_table_test.go, written by scripts/probes/AM14/dump_english_texts.sh), goes through the
// thread's own door and comes out as it went in.

// englishPackages are the packages whose sentences are in their manifests (it grows with the card).
var englishPackages = []string{"bank_reconciliation", "invoice_processing", "expense_audit", "po_matching", "financial_report"}

func TestEverySentenceOfAnEnglishRunReadsAsWrittenForAPerson(t *testing.T) {
	// the server learns a package's English from its manifest when it starts and whenever it reads a
	// definition (english_package.go); here, from the manifests in the checkout
	for _, pkg := range englishPackages {
		manifest, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", pkg, "agent.json"))
		if err != nil {
			t.Fatal(err)
		}
		AddPackageEnglish(manifest)
	}
	var doc packageDoc
	if err := json.Unmarshal([]byte(englishPackageOutputs), &doc); err != nil {
		t.Fatal(err)
	}
	texts, refused, _ := readAsWritten(t, doc)
	t.Logf("%d sentences of the packages' eval sets run in English, %d refused (%d findings, %d summaries and gates, %d checks, %d assumptions)",
		texts, refused, len(doc.Findings), len(doc.Summaries), len(doc.Checks), len(doc.Assumptions))
	if texts < 300 {
		t.Fatalf("the packages' English sentences were not read: %d", texts)
	}
	// every case in the table is an English run, of a package named above, and its questions are English
	named := map[string]bool{}
	for _, p := range englishPackages {
		named[p] = true
	}
	seen := map[string]bool{}
	hasLatinWord := regexp.MustCompile(`[A-Za-z]{4,} [a-z]{2,}`)
	for _, f := range doc.Findings {
		if caseLang(f.Case) != English || !named[f.Package] {
			t.Fatalf("%s/%s: not an English run of a package of this table", f.Package, f.Case)
		}
		seen[f.Package] = true
		var fd Finding
		_ = json.Unmarshal(f.Finding, &fd)
		if !hasLatinWord.MatchString(fd.Question) {
			t.Fatalf("%s/%s: a question of an English run is not in English: %q", f.Package, f.Case, fd.Question)
		}
		// (an answer may carry the person's own data — "Use บริษัท …", "Full name: กระดาษ A4" — its own words are English)
		for _, o := range fd.Options {
			if !am14Word.MatchString(o.Label) {
				t.Errorf("%s/%s: an answer of an English run is not in English: %q", f.Package, f.Case, o.Label)
			}
		}
	}
	for _, c := range doc.Checks {
		label, _ := c.Check["label"].(string)
		if caseLang(c.Case) != English || anyThaiOutsideQuotes(label) {
			t.Errorf("%s/%s: a check's line of an English run is not in English: %q", c.Package, c.Case, label)
		}
	}
	for _, p := range englishPackages {
		if !seen[p] {
			t.Errorf("%s: no question of its eval set is in the table", p)
		}
	}
}

var (
	am14Quoted = regexp.MustCompile(`“[^”]*”`)
	am14Thai   = regexp.MustCompile(`[ก-๛]`)
	am14Word   = regexp.MustCompile(`^[A-Z]`) // (an English label begins with its own word)
)

// anyThaiOutsideQuotes: Thai in a text that is not a value quoted from a file (a sheet's name, a cell).
func anyThaiOutsideQuotes(s string) bool {
	return am14Thai.MatchString(am14Quoted.ReplaceAllString(s, ""))
}
