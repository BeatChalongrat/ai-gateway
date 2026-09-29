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

// PersonText is text a person may read.
type PersonText struct{ s string }

// wording is a sentence written in the source. Code outside this package
// cannot name the type, so it can only pass a literal.
type wording string

func (t PersonText) String() string { return t.s }

// Empty: there is nothing to read.
func (t PersonText) Empty() bool { return strings.TrimSpace(t.s) == "" }

// MarshalJSON writes the text as a JSON string.
func (t PersonText) MarshalJSON() ([]byte, error) { return json.Marshal(t.s) }

// UnmarshalJSON reads a text back from a row or a payload. What comes back is
// only what a person is shown of it: reading JSON is no way around the rule.
func (t *PersonText) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err != nil {
		t.s = ""
		return nil // a payload with something else in a text's place has no text
	}
	*t = LabelFromPackage(s, NoText)
	return nil
}

// Fact is a typed value put into a sentence.
type Fact struct{ s string }

// Text is a sentence written in the source, with its facts (each %s takes one).
func Text(sentence wording, facts ...Fact) PersonText {
	if len(facts) == 0 {
		return PersonText{s: string(sentence)}
	}
	args := make([]any, len(facts))
	for i, f := range facts {
		args[i] = f.s
	}
	return PersonText{s: fmt.Sprintf(string(sentence), args...)}
}

// Join puts texts one after another.
func Join(sep wording, texts ...PersonText) PersonText {
	var parts []string
	for _, t := range texts {
		if !t.Empty() {
			parts = append(parts, t.s)
		}
	}
	return PersonText{s: strings.Join(parts, string(sep))}
}

// Words is a text as a fact of another sentence.
func Words(t PersonText) Fact { return Fact{t.s} }

// Count is a whole number.
func Count(n int) Fact { return Fact{groupInt(int64(n))} }

// Credits is an amount of credits: "1,134.58".
func Credits(v float64) Fact { return Fact{CreditsText(v)} }

// Number is a number as people write one: "1,000,000,000", "0.5".
func Number(v float64) Fact { return Fact{CreditsText(v)} }

// Baht is an amount of money with two decimals: "39.71".
func Baht(v float64) Fact {
	s := strconv.FormatFloat(v, 'f', 2, 64)
	i := strings.IndexByte(s, '.')
	n, _ := strconv.ParseInt(s[:i], 10, 64)
	return Fact{groupInt(n) + s[i:]}
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
func readable(s string, max int) string {
	s = clean(s, max)
	if s == "" || plainReader.Value(s) {
		return s
	}
	return quoted(s)
}

// FileName is a file's name (the person's own, or a file the agent made).
func FileName(name string) Fact { return Fact{readable(name, 160)} }

// FileNames is a list of files' names.
func FileNames(names []string) Fact {
	out := make([]string, 0, len(names))
	for _, n := range names {
		out = append(out, readable(n, 160))
	}
	return Fact{strings.Join(out, ", ")}
}

// Name is a person's, a department's or an agent's name.
func Name(s string) Fact { return Fact{readable(s, 120)} }

// Typed is what a person typed, for a sentence that puts it in quotation
// marks itself (“%s”).
func Typed(s string, max int) Fact {
	return Fact{strings.NewReplacer("“", "‘", "”", "’", `"`, "'").Replace(clean(s, max))}
}

// Theirs is what a person typed, in quotation marks.
func Theirs(s string) Fact { return Fact{quoted(s)} }

// Aside is what a person wrote, in parentheses after a sentence (an admin's
// reason for switching an agent off); nothing when they wrote nothing.
func Aside(s string) Fact {
	if s = clean(s, 300); s == "" {
		return Fact{}
	}
	return Fact{" (" + strings.NewReplacer("(", "", ")", "").Replace(readable(s, 300)) + ")"}
}

// ContactFact is whom to ask, as a sentence names them.
func ContactFact(c Contact) Fact {
	switch {
	case c.Name != "" && c.Email != "":
		return Fact{Name(c.Name).s + " (" + Email(c.Email).s + ")"}
	case c.Name != "":
		return Name(c.Name)
	case c.Email != "":
		return Email(c.Email)
	}
	return Fact{Contact{}.Label()}
}

var (
	emailShape   = regexp.MustCompile(`^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$`)
	modelShape   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$`)
	versionShape = regexp.MustCompile(`^[0-9]+(?:\.[0-9]+){0,3}$`)
)

// Email is an e-mail address.
func Email(s string) Fact {
	if s = strings.TrimSpace(s); emailShape.MatchString(s) {
		return Fact{s}
	}
	return Fact{readable(s, 120)}
}

// ModelName is a model's name as the catalogue has it.
func ModelName(s string) Fact {
	if s = strings.TrimSpace(s); modelShape.MatchString(s) {
		return Fact{readable(s, 100)}
	}
	return Fact{quoted(s)}
}

// Version is a version number ("1.2.0").
func Version(s string) Fact {
	if s = strings.TrimSpace(s); versionShape.MatchString(s) {
		return Fact{s}
	}
	return Fact{quoted(s)}
}

// When is a moment as a Thai reader says it ("28 ก.ย. 2569 เวลา 10:05 น.").
func When(t time.Time) Fact { return Fact{ThaiDateTime(t)} }

// Period is a period as a Thai reader says it ("กันยายน 2569").
func Period(p string) Fact { return Fact{readable(PeriodLabel(p), 60)} }

// FromPackage is a sentence a package wrote for a person (a gate's what and
// next, a check's label, a manifest's words), as a person is shown it; the
// platform's own sentence when nothing of it can be shown. declared is what
// the sentence's own item carries as the person's data.
func FromPackage(s string, otherwise PersonText, declared ...string) PersonText {
	if shown := ShownToPerson(s, declared...); shown != "" {
		return PersonText{s: shown}
	}
	return otherwise
}

// LabelFromPackage is a name or a heading a package wrote (an input's label,
// a period, an agent's name). Unlike a sentence it may be a bare value —
// "GL", "กันยายน 2569", a file's name — and needs no Thai word to be one.
func LabelFromPackage(s string, otherwise PersonText, declared ...string) PersonText {
	r := plainReader
	if len(declared) > 0 {
		r = NewReader(declared...)
	}
	if s = clean(s, 160); s != "" && r.Value(s) {
		return PersonText{s: s}
	}
	if shown := r.Shown(s); shown != "" {
		return PersonText{s: shown}
	}
	return otherwise
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
