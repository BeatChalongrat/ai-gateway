package agentmarket

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// AM12 — the same rule for the sixth package, in both of its languages: everything the procurement
// package hands to a thread (am12_packages_table_test.go: its questions before a search, the choice,
// its summaries, every quality check of its files and of a form it filled, its assumptions — in Thai and
// in English) goes through the thread's own door and comes out as it went in.
func TestEverySentenceOfTheProcurementPackageReadsAsWrittenInBothLanguages(t *testing.T) {
	// the server learns a package's English from its manifest when it starts and whenever it reads a
	// definition (english_package.go); here, from the package's manifest in the checkout
	manifest, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", "procurement", "agent.json"))
	if err != nil {
		t.Fatal(err)
	}
	AddPackageEnglish(manifest)
	var doc packageDoc
	if err := json.Unmarshal([]byte(procurementOutputs), &doc); err != nil {
		t.Fatal(err)
	}
	texts, refused, _ := readAsWritten(t, doc)
	t.Logf("%d sentences of the procurement package, %d refused (%d findings, %d summaries, %d checks, %d assumptions)",
		texts, refused, len(doc.Findings), len(doc.Summaries), len(doc.Checks), len(doc.Assumptions))
	// both languages are in the table: a question in Thai and the same question in English
	thai, english := 0, 0
	hasLatinWord := regexp.MustCompile(`[A-Za-z]{4,} [a-z]{2,}`)
	for _, f := range doc.Findings {
		var fd Finding
		_ = json.Unmarshal(f.Finding, &fd)
		// (a question in English still quotes what the person typed, in Thai: the case says its language)
		if caseLang(f.Case) == English {
			english++
			if !hasLatinWord.MatchString(fd.Question) {
				t.Fatalf("%s: a question of an English run is not in English: %q", f.Case, fd.Question)
			}
		} else {
			thai++
		}
		if fd.Kind == "pick" && (nullIfEmpty(fd.Recommended) != nil || len(fd.Options) < 2) {
			t.Fatalf("%s: a choice among candidates recommends none of them: %+v", f.Case, fd)
		}
	}
	if thai < 4 || english < 4 || texts < 120 {
		t.Fatalf("the package's sentences were not read in both languages: %d Thai and %d English questions, %d sentences", thai, english, texts)
	}
}

// engineEnglishFiles are the engine's files that write English a person reads (a check's label and
// detail, in the language of the run).
var engineEnglishFiles = []string{"deliverables/workpaper.py"}

var (
	pyString    = regexp.MustCompile(`"((?:[^"\\\n]|\\.)*)"`)
	pyField     = regexp.MustCompile(`\{[^{}]*\}`)
	anyThai     = regexp.MustCompile(`[ก-๛]`)
	twoWordsMin = regexp.MustCompile(`[A-Za-z]{3,}[^A-Za-z]+[A-Za-z]{2,}`)
)

// engineEnglish reads, from the engine's source, every string it holds that is English prose: no Thai in
// it, and at least two words (a key, a number format, a column letter is not prose). Its {places} are
// taken out — what fills them is data.
func engineEnglish(t *testing.T) []string {
	t.Helper()
	var out []string
	for _, rel := range engineEnglishFiles {
		src, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "backend", "agent_market", filepath.FromSlash(rel)))
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range pyString.FindAllStringSubmatch(string(src), -1) {
			s := pyField.ReplaceAllString(m[1], " ")
			if anyThai.MatchString(s) || !twoWordsMin.MatchString(s) {
				continue
			}
			out = append(out, s)
		}
	}
	// AM14: and the sentences of the shared engine that are kept beside it, by key (engine/words.json: a
	// matching card, what a file read back showed) — every one of their English, whatever its length
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "engine", "words.json"))
	if err != nil {
		t.Fatal(err)
	}
	var shared struct {
		Strings map[string]string `json:"strings"`
		En      map[string]string `json:"en"`
	}
	if err := json.Unmarshal(raw, &shared); err != nil {
		t.Fatal(err)
	}
	if len(shared.Strings) < 60 {
		t.Fatalf("the shared engine's words were not read: %d", len(shared.Strings))
	}
	for key, thai := range shared.Strings {
		en := shared.En[thai]
		if en == "" {
			t.Fatalf("engine/words.json: %s has no English", key)
		}
		out = append(out, pyField.ReplaceAllString(en, " "))
	}
	return out
}

// The server does not link the Python engine, and it is the server that reads a file's checks back before
// a person is sent them. So the words of the engine's English sentences are told to this package
// (english_engine_words.go): every one of them, and none that no sentence uses.
func TestTheEnginesEnglishWordsAreKnownToTheControlPlane(t *testing.T) {
	want := map[string]bool{}
	sentences := engineEnglish(t)
	for _, s := range sentences {
		for _, w := range latinWords(s) {
			want[w] = true
		}
	}
	if len(sentences) < 10 {
		t.Fatalf("the engine's English was not read: %d sentences", len(sentences))
	}
	have := map[string]bool{}
	for _, w := range engineEnglishWords {
		if have[w] {
			t.Errorf("english_engine_words.go: %q is there twice", w)
		}
		have[w] = true
		if !want[w] {
			t.Errorf("english_engine_words.go: no sentence of the engine uses %q any more", w)
		}
	}
	var missing []string
	for w := range want {
		if !have[w] {
			missing = append(missing, w)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		t.Errorf("english_engine_words.go lacks %d words of the engine's English (AM12_WORDS=<file> writes the list): %s",
			len(missing), strings.Join(missing, " "))
	}
	if !sort.StringsAreSorted(engineEnglishWords) {
		t.Error("english_engine_words.go is not sorted")
	}
	if path := os.Getenv("AM12_WORDS"); path != "" {
		all := make([]string, 0, len(want))
		for w := range want {
			all = append(all, w)
		}
		sort.Strings(all)
		_ = os.WriteFile(path, []byte(strings.Join(all, "\n")+"\n"), 0o644)
	}
}
