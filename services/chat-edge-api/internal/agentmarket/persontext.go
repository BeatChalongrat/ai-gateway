package agentmarket

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// ── Text built for a person (AM6) ────────────────────────────────────────
//
// Whatever writes to a thread takes a PersonText, not a string. A PersonText
// is made in this file and nowhere else:
//
//   - Text: a sentence written in the source (a constant — the type of its
//     first argument cannot be named outside this package, so only a literal
//     fits) with facts put into it;
//   - Fact: a typed value — a count, an amount of credits, a file's name, a
//     person's name, a model, a time. What a person typed, and any name that
//     is not plainly readable, goes into quotation marks;
//   - FromPackage, FromModel: a sentence a package's check or a model wrote,
//     which is only what ShownToPerson shows of it (reads.go), or the
//     platform's own sentence when that is nothing.
//
// An error's text is none of these: handing err.Error() to a thread writer
// does not compile, and handing it to FromPackage shows nothing of it.
//
// AM11: a text has two languages. A sentence written in the source is Thai
// and its English is in the catalogue (english.go); a fact that reads
// differently in English (a date, a period) carries both. Which one a person
// is sent is decided where the text is written or sent (lang.go: MarshalIn),
// not where it is built. A sentence a package or a model wrote is in the one
// language its run is in, and is the same in both.

// PersonText is text a person may read.
type PersonText struct {
	s string // Thai
	e string // English; "" = the same as s
}

// wording is a sentence written in the source. Code outside this package
// cannot name the type, so it can only pass a literal.
type wording string

// String is the text in Thai — what every caller before AM11 means by it.
func (t PersonText) String() string { return t.s }

// In is the text in l.
func (t PersonText) In(l Lang) string {
	if l == English && t.e != "" {
		return t.e
	}
	return t.s
}

// Empty: there is nothing to read — in Thai, which is what every caller
// before AM11 means by it. A text of a package's that only the English rule
// shows is empty by this and not by EmptyIn(English).
func (t PersonText) Empty() bool { return strings.TrimSpace(t.s) == "" }

// EmptyIn: there is nothing to read in l.
func (t PersonText) EmptyIn(l Lang) bool { return strings.TrimSpace(t.In(l)) == "" }

// Only is the text as it is in l and in nothing else: for a row that keeps
// one language (a draft, a subject line), read back and sent as it was kept.
func (t PersonText) Only(l Lang) PersonText { return PersonText{s: t.In(l)} }

// MarshalJSON writes the text as a JSON string, in the language of the
// MarshalIn it is part of (Thai for a plain json.Marshal).
func (t PersonText) MarshalJSON() ([]byte, error) { return json.Marshal(t.In(marshalLang())) }

// UnmarshalJSON reads a text back from a row or a payload. What comes back is
// only what a person is shown of it: reading JSON is no way around the rule.
func (t *PersonText) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err != nil {
		t.s = ""
		return nil // a payload with something else in a text's place has no text
	}
	*t = PersonText{s: labelRead(plainReader, s)}
	return nil
}

// Fact is a typed value put into a sentence. Most facts read the same in
// both languages; one that does not (a date) carries its English too.
type Fact struct {
	s string
	e string // English; "" = the same as s
}

func (f Fact) in(l Lang) string {
	if l == English && f.e != "" {
		return f.e
	}
	return f.s
}

// Text is a sentence written in the source, with its facts (each %s takes one).
// The sentence is Thai; its English comes from the catalogue (english.go).
func Text(sentence wording, facts ...Fact) PersonText {
	th := render(string(sentence), Thai, facts)
	e, ok := english[string(sentence)]
	if !ok {
		// no English of its own: a sentence made only of its facts ("%s — %s") still has theirs
		e = string(sentence)
	}
	en := render(e, English, facts)
	if en == th {
		en = ""
	}
	return PersonText{s: th, e: en}
}

func render(sentence string, l Lang, facts []Fact) string {
	if len(facts) == 0 {
		return sentence
	}
	args := make([]any, len(facts))
	for i, f := range facts {
		args[i] = f.in(l)
	}
	return fmt.Sprintf(sentence, args...)
}

// Join puts texts one after another. A text with nothing to read in a
// language is left out of that language's line (in Thai, exactly as before).
func Join(sep wording, texts ...PersonText) PersonText {
	var th, en []string
	for _, t := range texts {
		if !t.Empty() {
			th = append(th, t.s)
		}
		if !t.EmptyIn(English) {
			en = append(en, t.In(English))
		}
	}
	out := PersonText{s: strings.Join(th, string(sep)), e: strings.Join(en, T(English, sep))}
	if out.e == out.s {
		out.e = ""
	}
	return out
}

// Words is a text as a fact of another sentence.
func Words(t PersonText) Fact { return Fact{s: t.s, e: t.e} }

// Count is a whole number.
func Count(n int) Fact { return Fact{s: groupInt(int64(n))} }

// Credits is an amount of credits: "1,134.58".
func Credits(v float64) Fact { return Fact{s: CreditsText(v)} }

// Number is a number as people write one: "1,000,000,000", "0.5".
func Number(v float64) Fact { return Fact{s: CreditsText(v)} }

// Baht is an amount of money with two decimals: "39.71".
func Baht(v float64) Fact {
	s := strconv.FormatFloat(v, 'f', 2, 64)
	i := strings.IndexByte(s, '.')
	n, _ := strconv.ParseInt(s[:i], 10, 64)
	return Fact{s: groupInt(n) + s[i:]}
}

// CreditsText prints credits with thousands separators and no trailing zeros.
func CreditsText(v float64) string {
	s := strconv.FormatFloat(v, 'f', 2, 64)
	i := strings.IndexByte(s, '.')
	n, _ := strconv.ParseInt(s[:i], 10, 64)
	frac := strings.TrimRight(s[i:], "0")
	if frac == "." {
		frac = ""
	}
	out := groupInt(n) + frac
	if v < 0 && n == 0 && frac != "" {
		out = "-" + out
	}
	return out
}

func groupInt(n int64) string {
	neg := n < 0
	if neg {
		n = -n
	}
	s := strconv.FormatInt(n, 10)
	var b strings.Builder
	for i, ch := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(ch)
	}
	if neg {
		return "-" + b.String()
	}
	return b.String()
}

// clean makes a value fit one line of a sentence.
func clean(s string, max int) string {
	s = strings.Join(strings.Fields(s), " ")
	if r := []rune(s); len(r) > max {
		s = string(r[:max-1]) + "…"
	}
	return s
}

// quoted puts a value in quotation marks.
func quoted(s string) string {
	s = strings.NewReplacer("“", "‘", "”", "’", `"`, "'").Replace(clean(s, 160))
	return "“" + s + "”"
}

// readable is the value as it is when every part of it is readable, else in
// quotation marks: a value of the person's own is theirs to read, and says so.
// This is the Thai sentence's rule, as it was before AM11: a name in Latin
// letters that is not on the Thai reader's list is quoted, so that the same
// reader shows it when the item is sent.
func readable(s string, max int) string { return readableIn(Thai, s, max) }

// readableIn is readable by the rule of l. An English sentence quotes less:
// a name made of words its own reader knows ("Finance") needs no marks there,
// and would lose them — and then itself — under the Thai rule.
func readableIn(l Lang, s string, max int) string {
	s = clean(s, max)
	r := thaiReader
	if l == English {
		r = plainReader
	}
	if s == "" || r.Value(s) {
		return s
	}
	return quoted(s)
}

// named is a value as each language's sentence carries it (readableIn).
func named(s string, max int) Fact {
	f := Fact{s: readableIn(Thai, s, max), e: readableIn(English, s, max)}
	if f.e == f.s {
		f.e = ""
	}
	return f
}

// FileName is a file's name (the person's own, or a file the agent made).
func FileName(name string) Fact { return named(name, 160) }

// FileNames is a list of files' names.
func FileNames(names []string) Fact {
	th, en := make([]string, 0, len(names)), make([]string, 0, len(names))
	for _, n := range names {
		th = append(th, readableIn(Thai, n, 160))
		en = append(en, readableIn(English, n, 160))
	}
	return Fact{s: strings.Join(th, ", "), e: strings.Join(en, ", ")}
}

// Name is a person's, a department's or an agent's name.
func Name(s string) Fact { return named(s, 120) }

// Typed is what a person typed, for a sentence that puts it in quotation
// marks itself (“%s”).
func Typed(s string, max int) Fact {
	return Fact{s: strings.NewReplacer("“", "‘", "”", "’", `"`, "'").Replace(clean(s, max))}
}

// Theirs is what a person typed, in quotation marks.
func Theirs(s string) Fact { return Fact{s: quoted(s)} }

// Aside is what a person wrote, in parentheses after a sentence (an admin's
// reason for switching an agent off); nothing when they wrote nothing.
func Aside(s string) Fact {
	if s = clean(s, 300); s == "" {
		return Fact{}
	}
	in := func(l Lang) string {
		return " (" + strings.NewReplacer("(", "", ")", "").Replace(readableIn(l, s, 300)) + ")"
	}
	return Fact{s: in(Thai), e: in(English)}
}

// ContactFact is whom to ask, as a sentence names them.
func ContactFact(c Contact) Fact {
	switch {
	case c.Name != "" && c.Email != "":
		return Fact{s: Name(c.Name).s + " (" + Email(c.Email).s + ")", e: Name(c.Name).in(English) + " (" + Email(c.Email).in(English) + ")"}
	case c.Name != "":
		return Name(c.Name)
	case c.Email != "":
		return Email(c.Email)
	}
	return Fact{s: Contact{}.Label(), e: Contact{}.LabelIn(English)}
}

var (
	emailShape   = regexp.MustCompile(`^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$`)
	modelShape   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$`)
	versionShape = regexp.MustCompile(`^[0-9]+(?:\.[0-9]+){0,3}$`)
)

// Email is an e-mail address.
func Email(s string) Fact {
	if s = strings.TrimSpace(s); emailShape.MatchString(s) {
		return Fact{s: s}
	}
	return named(s, 120)
}

// ModelName is a model's name as the catalogue has it.
func ModelName(s string) Fact {
	if s = strings.TrimSpace(s); modelShape.MatchString(s) {
		return named(s, 100)
	}
	return Fact{s: quoted(s)}
}

// Version is a version number ("1.2.0").
func Version(s string) Fact {
	if s = strings.TrimSpace(s); versionShape.MatchString(s) {
		return Fact{s: s}
	}
	return Fact{s: quoted(s)}
}

// When is a moment as each language says it ("28 ก.ย. 2569 เวลา 10:05 น.", "28 Sep 2026, 10:05").
func When(t time.Time) Fact { return Fact{s: ThaiDateTime(t), e: DateTimeIn(English, t)} }

// Day is a day as each language says it ("28 ก.ย. 2569", "28 Sep 2026").
func Day(t time.Time) Fact { return Fact{s: ThaiDate(t), e: DateIn(English, t)} }

// Period is a period as each language says it ("กันยายน 2569", "September 2026").
func Period(p string) Fact {
	return Fact{s: readable(PeriodLabel(p), 60), e: readableIn(English, PeriodLabelIn(English, p), 60)}
}

// PeriodNamed is Period where a sentence names a run by its period (it takes
// the room a name takes).
func PeriodNamed(p string) Fact {
	return Fact{s: readable(PeriodLabel(p), 120), e: readableIn(English, PeriodLabelIn(English, p), 120)}
}

// ReminderPeriod is the period a reminder is for, as each language says it
// ("ก.ย. 2569", "Sep 2026"; "28 ก.ย. – 4 ต.ค. 2569", "28 Sep – 4 Oct 2026").
func ReminderPeriod(p string) Fact {
	return Fact{s: readable(ReminderPeriodLabel(p), 120), e: readableIn(English, ReminderPeriodLabelIn(English, p), 120)}
}

// Weekday is a day of the week (1 = Monday … 7 = Sunday) as each language says it.
func Weekday(n int) Fact { return Fact{s: WeekdayIn(Thai, n), e: WeekdayIn(English, n)} }

// Each is a value of the platform's own that each language says its own way
// and a function builds for a language (an amount with its currency, a length
// of time, a list of names with its last word): both, as a fact of a sentence.
func Each(say func(Lang) string) Fact { return Fact{s: say(Thai), e: say(English)} }

// EachTyped is Each for a value that holds what a person typed or named (as
// Typed: on one line, cut to max, its quotation marks made safe).
func EachTyped(max int, say func(Lang) string) Fact {
	return Fact{s: Typed(say(Thai), max).s, e: Typed(say(English), max).s}
}

// EachNamed is Each for a value a sentence carries as it carries a name (as
// Name: as it is when its language's reader takes every part of it, else in
// quotation marks).
func EachNamed(max int, say func(Lang) string) Fact {
	return Fact{s: readableIn(Thai, say(Thai), max), e: readableIn(English, say(English), max)}
}

// InBoth is such a value as a text of its own.
func InBoth(say func(Lang) string) PersonText {
	t := PersonText{s: say(Thai), e: say(English)}
	if t.e == t.s {
		t.e = ""
	}
	return t
}

// FromPackage is a sentence a package wrote for a person (a gate's what and
// next, a check's label, a manifest's words), as a person is shown it; the
// platform's own sentence when nothing of it can be shown. declared is what
// the sentence's own item carries as the person's data.
//
// AM11: the sentence is in one language — its run's. What of it is shown is
// decided by the rule of the language it is written in (reads.go): the Thai
// side of the text is what the Thai rule shows of it, exactly as before, and
// the English side what the English rule shows. Whoever writes the text
// picks the side, as for any other text.
func FromPackage(s string, otherwise PersonText, declared ...string) PersonText {
	e := englishOfPackage(s) // the English the package's manifest gives for it, if it gives one
	return readBoth(otherwise, func(r *Reader) string { return r.Shown(s) }, func(r *Reader) string { return r.Shown(e) }, declared...)
}

// readBoth reads a package's or a model's words by each language's rule, and
// says the platform's own sentence in a language whose rule shows nothing.
func readBoth(otherwise PersonText, read, readEnglish func(*Reader) string, declared ...string) PersonText {
	th, en := thaiReader, plainReader
	if len(declared) > 0 {
		th, en = readerFor(Thai, declared...), readerFor(English, declared...)
	}
	out := PersonText{s: read(th), e: readEnglish(en)}
	if out.s == "" {
		out.s = otherwise.s
	}
	if out.e == "" {
		out.e = otherwise.e
	}
	if out.e == out.s {
		out.e = ""
	}
	return out
}

// TextIn is a text that is kept in one language only — a draft's body, built
// in l from sentences of ours and a package's — as a reader of l is shown it.
func TextIn(l Lang, s string) PersonText { return PersonText{s: readerOf(l).Shown(s)} }

// StoredText is a text read back from a row that keeps one language (a
// draft's body): what a person is shown of it — it was stored as that, and
// the row does not say in which language, so it is read by the rule that
// reads both.
func StoredText(s string) PersonText { return PersonText{s: plainReader.Shown(s)} }

// StoredLabel is StoredText for a name or a heading (a draft's subject).
func StoredLabel(s string) PersonText { return PersonText{s: labelRead(plainReader, s)} }

// labelRead is a name or a heading as one reader shows it: itself when it is
// a value that reader takes, else what the reader shows of it as a sentence.
func labelRead(r *Reader, s string) string {
	if s = clean(s, 160); s != "" && r.Value(s) {
		return s
	}
	return r.Shown(s)
}

// LabelFromPackage is a name or a heading a package wrote (an input's label,
// a period, an agent's name). Unlike a sentence it may be a bare value —
// "GL", "กันยายน 2569", a file's name — and needs no Thai word to be one.
func LabelFromPackage(s string, otherwise PersonText, declared ...string) PersonText {
	e := englishOfPackage(s)
	return readBoth(otherwise, func(r *Reader) string { return labelRead(r, s) }, func(r *Reader) string { return labelRead(r, e) }, declared...)
}

// FromModel is a sentence a model wrote (an answer about the round's
// figures), as a person is shown it. The number guard is the caller's.
func FromModel(s string, otherwise PersonText) PersonText {
	return FromPackage(s, otherwise)
}

// Said is a package's name for something, as a fact of a sentence.
func Said(s string, otherwise PersonText, declared ...string) Fact {
	return Words(LabelFromPackage(s, otherwise, declared...))
}

// NoText is no text at all.
var NoText = PersonText{}
