package agentmarket

import (
	"encoding/json"
	"strings"
	"testing"
)

// AM18 part B — what a person asks of the documents beyond the package's list: the language, from now on
// or this once, and a request that could mean two things. These are the refusal cases: what a model must
// not be able to do by answering wrong, and what the platform must say.

var bankAsks = InstructionSpec{
	Can: []Directive{
		{ID: "sort_by_amount", Label: "เรียงรายการกระทบยอดจากจำนวนเงินมากที่สุด", Group: "order"},
		{ID: "sort_by_date", Label: "เรียงรายการกระทบยอดตามวันที่", Group: "order"},
	},
	Cannot: []Directive{
		{ID: "drop_small", Label: "ตัดรายการยอดเล็กออก", Why: "รายการกระทบยอดทุกรายการต้องอยู่ครบ"},
	},
}

func TestAM18TheLanguageAskedForIsTheRoundsAndOnlyWhenThePackageSpeaksIt(t *testing.T) {
	both := bankAsks.Speaking([]string{"th", "en"})
	const typed = "ทำเป็นภาษาอังกฤษ เรียงตามวันที่"
	got := ReadAsked(both, typed, json.RawMessage(`{"apply": ["lang_en", "sort_by_date"], "cannot": []}`))
	if got.Lang != "en" || strings.Join(got.Apply, " ") != "sort_by_date" || len(got.Cannot) != 0 || got.Nothing() {
		t.Fatalf("the language is the round's, not a directive of the package's: %+v", got)
	}
	// a package whose documents are Thai alone: English cannot be done, and the reason says what it speaks
	thai := bankAsks.Speaking(nil)
	got = ReadAsked(thai, typed, json.RawMessage(`{"apply": ["lang_en", "sort_by_date"], "cannot": []}`))
	if got.Lang != "" || len(got.Cannot) != 1 || got.Cannot[0].ID != LangEnglish {
		t.Fatalf("a language the package does not speak is what cannot be done: %+v", got)
	}
	said := AskedNotDone(thai, nil, got.Cannot)
	if len(said) != 1 || !strings.Contains(said[0].In(Thai), "ภาษาไทยเท่านั้น") || !strings.Contains(said[0].In(English), "in Thai only") {
		t.Fatalf("the refusal names the languages it speaks: %v", said)
	}
	// any other language: refused with the reason, in both languages, and nothing is translated
	got = ReadAsked(both, "ทำเป็นภาษาญี่ปุ่น", json.RawMessage(`{"apply": [], "cannot": [{"id": "lang_other", "words": "ทำเป็นภาษาญี่ปุ่น"}]}`))
	if len(got.Cannot) != 1 || got.Cannot[0].ID != LangOther || got.Cannot[0].Words != "ทำเป็นภาษาญี่ปุ่น" || got.Lang != "" {
		t.Fatalf("another language: %+v", got)
	}
	said = AskedNotDone(both, nil, got.Cannot)
	if len(said) != 1 || !strings.Contains(said[0].In(English), "Thai and English only") || !strings.Contains(said[0].In(English), "translate") {
		t.Fatalf("the reason: %v", said)
	}
	// both languages at once: a round is written in one, so it is asked — never one picked for the person
	got = ReadAsked(both, "ขอทั้งไทยและอังกฤษ", json.RawMessage(`{"apply": ["lang_th", "lang_en"], "cannot": []}`))
	if got.Lang != "" || len(got.Ask) != 1 || strings.Join(got.Ask[0].IDs, " ") != "lang_th lang_en" {
		t.Fatalf("both at once is a question: %+v", got)
	}
	// the question lists the languages the package speaks, in the "can" list, and the others in "cannot"
	_, user := AskedPrompt(both, typed)
	for _, want := range []string{"- lang_th: ทำเอกสารเป็นภาษาไทย", "- lang_en: ทำเอกสารเป็นภาษาอังกฤษ", "- lang_other: ทำเอกสารเป็นภาษาอื่น", `"standing"`, `"ask"`} {
		if !strings.Contains(user, want) {
			t.Fatalf("the question lacks %q:\n%s", want, user)
		}
	}
	_, user = AskedPrompt(thai, typed)
	if i, j := strings.Index(user, "- lang_en:"), strings.Index(user, "ทำไม่ได้:"); i < 0 || j < 0 || i < j {
		t.Fatalf("a language the package does not speak is listed as what cannot be done:\n%s", user)
	}
	if strings.Contains(user[:strings.Index(user, "ทำไม่ได้:")], "lang_en") {
		t.Fatal("English offered by a package that speaks Thai alone")
	}
}

func TestAM18WhatIsAskedForFromNowOnIsOnlyWhatWasApplied(t *testing.T) {
	both := bankAsks.Speaking([]string{"th", "en"})
	const typed = "ต่อไปนี้เรียงตามวันที่ทุกครั้ง และรอบนี้ขอภาษาอังกฤษ"
	raw := json.RawMessage(`{"apply": ["sort_by_date", "lang_en"], "cannot": [], "standing": ["sort_by_date", "drop_small", "sort_by_amount", "no_such", "sort_by_date"]}`)
	got := ReadAsked(both, typed, raw)
	if strings.Join(got.Standing, " ") != "sort_by_date" || got.Lang != "en" {
		t.Fatalf("standing is of what was applied, each once — never of what cannot be done or was not asked: %+v", got)
	}
	// the language can be asked for from now on too
	got = ReadAsked(both, "ทำเป็นภาษาอังกฤษตั้งแต่นี้ไป", json.RawMessage(`{"apply": ["lang_en"], "standing": ["lang_en"]}`))
	if got.Lang != "en" || strings.Join(got.Standing, " ") != "lang_en" {
		t.Fatalf("a standing language: %+v", got)
	}
	// what the thread says, in both languages, and where it is cleared
	said := AskedStanding(both, []string{"sort_by_date", "lang_en"})
	for _, want := range []string{"เรียงรายการกระทบยอดตามวันที่", "ทำเอกสารเป็นภาษาอังกฤษ", "ล้าง"} {
		if !strings.Contains(said.In(Thai), want) {
			t.Fatalf("the Thai lacks %q: %s", want, said.In(Thai))
		}
	}
	if !strings.Contains(said.In(English), "from now on") || !strings.Contains(said.In(English), "Make the documents in English") || !strings.Contains(said.In(English), "settings page") {
		t.Fatalf("the English: %s", said.In(English))
	}
	// (the package's label is said in the reader's language where its English is known — the bank's is)
	if once := AskedOnce(both, []string{"sort_by_amount"}); !strings.HasSuffix(once.In(English), " is for this round only") ||
		!strings.HasPrefix(once.In(Thai), "ส่วน เรียงรายการกระทบยอดจากจำนวนเงินมากที่สุด") {
		t.Fatalf("this round only: %s / %s", once.In(Thai), once.In(English))
	}
	// the install's defaults hold it, and are read back
	ids, lang := StandingOf([]Default{StandingDefault(both, []string{"sort_by_date"}, nil), LanguageDefault(English, nil)})
	if strings.Join(ids, " ") != "sort_by_date" || lang != English {
		t.Fatalf("read back: %v %q", ids, lang)
	}
	ids, lang = StandingOf([]Default{{Key: LanguageKey, Value: json.RawMessage(`"fr"`)}})
	if ids != nil || lang != "" {
		t.Fatalf("a language that is neither is no default: %v %q", ids, lang)
	}
	// nothing asked for from now on: nothing kept, nothing said
	got = ReadAsked(both, "เรียงตามวันที่", json.RawMessage(`{"apply": ["sort_by_date"]}`))
	if len(got.Standing) != 0 || !AskedStanding(both, nil).Empty() {
		t.Fatalf("this once: %+v", got)
	}
}

func TestAM18ARequestThatCouldMeanTwoThingsIsAskedNeverGuessed(t *testing.T) {
	both := bankAsks.Speaking([]string{"th", "en"})
	const typed = "เรียงรายการใหม่ด้วย"
	raw := json.RawMessage(`{"apply": [], "cannot": [], "ask": [
		{"ids": ["sort_by_amount", "sort_by_date", "sort_by_amount", "drop_small", "no_such"], "words": "เรียงรายการใหม่"},
		{"ids": ["sort_by_amount"], "words": "x"},
		{"ids": ["sort_by_amount", "sort_by_date"], "words": "เรียงให้สวย"}]}`)
	got := ReadAsked(both, typed, raw)
	if len(got.Apply) != 0 || len(got.Ask) != 2 {
		t.Fatalf("nothing applied, two asks (one id is no question): %+v", got)
	}
	if strings.Join(got.Ask[0].IDs, " ") != "sort_by_amount sort_by_date" || got.Ask[0].Words != "เรียงรายการใหม่" {
		t.Fatalf("declared ids, each once, the person's own words: %+v", got.Ask[0])
	}
	if got.Ask[1].Words != "" {
		t.Fatalf("a quote the person did not type is dropped: %+v", got.Ask[1])
	}
	if got.Nothing() {
		t.Fatal("a question is something")
	}
	// an id already applied is not asked about again
	got = ReadAsked(both, typed, json.RawMessage(`{"apply": ["sort_by_date"], "ask": [{"ids": ["sort_by_amount", "sort_by_date"], "words": "เรียงรายการใหม่"}]}`))
	if len(got.Ask) != 0 {
		t.Fatalf("applied, so not asked: %+v", got)
	}
	// the card: one option per thing it could mean, in the run's language, blocking until answered, the
	// first the package lists as what a skipped card records
	f := AskedChoiceFinding(English, both, 0, AskedAmbiguous{IDs: []string{"sort_by_amount", "sort_by_date"}, Words: "เรียงรายการใหม่"})
	if f == nil || f.Key != AskedChoiceKey(0) || f.Severity != "blocking" || len(f.Options) != 2 || string(f.Default) != `"sort_by_amount"` {
		t.Fatalf("the card: %+v", f)
	}
	if err := f.Validate(); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(f.Question, "เรียงรายการใหม่") || !strings.Contains(f.Question, "which one") {
		t.Fatalf("the question quotes the person and asks in the run's language: %s", f.Question)
	}
	// (an option is the package's own label, in the run's language where its English is known)
	if f.Options[0].Label != LabelFromPackage(both.Can[0].Label, NoText).In(English) ||
		AskedChoiceFinding(Thai, both, 0, AskedAmbiguous{IDs: []string{"sort_by_amount", "sort_by_date"}}).Options[0].Label != "เรียงรายการกระทบยอดจากจำนวนเงินมากที่สุด" {
		t.Fatalf("an option is the package's own label: %+v", f.Options)
	}
	if AskedChoiceKey(1) != "asked_choice.1" {
		t.Fatal(AskedChoiceKey(1))
	}
	// the answer read back, by card
	answers := map[string]Answer{AskedChoiceKey(0): {Value: json.RawMessage(`"sort_by_date"`)}, AskedChoiceKey(2): {Value: json.RawMessage(`"lang_en"`)}}
	if got := AskedChoices(answers); strings.Join(got, " ") != "sort_by_date lang_en" {
		t.Fatalf("the choices: %v", got)
	}
	if LangOfID("lang_en") != English || LangOfID("sort_by_date") != "" || LangID(Thai) != LangThai {
		t.Fatal("the language of an id")
	}
	// fewer than two things it can do is no question
	if AskedChoiceFinding(Thai, both, 0, AskedAmbiguous{IDs: []string{"sort_by_amount", "no_such"}}) != nil {
		t.Fatal("one option is no choice")
	}
}

func TestAM18TheSentencesOfTheLanguageAreInBothLanguages(t *testing.T) {
	// (a package's label is said as the package wrote it where its English is not known — a sentence
	// that quotes one is checked by its frame)
	if s := AskedByStanding(bankAsks, []string{"sort_by_date"}); !strings.HasPrefix(s.In(English), "Made as asked earlier: ") ||
		!strings.HasPrefix(s.In(Thai), "ทำตามที่ขอไว้ก่อนหน้า: ") {
		t.Fatalf("made as asked earlier: %q / %q", s.In(Thai), s.In(English))
	}
	for _, s := range []PersonText{AskedLanguage(English), AskedLanguageAlready(Thai), AskedNewRoundInLanguage(English), LanguageName(Thai)} {
		if s.EmptyIn(Thai) || s.EmptyIn(English) || strings.ContainsAny(s.In(English), "กขคงจฉชซญดตถทธนบปผพฟภมยรลวศษสหอฮะาิีึืุูเแโใไ") {
			t.Fatalf("a sentence in one language only: %q / %q", s.In(Thai), s.In(English))
		}
	}
	if !strings.Contains(AskedNewRoundInLanguage(English).In(English), "keep their language") {
		t.Fatal(AskedNewRoundInLanguage(English).In(English))
	}
}
