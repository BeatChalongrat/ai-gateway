package agentmarket

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

// AM11 — Agent Market in two languages. The standing rule (GLOSSARY.md §6.1):
// a sentence exists in Thai and in English or it does not exist. These tests
// are that rule for the server: they read the source of both planes and fail
// on a Thai sentence with no English, on an English entry nothing uses, on a
// message code in one language only, and on an English sentence the server's
// own guard would withhold.

// ── the mechanism ────────────────────────────────────────────────────────

func newRequest(method, url, acceptLanguage string) (*http.Request, error) {
	r := httptest.NewRequest(method, url, nil)
	if acceptLanguage != "" {
		r.Header.Set("Accept-Language", acceptLanguage)
	}
	return r, nil
}

func TestATextIsBuiltInBothLanguagesAndWrittenInOne(t *testing.T) {
	AddEnglish(map[string]string{
		"ทดสอบ AM11: ลงชื่อเมื่อ %s โดย %s": "AM11 test: signed off on %s by %s",
		"ทดสอบ AM11: %s ไฟล์ของงวด %s":      "AM11 test: %[2]s, %[1]s files",
		" ทดสอบ AM11 และ ":                  " AM11 test and ",
	})
	at := time.Date(2026, 9, 28, 3, 5, 0, 0, time.UTC) // 10:05 in Bangkok
	signed := Text("ทดสอบ AM11: ลงชื่อเมื่อ %s โดย %s", When(at), Name("สมชาย"))
	if got := signed.String(); got != "ทดสอบ AM11: ลงชื่อเมื่อ 28 ก.ย. 2569 เวลา 10:05 น. โดย สมชาย" {
		t.Fatalf("Thai: %s", got)
	}
	if got := signed.In(English); got != "AM11 test: signed off on 28 Sep 2026, 10:05 by สมชาย" {
		t.Fatalf("English: %s", got)
	}
	// English may put its facts in another order
	files := Text("ทดสอบ AM11: %s ไฟล์ของงวด %s", Count(3), Period("2026-09"))
	if th, en := files.In(Thai), files.In(English); th != "ทดสอบ AM11: 3 ไฟล์ของงวด กันยายน 2569" || en != "AM11 test: September 2026, 3 files" {
		t.Fatalf("%q / %q", th, en)
	}
	// a text inside a text keeps both
	both := Text("%s — %s", Words(signed), Words(files))
	if !strings.Contains(both.In(English), "signed off") || !strings.Contains(both.In(English), "September 2026") ||
		strings.Contains(both.In(English), "ลงชื่อ") {
		t.Fatalf("nested, English: %s", both.In(English))
	}
	joined := Join(" ทดสอบ AM11 และ ", signed, files)
	if !strings.Contains(joined.In(English), " AM11 test and ") || !strings.Contains(joined.In(Thai), " ทดสอบ AM11 และ ") {
		t.Fatalf("joined: %q / %q", joined.In(Thai), joined.In(English))
	}
	// what a package or a model wrote is in one language, and is that in both
	pkg := FromPackage("ยอดรวมไม่ตรงกับรายการ", NoText)
	if pkg.In(English) != pkg.In(Thai) {
		t.Fatalf("a package's sentence changed with the language: %q / %q", pkg.In(Thai), pkg.In(English))
	}

	// written in one: a plain json.Marshal is Thai, as every writer before AM11 expects
	type item struct {
		Text PersonText   `json:"text"`
		More []PersonText `json:"more"`
	}
	v := item{Text: signed, More: []PersonText{files}}
	plain, _ := json.Marshal(v)
	en, _ := MarshalIn(English, v)
	th, _ := MarshalIn(Thai, v)
	if string(plain) != string(th) || strings.Contains(string(en), "ลงชื่อ") || !strings.Contains(string(en), "signed off") ||
		!strings.Contains(string(en), "September 2026") {
		t.Fatalf("plain %s\nth %s\nen %s", plain, th, en)
	}
	// what was written is read back as it was written, whatever is asked of it later
	var back item
	if err := json.Unmarshal(en, &back); err != nil {
		t.Fatal(err)
	}
	again, _ := MarshalIn(Thai, back)
	if string(again) != string(en) {
		t.Fatalf("a stored English text changed on the way back out: %s", again)
	}

	// two writers at once, each in its own language
	var wg sync.WaitGroup
	for i := 0; i < 200; i++ {
		l := Thai
		if i%2 == 1 {
			l = English
		}
		wg.Add(1)
		go func(l Lang) {
			defer wg.Done()
			b, _ := MarshalIn(l, v)
			if (l == English) != strings.Contains(string(b), "signed off") {
				t.Errorf("a writer in %s got %s", l, b)
			}
		}(l)
	}
	wg.Wait()
}

func TestWhichLanguageARequestNames(t *testing.T) {
	for header, want := range map[string]Lang{
		"": Thai, "th": Thai, "en": English, "en-GB": English, "EN": English,
		// a browser's own list names no screen
		"en-US,en;q=0.9,th;q=0.8": Thai, "th-TH,th;q=0.9,en;q=0.8": Thai, "fr": Thai,
	} {
		r, _ := newRequest("GET", "/x", header)
		if got := RequestLang(r); got != want {
			t.Errorf("Accept-Language %q: %s, want %s", header, got, want)
		}
	}
	// a link and an event stream cannot set a header
	r, _ := newRequest("GET", "/x?lang=en", "th")
	if RequestLang(r) != English {
		t.Error("?lang=en was not read")
	}
}

func TestAnItemIsReadByTheRuleOfTheLanguageItWasWrittenIn(t *testing.T) {
	AddEnglish(map[string]string{"ทดสอบ AM11: งานของรอบนี้เสร็จแล้ว ไฟล์อยู่ด้านล่าง": "This run is finished. The files are below."})
	sentence := "This run is finished. The files are below."
	// written in English, and marked so: read as English
	enItem, _ := json.Marshal(map[string]any{"lang": "en", "text": sentence, "tone": "info"})
	var got struct {
		Lang string `json:"lang"`
		Text string `json:"text"`
	}
	_ = json.Unmarshal(ForClient("notice", enItem), &got)
	if got.Text != sentence || got.Lang != "en" {
		t.Fatalf("an English item's own sentence: %+v", got)
	}
	// the same words in an item written in Thai are not on its list — exactly as before AM11
	thItem, _ := json.Marshal(map[string]any{"text": sentence, "tone": "info"})
	got.Text, got.Lang = "", ""
	_ = json.Unmarshal(ForClient("notice", thItem), &got)
	if got.Text != "" || got.Lang != "" {
		t.Fatalf("an English sentence in a Thai item reached the person: %+v", got)
	}
	// an error's own words reach nobody, in either language: the whole text when it is one; the words
	// that are its own when it is said around words of ours (withheld, as in Thai — reads.go)
	for bad, own := range map[string]string{
		"KeyError: 'amount'": "KeyError", "open /tmp/x.xlsx: no such file or directory": "tmp",
		"the files are undefined": "undefined", "context deadline exceeded": "deadline",
		"runtime error: index out of range [3] with length 2": "range",
	} {
		for _, lang := range []string{"en", ""} {
			item, _ := json.Marshal(map[string]any{"lang": lang, "text": bad, "tone": "info"})
			got.Text = ""
			_ = json.Unmarshal(ForClient("notice", item), &got)
			if strings.Contains(got.Text, own) || LooksLikeError(got.Text) {
				t.Errorf("lang %q: %q reached the person as %q", lang, bad, got.Text)
			}
		}
	}
	// a word nobody's sentence uses is still not readable in English
	item, _ := json.Marshal(map[string]any{"lang": "en", "text": "The files are below. Zxqvortex frobnicated.", "tone": "info"})
	got.Text = ""
	_ = json.Unmarshal(ForClient("notice", item), &got)
	if strings.Contains(got.Text, "Zxqvortex") || strings.Contains(got.Text, "frobnicated") {
		t.Fatalf("an unknown word was shown: %q", got.Text)
	}
	// the customer's own Thai inside an English sentence is theirs, and stays
	item, _ = json.Marshal(map[string]any{"lang": "en", "text": "This run is finished. “บริษัท ตัวอย่าง จำกัด”", "tone": "info"})
	got.Text = ""
	_ = json.Unmarshal(ForClient("notice", item), &got)
	if !strings.Contains(got.Text, "บริษัท ตัวอย่าง จำกัด") {
		t.Fatalf("the customer's Thai was withheld from an English item: %q", got.Text)
	}
}

func TestWhatIsSaidInPlaceOfATextThatCannotBeShownIsInTheItemsLanguage(t *testing.T) {
	for lang, wantThai := range map[string]bool{"": true, "en": false} {
		item, _ := json.Marshal(map[string]any{"lang": lang, "tone": "error", "run_id": "",
			"text": "Traceback (most recent call last)", "failure": map[string]any{"reason": "render_failed", "what": "x.py line 3"}})
		var got struct {
			Text string `json:"text"`
		}
		_ = json.Unmarshal(ForClient("agent_text", item), &got)
		hasThai := strings.ContainsFunc(got.Text, isThai)
		if got.Text == "" || hasThai != wantThai {
			t.Errorf("lang %q: the stand-in sentence is %q", lang, got.Text)
		}
	}
}

// ── the catalogue against the source ─────────────────────────────────────

// Thai that is not a sentence for a person, by the declaration it sits in (file:name), with why.
// A new entry here is a decision: the default is that Thai in the source is a sentence and needs English.
var notASentence = map[string]string{
	// ── words that read what a person typed (each list holds its English words too: words.go) ──
	"api.go:newRoundWords":                  "reads a person's typed message: the words that ask for a new run",
	"rounds.go:aboutRoundWords":             "reads a person's typed message: a question about the run that just finished, or a request for its files in another form",
	"rounds.go:socialWords":                 "reads a person's typed message: a short thanks or acknowledgement",
	"rounds.go:socialMarks":                 "the marks left of a message that was only thanks (the Thai repetition mark among them): not words",
	"rounds.go:freshStartWords":             "reads a person's typed message: someone about to delete a chat to start over",
	"roundform.go:formWords":                "reads a person's typed message: a request to fill a form",
	"clarify.go:negations":                  "reads a person's typed answer: the words that turn an option into its opposite",
	"expense_policy.go:policyCurrencyWords": "reads an amount an admin typed into the policy table: the currency's word and sign are taken off — the package's own two, and no more until the package takes more",
	"runner/jobs.go:formatWords":            "reads a person's typed message: the words that name a kind of file",
	"runner/jobs.go:pdfWords":               "reads a person's typed message: the words that ask for a PDF",
	"runner/plan_am23.go:xlsWords":          "reads a person's typed message: the words that ask for a .xls file (AM23)",
	"runner/jobs.go:questionWords":          "reads a person's typed message: the words that make it a question",
	"runner/jobs.go:moreFilesWords":         "reads a person's typed message: a request for more files without naming a kind",
	"runner/explain.go:whyWords":            "reads a person's typed question: the words that ask for a reason or an origin",
	// ── words that read a package's or a model's text ──
	"clarify.go:Validate":              "reads a package's question: what a card may never ask for (a secret), in either language",
	"runner/explain.go:periodSuffixes": "reads a package's label of a figure: the ending it carries for “this period”",
	"runner/explain.go:causalWords":    "reads a model's answer: the words that give a cause, which it may not",
	"runner/model.go:GuardNumbersIn":   "reads a model's answer (the letters of the Thai unit of millions, trimmed off a number), and the reason sent back to the model",
	"runner/model.go:numberForms":      "reads a model's answer: the units a quotable figure is printed with",
	"runner/model.go:million":          "reads a model's answer: the unit of millions, in the one form the guard compares",
	"runner/model.go:blankIDs":         "reads a model's answer: the words an account code follows",
	// ── addressed to a model (a prompt is Thai whatever the run's language; where it names the language
	// of the answer or of what the person typed, it names the run's: runner/modellang.go) ──
	"runner/modellang.go:languageName":      "addressed to a model: the run's language, as a prompt names it",
	"runner/modellang.go:answerIn":          "addressed to a model: which language to answer in (an English run)",
	"runner/modellang.go:directionWord":     "addressed to a model: the example of a direction said in words",
	"runner/jobs.go:figuresQuestionRules":   "addressed to a model: the system prompt of a question about a run's figures",
	"runner/jobs.go:knowledgeRules":         "addressed to a model: what it is told about passages of the knowledge base",
	"runner/jobs.go:knowledgeQuestionRules": "addressed to a model: the system prompt of a question answered from the knowledge base",
	"runner/jobs.go:questionHeading":        "addressed to a model: the heading of the person's question in the user message",
	"runner/jobs.go:noCauseReason":          "addressed to a model: the reason an answer that gives a cause is sent back with",
	"runner/jobs.go:askModel":               "addressed to a model: the reason an answer that is not the JSON asked for is sent back with",
	"runner/template.go:proposeMapping":     "addressed to a model: the reason an answer without a mapping is sent back with",
	"runner/template.go:mappingSystem":      "addressed to a model: the system prompt that asks which field of a form takes which value (its answer is keys only)",
	"runner/template.go:mappingPrompt":      "addressed to a model: the headings of the form's fields and the agent's values",
	"runner/documents.go:visionJSON":        "addressed to a vision model: the reasons an unusable answer is sent back with",
	"runner/deliver.go:checkNarrative":      "addressed to a model: the reasons an unusable commentary is sent back with",
	"runner/deliver.go:guardFigures":        "addressed to a model: the reasons a commentary that types a figure is sent back with",
	"runner/deliver.go:narrativePrompt":     "addressed to a model: the headings of the figures it is shown and what it is asked (the answer's language: modellang.go)",
	"runner/deliver.go:writingLimitsLine":   "addressed to a model: how much the layouts take and how to write to it — in the run's language, both languages written here (AM22 §1.3, §1.6)",
	"runner/modellang.go:retryLine":         "addressed to a model: what is added to the prompt on its one retry, in the run's language (AM22 §1.6)",
	"runner/modellang.go:inLang":            "addressed to a model: a reason in the language of its answer (AM22 §1.6) — its callers' Thai is in the register by their own names",
	"runner/modellang.go:where":             "addressed to a model: which part of its answer a reason is about, in the run's language (AM22)",
	"runner/modellang.go:tooLong":           "addressed to a model: the reason a part of the answer longer than the document takes is sent back with (AM22 §1.2)",
	"runner/modellang.go:tooMany":           "addressed to a model: the reason a heading with more points than the document takes is sent back with (AM22 §1.2)",
	"runner/modellang.go:notAList":          "addressed to a model: the reason a list sent as something else is sent back with (AM22 §1.2)",
	"runner/modellang.go:notASentence":      "addressed to a model: the reason a list member that is not a sentence is sent back with (AM22 §1.2)",
	"runner/modellang.go:wordNeverWritten":  "addressed to a model: the reason an answer with a word the agent's documents never contain is sent back with (AM22)",
	// AM23 §3.3: what makes a commentary read machine-made, held by the validator (runner/narrative_am23.go)
	"runner/narrative_am23.go:paddingFillerTH": "reads a model's answer: the Thai words a sentence restating a figure is made of besides its label, set aside when a point's new words are counted",
	"runner/narrative_am23.go:paddingReason":   "addressed to a model: the reason a point that only restates the facts is sent back with (AM23 §3.3)",
	"runner/narrative_am23.go:hedgeReason":     "addressed to a model: the reason an answer with a hedge of the package's is sent back with (AM23 §3.3)",
	"runner/narrative_am23.go:headingReason":   "addressed to a model: the reason a part under its generic heading or none is sent back with (AM23 §3.3)",
	"runner/narrative_am23.go:headingTooLong":  "addressed to a model: the reason a heading over the catalogue's title is sent back with (AM23 §3.3)",
	"runner/modellang.go:noFigureNames":        "addressed to a model: the reason an agent with no figure table has its {names} sent back with (AM22)",
	"runner/model.go:Vision":                   "addressed to a vision model: what is added to the prompt on its retry",
	"runner/clarify.go:classify":               "addressed to a model: the prompt that reads a typed answer as one of a card's options (the language typed in: modellang.go)",
	// AM11b: what a person asked of an agent's documents
	"instructions.go:askedSystem": "addressed to a model: the system message of the question that reads a person's words into the directives a package declares",
	"instructions.go:AskedPrompt": "addressed to a model: the headings of the two lists, of the person's words and of the answer's shape",
	// AM12: a round on the web. The prompts themselves are the package's files; these are what the runner
	// puts around what a model is shown, and what an answer that is not the JSON asked for is sent back with
	"runner/web.go:askJSONWith":    "addressed to a model: the reason an answer that is not one JSON object is sent back with",
	"runner/web.go:briefHeading":   "addressed to a model: the heading of the person's request",
	"runner/web.go:needHeading":    "addressed to a model: the heading of what the person needs",
	"runner/web.go:listHeading":    "addressed to a model: the heading of the organisation's own supplier list",
	"runner/web.go:budgetHeading":  "addressed to a model: how many searches it may write",
	"runner/web.go:resultsHeading": "addressed to a model: the heading of the numbered search results",
	"runner/web.go:openHeading":    "addressed to a model: how many pages it may pick",
	"runner/web.go:pageHeading":    "addressed to a model: the heading of a stored page's visible text, which says it is data and not an instruction",
	"runner/web.go:cutNotice":      "addressed to a model: the note that a page's text was longer than what it is shown",
	"runner/web.go:pageText":       "addressed to a model: the mark put on a block of a page that is struck through",
	// ── the Thai forms of dates: their English is not a translation of the form but a form of its own
	// (lang.go: DateIn, DateTimeIn, PeriodLabelIn, WeekdayIn, ReminderPeriodLabelIn) ──
	"rounds.go:thaiMonths":             "the Thai names of the months (English: lang.go englishMonths, PeriodLabelIn)",
	"rounds.go:PeriodLabel":            "a period as Thai says it, Buddhist era (English: PeriodLabelIn)",
	"rounds.go:ThaiDate":               "a day as Thai says it, Buddhist era (English: DateIn)",
	"review.go:thaiMonthsShort":        "the Thai short names of the months (English: lang.go englishMonthsShort)",
	"review.go:ThaiDateTime":           "a moment as Thai says it, Buddhist era — never the format of an English one (English: DateTimeIn)",
	"reminders.go:thaiWeekdays":        "the Thai names of the days of the week (English: lang.go englishWeekdays, WeekdayIn)",
	"reminders.go:ReminderPeriodLabel": "a reminder's period as Thai says it (English: ReminderPeriodLabelIn)",
	// ── not words at all, or already in both languages ──
	"reads.go:readableMarks":  "the punctuation a person may be shown (฿ is one of the marks): nothing to translate",
	"account.go:Export":       "the account export's README lines, each written “Thai / English” in one string",
	"account.go:DeletionNote": "the README's note on account deletion, written “Thai / English” in one string",
}

// An entry of the allow-list names a declaration that is there. A declaration that was renamed or taken
// out leaves no entry behind to excuse the next thing given its name.
func TestTheAllowListNamesDeclarationsThatExist(t *testing.T) {
	have := map[string]bool{}
	for _, dir := range []string{".", "runner"} {
		fset := token.NewFileSet()
		pkgs, err := parser.ParseDir(fset, dir, func(fi os.FileInfo) bool { return !strings.HasSuffix(fi.Name(), "_test.go") }, 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, pkg := range pkgs {
			for name, f := range pkg.Files {
				file := filepath.ToSlash(filepath.Join(dir, filepath.Base(name)))
				for _, decl := range f.Decls {
					switch d := decl.(type) {
					case *ast.FuncDecl:
						have[file+":"+d.Name.Name] = true
					case *ast.GenDecl:
						for _, sp := range d.Specs {
							switch s := sp.(type) {
							case *ast.ValueSpec:
								for _, n := range s.Names {
									have[file+":"+n.Name] = true
								}
							case *ast.TypeSpec:
								have[file+":"+s.Name.Name] = true
							}
						}
					}
				}
			}
		}
	}
	for key, why := range notASentence {
		if !have[key] {
			t.Errorf("notASentence names %s, which is not a declaration of the source", key)
		}
		if strings.TrimSpace(why) == "" {
			t.Errorf("notASentence[%s] says no reason", key)
		}
	}
	for key := range messageDecls {
		if !have[key] {
			t.Errorf("messageDecls names %s, which is not a declaration of the source", key)
		}
	}
}

type sourceLiteral struct {
	file  string
	line  int
	decl  string
	value string
}

var hasThai = regexp.MustCompile(`[\x{0E00}-\x{0E7F}]`)

// thaiInSource is every Thai string the two planes' source holds, as the program holds it: a literal,
// or literals and constants joined with + (folded, so the key is the whole sentence the catalogue is
// asked for). Comments are not strings. A regexp's pattern reads text; it is not said to anyone.
func thaiInSource(t *testing.T) []sourceLiteral {
	t.Helper()
	var out []sourceLiteral
	for _, dir := range []string{".", "runner"} {
		fset := token.NewFileSet()
		pkgs, err := parser.ParseDir(fset, dir, func(fi os.FileInfo) bool {
			return !strings.HasSuffix(fi.Name(), "_test.go") && !isCatalogueFile(fi.Name())
		}, 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, pkg := range pkgs {
			consts := map[string]string{}
			var fold func(e ast.Expr) (string, bool)
			fold = func(e ast.Expr) (string, bool) {
				switch x := e.(type) {
				case *ast.BasicLit:
					if x.Kind != token.STRING {
						return "", false
					}
					s, err := strconv.Unquote(x.Value)
					return s, err == nil
				case *ast.Ident:
					s, ok := consts[x.Name]
					return s, ok
				case *ast.ParenExpr:
					return fold(x.X)
				case *ast.BinaryExpr:
					if x.Op != token.ADD {
						return "", false
					}
					a, ok1 := fold(x.X)
					b, ok2 := fold(x.Y)
					return a + b, ok1 && ok2
				}
				return "", false
			}
			// string constants, package-level and local, to a fixed point (one may be built from another)
			var specs []*ast.ValueSpec
			for _, f := range pkg.Files {
				ast.Inspect(f, func(n ast.Node) bool {
					if gd, ok := n.(*ast.GenDecl); ok && gd.Tok == token.CONST {
						for _, sp := range gd.Specs {
							specs = append(specs, sp.(*ast.ValueSpec))
						}
					}
					return true
				})
			}
			for changed := true; changed; {
				changed = false
				for _, vs := range specs {
					for i, name := range vs.Names {
						if i >= len(vs.Values) {
							continue
						}
						if s, ok := fold(vs.Values[i]); ok {
							if old, had := consts[name.Name]; !had || old != s {
								consts[name.Name] = s
								changed = true
							}
						}
					}
				}
			}
			for name, f := range pkg.Files {
				file := filepath.ToSlash(filepath.Join(dir, filepath.Base(name)))
				for _, decl := range f.Decls {
					declName := declNameOf(decl)
					var visit func(n ast.Node) bool
					visit = func(n ast.Node) bool {
						switch x := n.(type) {
						case *ast.Field:
							return false // a struct tag is not text
						case *ast.CallExpr:
							if sel, ok := x.Fun.(*ast.SelectorExpr); ok {
								if id, ok := sel.X.(*ast.Ident); ok && id.Name == "regexp" {
									return false
								}
							}
						case *ast.ValueSpec:
							if len(x.Names) > 0 && declName == "" {
								old := declName
								declName = x.Names[0].Name
								for _, v := range x.Values {
									ast.Inspect(v, visit)
								}
								declName = old
								return false
							}
						case ast.Expr:
							if s, ok := fold(x); ok {
								if _, isIdent := x.(*ast.Ident); !isIdent && hasThai.MatchString(s) {
									out = append(out, sourceLiteral{file, fset.Position(x.Pos()).Line, declName, s})
								}
								return false // one sentence, not its pieces
							}
						}
						return true
					}
					ast.Inspect(decl, visit)
				}
			}
		}
	}
	return out
}

func declNameOf(d ast.Decl) string {
	switch x := d.(type) {
	case *ast.FuncDecl:
		return x.Name.Name
	case *ast.GenDecl:
		if len(x.Specs) == 1 {
			switch s := x.Specs[0].(type) {
			case *ast.ValueSpec:
				if len(s.Names) > 0 {
					return s.Names[0].Name
				}
			case *ast.TypeSpec:
				return s.Name.Name
			}
		}
	}
	return ""
}

func TestEverySentenceHasItsEnglish(t *testing.T) {
	english := bothPlanesEnglish(t)
	used := map[string]bool{}
	missing := map[string][]string{}
	n := 0
	for _, lit := range thaiInSource(t) {
		if _, skip := notASentence[lit.file+":"+lit.decl]; skip {
			continue
		}
		if messageDecls[lit.file+":"+lit.decl] {
			continue // a message map has its English in a map of its own, and its own test
		}
		n++
		if _, ok := english[lit.value]; ok {
			used[lit.value] = true
			continue
		}
		short := lit.value
		if r := []rune(short); len(r) > 70 {
			short = string(r[:70]) + "…"
		}
		missing[lit.file] = append(missing[lit.file], fmt.Sprintf("%d (%s): %s", lit.line, lit.decl, short))
	}
	if path := os.Getenv("AM11_DUMP"); path != "" {
		var todo []map[string]any
		for _, lit := range thaiInSource(t) {
			_, skip := notASentence[lit.file+":"+lit.decl]
			_, has := english[lit.value]
			if !skip && !has && !messageDecls[lit.file+":"+lit.decl] {
				todo = append(todo, map[string]any{"file": lit.file, "line": lit.line, "decl": lit.decl, "thai": lit.value})
			}
		}
		b, _ := json.MarshalIndent(todo, "", " ")
		_ = os.WriteFile(path, b, 0o644)
		noEnglish := map[string]string{}
		for code, th := range userMessages {
			if _, ok := userMessagesEn[code]; !ok {
				noEnglish[code] = th
			}
		}
		b, _ = json.MarshalIndent(noEnglish, "", " ")
		_ = os.WriteFile(strings.TrimSuffix(path, ".json")+".messages.json", b, 0o644)
	}
	if n < 300 {
		t.Fatalf("only %d Thai sentences were found in the source — the reader of the source is broken", n)
	}
	if len(missing) > 0 {
		files := make([]string, 0, len(missing))
		total := 0
		for f, ls := range missing {
			files = append(files, f)
			total += len(ls)
		}
		sort.Strings(files)
		var b strings.Builder
		for _, f := range files {
			fmt.Fprintf(&b, "\n%s — %d\n", f, len(missing[f]))
			for _, l := range missing[f] {
				b.WriteString("    " + l + "\n")
			}
		}
		t.Fatalf("%d of %d Thai sentences in the source have no English (english_*.go; or notASentence, with why):%s", total, n, b.String())
	}
	var stale []string
	for th := range english {
		if !used[th] && !strings.Contains(th, "ทดสอบ AM11") {
			stale = append(stale, th)
		}
	}
	sort.Strings(stale)
	if len(stale) > 0 {
		t.Fatalf("%d English entries translate no sentence of the source any more:\n  %s", len(stale), strings.Join(stale, "\n  "))
	}
}

// messageDecls: the declarations whose Thai has its English in a map of its own, code by code, and its
// own test (TestEveryMessageHasBothLanguages). They are named by where they are declared, never by what
// they say: "ไฟล์" is what a NOT_FOUND is about in whatThai and a sentence of its own in the runner, and
// the second needs its English like any other.
var messageDecls = map[string]bool{
	"messages.go:userMessages": true, "messages.go:whatThai": true,
	"reminders.go:reminderMailMessages": true,
}

// ── the catalogue's files ────────────────────────────────────────────────

// isCatalogueFile: the file holds the English of sentences (english_*.go, runner/english*.go), not sentences.
func isCatalogueFile(name string) bool {
	return name == "english.go" || (strings.HasPrefix(name, "english_") && strings.HasSuffix(name, ".go"))
}

type catalogueEntry struct {
	file   string
	line   int
	th, en string
}

// catalogueFiles is every entry of every catalogue file of both planes, as written, in the order written.
// The runner's file registers into this package's map from a package this one cannot import, so the test
// reads it — and the control plane's — from the source.
func catalogueFiles(t *testing.T) []catalogueEntry {
	t.Helper()
	var out []catalogueEntry
	for _, dir := range []string{".", "runner"} {
		names, err := filepath.Glob(filepath.Join(dir, "english*.go"))
		if err != nil {
			t.Fatal(err)
		}
		sort.Strings(names)
		for _, name := range names {
			if strings.HasSuffix(name, "_test.go") || strings.HasSuffix(name, "_words.go") ||
				(dir == "." && filepath.Base(name) == "english.go") {
				continue // the mechanism and the word list, not a catalogue
			}
			fset := token.NewFileSet()
			f, err := parser.ParseFile(fset, name, nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			ast.Inspect(f, func(n ast.Node) bool {
				call, ok := n.(*ast.CallExpr)
				if !ok || len(call.Args) != 1 {
					return true
				}
				fn := ""
				switch x := call.Fun.(type) {
				case *ast.Ident:
					fn = x.Name
				case *ast.SelectorExpr:
					fn = x.Sel.Name
				}
				lit, ok := call.Args[0].(*ast.CompositeLit)
				if fn != "AddEnglish" || !ok {
					return true
				}
				for _, el := range lit.Elts {
					kv, ok := el.(*ast.KeyValueExpr)
					if !ok {
						t.Fatalf("%s: an entry that is not “Thai”: “English”", name)
					}
					k, ok1 := kv.Key.(*ast.BasicLit)
					v, ok2 := kv.Value.(*ast.BasicLit)
					if !ok1 || !ok2 {
						t.Fatalf("%s:%d: an entry must be two string literals", name, fset.Position(kv.Pos()).Line)
					}
					th, err1 := strconv.Unquote(k.Value)
					en, err2 := strconv.Unquote(v.Value)
					if err1 != nil || err2 != nil {
						t.Fatalf("%s:%d: an entry that cannot be read", name, fset.Position(kv.Pos()).Line)
					}
					out = append(out, catalogueEntry{filepath.ToSlash(name), fset.Position(kv.Pos()).Line, th, en})
				}
				return false
			})
		}
	}
	return out
}

// bothPlanesEnglish is the catalogue as a process that links both planes has it: this package's map
// (which holds this plane's files) and the runner's file.
func bothPlanesEnglish(t *testing.T) map[string]string {
	t.Helper()
	out := map[string]string{}
	for th, en := range english {
		out[th] = en
	}
	for _, e := range catalogueFiles(t) {
		out[e.th] = e.en
	}
	return out
}

// An entry is written once, in one file, and a file is kept sorted — so that two files cannot say two
// things for one sentence, and a change reads as one line.
func TestACatalogueEntryIsWrittenOnceAndInOrder(t *testing.T) {
	where := map[string]string{}
	last := map[string]string{}
	n := map[string]int{}
	for _, e := range catalogueFiles(t) {
		if at, twice := where[e.th]; twice {
			t.Errorf("%s:%d: %q is already in %s", e.file, e.line, e.th, at)
		}
		where[e.th] = fmt.Sprintf("%s:%d", e.file, e.line)
		if prev, ok := last[e.file]; ok && prev >= e.th {
			t.Errorf("%s:%d: %q is out of order (it comes after %q)", e.file, e.line, e.th, prev)
		}
		last[e.file] = e.th
		n[e.file]++
		if got, ok := english[e.th]; strings.HasPrefix(e.file, "english_") && (!ok || got != e.en) {
			t.Errorf("%s:%d: %q is not what the program holds for it", e.file, e.line, e.th)
		}
	}
	for _, f := range []string{"english_a.go", "english_b.go", "runner/english.go"} {
		if n[f] < 50 {
			t.Errorf("%s: only %d entries were read — the reader of the catalogue is broken", f, n[f])
		}
	}
}

// The server does not link the runner's package, and it is the server that reads a thread item back
// before a person is sent it (schema.go). So the words of the runner's English sentences are told to
// this package (english_runner_words.go): every one of them, and none that no sentence uses.
func TestTheRunnersEnglishWordsAreKnownToTheControlPlane(t *testing.T) {
	want := map[string]bool{}
	for _, e := range catalogueFiles(t) {
		if strings.HasPrefix(e.file, "runner/") {
			for _, w := range latinWords(e.en) {
				want[w] = true
			}
		}
	}
	have := map[string]bool{}
	for _, w := range runnerEnglishWords {
		if have[w] {
			t.Errorf("english_runner_words.go: %q is there twice", w)
		}
		have[w] = true
		if !want[w] {
			t.Errorf("english_runner_words.go: no sentence of the runner uses %q any more", w)
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
		t.Errorf("english_runner_words.go lacks %d words of runner/english.go (AM11_WORDS=<file> writes the list): %s",
			len(missing), strings.Join(missing, " "))
	}
	if !sort.StringsAreSorted(runnerEnglishWords) {
		t.Error("english_runner_words.go is not sorted")
	}
	if path := os.Getenv("AM11_WORDS"); path != "" {
		all := make([]string, 0, len(want))
		for w := range want {
			all = append(all, w)
		}
		sort.Strings(all)
		_ = os.WriteFile(path, []byte(strings.Join(all, "\n")+"\n"), 0o644)
	}
}

var (
	placeholder = regexp.MustCompile(`\{[a-z_]+\}`)
	verb        = regexp.MustCompile(`%(\[\d+\])?[sdvq]`)
)

func sorted(xs []string) string {
	xs = append([]string(nil), xs...)
	sort.Strings(xs)
	return strings.Join(xs, " ")
}

func TestEveryMessageHasBothLanguages(t *testing.T) {
	for code, th := range userMessages {
		en, ok := userMessagesEn[code]
		if !ok {
			t.Errorf("%s has no English", code)
			continue
		}
		if a, b := sorted(placeholder.FindAllString(th, -1)), sorted(placeholder.FindAllString(en, -1)); a != b {
			t.Errorf("%s: placeholders differ — Thai {%s}, English {%s}", code, a, b)
		}
		if hasThai.MatchString(en) {
			t.Errorf("%s: its English has Thai in it: %s", code, en)
		}
	}
	for code := range userMessagesEn {
		if _, ok := userMessages[code]; !ok {
			t.Errorf("%s is in English only", code)
		}
	}
	for what := range whatThai {
		if _, ok := whatEnglish[what]; !ok {
			t.Errorf("what a NOT_FOUND is about: %q has no English", what)
		}
	}
	for what := range whatEnglish {
		if _, ok := whatThai[what]; !ok {
			t.Errorf("what a NOT_FOUND is about: %q is in English only", what)
		}
	}
	// the reminder and mail card's own messages (reminders.go) are a second map of the same kind
	for code, th := range reminderMailMessages {
		en, ok := reminderMailMessagesEn[code]
		if !ok {
			t.Errorf("mail message %s has no English", code)
			continue
		}
		if a, b := sorted(placeholder.FindAllString(th, -1)), sorted(placeholder.FindAllString(en, -1)); a != b {
			t.Errorf("mail message %s: placeholders differ — Thai {%s}, English {%s}", code, a, b)
		}
		if hasThai.MatchString(en) {
			t.Errorf("mail message %s: its English has Thai in it: %s", code, en)
		}
		if mailSentenceIn(Thai, code) != th || mailSentenceIn(English, code) != en || mailSentence(code) != th {
			t.Errorf("mail message %s is not said as it is written", code)
		}
	}
	for code := range reminderMailMessagesEn {
		if _, ok := reminderMailMessages[code]; !ok {
			t.Errorf("mail message %s is in English only", code)
		}
	}
	// one sentence said in two places is one sentence: the card's own line and the refusal
	if en, ok := english[mailNotSetUp]; !ok || en != reminderMailMessagesEn["NO_MAIL_CONNECTOR"] ||
		reminderMailMessages["NO_MAIL_CONNECTOR"] != mailNotSetUp {
		t.Errorf("NO_MAIL_CONNECTOR and mailNotSetUp say two things in English:\n %s\n %s", en, reminderMailMessagesEn["NO_MAIL_CONNECTOR"])
	}
}

func TestAnEnglishSentenceTakesTheSameFacts(t *testing.T) {
	for th, en := range bothPlanesEnglish(t) {
		a, b := verb.FindAllString(th, -1), verb.FindAllString(en, -1)
		indexed := strings.Contains(en, "%[")
		if len(a) != len(b) && !indexed {
			t.Errorf("%d facts in Thai, %d in English:\n  %s\n  %s", len(a), len(b), th, en)
		}
		if indexed {
			for _, v := range b {
				if !strings.Contains(v, "[") {
					t.Errorf("an English sentence that orders its facts must index every one:\n  %s", en)
					break
				}
			}
		}
	}
}

// The server's last look withholds whatever reads like an error's own text, and its reader shows only
// what is on its list. No English sentence of ours may fall to either.
func TestNoEnglishSentenceReadsLikeAnError(t *testing.T) {
	sentences := map[string]string{}
	for th, en := range bothPlanesEnglish(t) {
		sentences[th] = en
	}
	for code, en := range userMessagesEn {
		sentences[code] = en
	}
	for code, en := range reminderMailMessagesEn {
		sentences["mail message "+code] = en
	}
	for key, en := range sentences {
		filled := placeholder.ReplaceAllString(verb.ReplaceAllString(en, "1"), "1")
		if LooksLikeError(filled) {
			t.Errorf("reads like an error's text (GLOSSARY.md §6 rule 4): %s\n  %s", key, en)
			continue
		}
		if strings.TrimSpace(strings.Trim(filled, "1 ·—-:,.")) == "" {
			continue // only its facts and marks
		}
		// a sentence reads; an entry that is a bare value — a unit after its amount ("THB 1"), a format's
		// name — is shown as a value is (Reader.Value), which needs no word of a language beside it
		if r := readerFor(English); !r.Reads(filled) && !r.Value(filled) {
			t.Errorf("the reader would not show this English sentence: %s\n  %s\n  shown: %q", key, en, r.Shown(filled))
		}
	}
}

// A catalogue file registers its entries when the program starts (init). A package-level variable is
// made before that — so a sentence built in one's initialiser would be built with no English, and stay
// so. None is: a sentence is built when it is said (inside a function), or kept as its Thai wording
// and built there.
func TestNoSentenceIsBuiltBeforeTheCatalogueIsThere(t *testing.T) {
	builders := map[string]bool{"Text": true, "T": true, "Join": true, "InBoth": true, "Each": true, "EachTyped": true, "EachNamed": true,
		"FromPackage": true, "LabelFromPackage": true, "FromModel": true, "Said": true, "TextIn": true, "forReq": true}
	for _, dir := range []string{".", "runner"} {
		fset := token.NewFileSet()
		pkgs, err := parser.ParseDir(fset, dir, func(fi os.FileInfo) bool { return !strings.HasSuffix(fi.Name(), "_test.go") }, 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, pkg := range pkgs {
			for _, f := range pkg.Files {
				for _, decl := range f.Decls {
					gd, ok := decl.(*ast.GenDecl)
					if !ok || gd.Tok != token.VAR {
						continue
					}
					ast.Inspect(gd, func(n ast.Node) bool {
						switch x := n.(type) {
						case *ast.FuncLit:
							return false // said later, when the function is called
						case *ast.CallExpr:
							name := ""
							switch fn := x.Fun.(type) {
							case *ast.Ident:
								name = fn.Name
							case *ast.SelectorExpr:
								if id, ok := fn.X.(*ast.Ident); ok && id.Name == "am" {
									name = fn.Sel.Name
								}
							}
							if builders[name] {
								t.Errorf("%s: a package-level variable builds a sentence (%s) before the catalogue is registered", fset.Position(x.Pos()), name)
							}
						}
						return true
					})
				}
			}
		}
	}
}
