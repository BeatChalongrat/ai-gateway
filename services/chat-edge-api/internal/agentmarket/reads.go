package agentmarket

import (
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

// ── Text that reads as written for a person (AM6) ────────────────────────
//
// AM5 took out of a text the error shapes it knew (errorText, engineWords in
// failure.go). A list of what to refuse lets through whatever nobody listed.
// This file is the other way round: it says what a person may read, and
// whatever is not that is withheld.
//
// A text reads as written for a person when it has Thai words and everything
// else in it is one of:
//
//  1. a number as people write one (an amount, a date, a time, a percentage);
//  2. a file's name;
//  3. words inside quotation marks “ ” or " " — a value quoted from the
//     person's own file;
//  4. a word the product itself uses in its Thai sentences (productWords);
//  5. a code of the person's data, letters with digits: a document number, an
//     account code, a cell (IV6909-0015, 1130-01, B12, Sheet1!A1);
//  6. one capital letter (a column, a debit or credit mark);
//  7. an e-mail address;
//  8. a word the same item declares as the person's data (a cell of the
//     evidence a question carries);
//  9. the name a document gives itself in front of its own file —
//     "Cloudnine Software Pte. Ltd. เลขที่ CN-2026-00917 (invoice.pdf): …" —
//     when the same item declares that file;
//  10. punctuation the product's own sentences use.
//
// An identifier_with_underscores, a path, a type's name, an address, a word
// nobody declared: none of them is on the list, whatever shape it has.
//
// AM11 — English. A run in English is written in English, so "has Thai words"
// cannot be the whole rule any more. In English a text reads as written for a
// person when every Latin word in it is one the product's own English
// sentences use (english.go: the catalogue's vocabulary, and the packages'),
// or is one of 1–10 above. This is weaker than the Thai rule and is meant to
// be: Thai text is never an error's text, while an error can be made of
// ordinary English words — so the list of error shapes (LooksLikeError) is
// asked as before, and no English sentence of the catalogue may read like one
// (TestNoEnglishSentenceReadsLikeAnError). A reader is strict Thai (an item
// written in Thai: an English sentence in it is refused, exactly as before
// AM11) or reads both (an item written in English, and text whose item is not
// known where it is read).
//
// What is not on the list is withheld, not repaired into something else: a
// parenthesis that holds any of it is left out, and what stands between two
// Thai words is either readable as a whole or replaced by "…" as a whole (so
// no piece of an error's text is shown beside the piece that was refused). A
// text with no Thai words left shows nothing, and its field says the
// platform's own sentence instead. The text itself stays where it was
// written: in the row, and on the run's steps.

// productWords are the Latin words the product's own Thai sentences use. The
// list was read from the sentences themselves — the four packages' checks,
// scripts and manifests, what their eval sets produce, the purchase-order
// package's cards, and the messages of this package — not thought up.
var productWords = wordSet(
	// the product and its parts
	"agent", "Agent", "Market", "OCR", "Sarabun", "LibreOffice", "SAP",
	// kinds of file
	"PDF", "Excel", "CSV", "XML", "JSON", "Word", "PowerPoint", "e-Tax",
	"pdf", "xlsx", "xls", "csv", "xml", "docx", "pptx", "txt",
	// accounting words Thai accountants write in Latin letters
	"VAT", "WHT", "GL", "PO", "GR", "ID", "Statement",
	// units and currencies
	"THB", "USD", "DPI", "KB", "MB", "GB",
	// months, as a date hint names them ("ค.ศ. เช่น September 2026")
	"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December",
	// AM11: and as an English date writes them ("28 Sep 2026", lang.go)
	"Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

func wordSet(words ...string) map[string]bool {
	m := make(map[string]bool, len(words))
	for _, w := range words {
		m[w] = true
	}
	return m
}

// readableMarks is the punctuation the product's sentences use (read from
// the same sentences as productWords; "*" is the emphasis of an answer built
// from a figure's record).
const readableMarks = ".,:;!?()/-–—−+=×÷%·…'’‘“”\"@→฿≈#*"

var (
	// what is read as one thing before anything else: a quoted value, an
	// e-mail address (or the "@domain" an address must end with), a file's name
	protectedSpan = regexp.MustCompile(`“[^“”\n]{1,200}”|"[^"\n]{1,200}"|` +
		`[A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+|` +
		`(?i:[^\s,;:()"'“”]+\.(?:xlsx|xlsm|xls|csv|pdf|xml|jpe?g|png|docx?|pptx?|txt|json|zip)\b)`)
	// a word or a number with what joins its parts (IV6909-0015, 1,234.56, Sheet1!A1)
	latinToken = regexp.MustCompile(`^[A-Za-z0-9]+(?:[-./&'_!:#+,][A-Za-z0-9]+)*`)
	dataCode   = regexp.MustCompile(`^[A-Za-z0-9]+(?:[-./!:#][A-Za-z0-9]+)*$`)
)

type pieceKind int

const (
	pThai    pieceKind = iota // Thai words
	pSpace                    // white space
	pMark                     // punctuation the product's sentences use
	pPlain                    // a number, a file's name, a quoted value, an address
	pLatin                    // a Latin word that is on the list
	pEnglish                  // AM11: a word the product's English sentences use
	pWord                     // a Latin word that is not
	pNo                       // anything else that is not
)

type piece struct {
	s    string
	kind pieceKind
	file bool // a file's name
}

// Reader reads texts of one item: what the item declares as the person's
// data (cells of its evidence, its files) is readable in its sentences.
type Reader struct {
	words map[string]bool
	files map[string]bool
	// thaiOnly: the text is of an item written in Thai — an English word is
	// not on its list (the rule as it was before AM11)
	thaiOnly bool
}

// readerFor is the reader for an item written in l: strict Thai, or both.
func readerFor(l Lang, declared ...string) *Reader {
	r := NewReader(declared...)
	r.thaiOnly = l != English
	return r
}

// NewReader is a reader for an item that declares these values as the
// person's own data. It reads both languages (readerFor makes it strict).
func NewReader(declared ...string) *Reader {
	r := &Reader{words: map[string]bool{}, files: map[string]bool{}}
	r.Declare(declared...)
	return r
}

// Declare adds values the item carries as the person's data.
func (r *Reader) Declare(values ...string) {
	for _, v := range values {
		if len(v) > 4000 || len(r.words) > 5000 {
			continue
		}
		for _, p := range split(v) {
			switch {
			case p.file:
				r.files[p.s] = true
			case p.kind == pWord, p.kind == pEnglish:
				// a word of the person's data is theirs whether or not an English sentence of ours uses it
				r.words[p.s] = true
			}
		}
	}
}

func isThai(c rune) bool { return c >= 0x0E00 && c <= 0x0E7F }

// split cuts a text into pieces and says of each whether it is on the list.
func split(text string) []piece {
	var out []piece
	add := func(s string, k pieceKind) {
		if n := len(out); n > 0 && out[n-1].kind == k && (k == pThai || k == pSpace) {
			out[n-1].s += s
			return
		}
		out = append(out, piece{s: s, kind: k})
	}
	plain := func(s string) {
		for len(s) > 0 {
			c, size := utf8.DecodeRuneInString(s)
			switch {
			case isThai(c):
				add(s[:size], pThai)
			case unicode.IsSpace(c) || c == 0x200B || c == 0x200C || c == 0x200D || c == 0xFEFF:
				add(s[:size], pSpace)
			case c < 0x80 && (unicode.IsLetter(c) || unicode.IsDigit(c)):
				tok := latinToken.FindString(s)
				add(tok, tokenKind(tok))
				size = len(tok)
			case strings.ContainsRune(readableMarks, c):
				add(s[:size], pMark)
			default:
				add(s[:size], pNo)
			}
			s = s[size:]
		}
	}
	at := 0
	for _, span := range protectedSpan.FindAllStringIndex(text, -1) {
		plain(text[at:span[0]])
		s := text[span[0]:span[1]]
		p := piece{s: s, kind: pPlain}
		first := rune(s[0])
		switch {
		case strings.HasPrefix(s, "“") || first == '"':
		case strings.Contains(s, "@"):
		case strings.ContainsAny(s, `/\`):
			p.kind = pNo // a path is not a file's name
		default:
			p.file = true
		}
		out = append(out, p)
		at = span[1]
	}
	plain(text[at:])
	return out
}

// tokenKind says what a run of Latin letters and digits is.
func tokenKind(tok string) pieceKind {
	letters, digits := false, false
	for _, c := range tok {
		switch {
		case c >= '0' && c <= '9':
			digits = true
		case (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'):
			letters = true
		}
	}
	switch {
	case !letters:
		// a number as people write one: an address of a machine has more dots
		if len(tok) <= 40 && strings.Count(tok, ".") <= 2 && strings.Count(tok, ":") <= 2 && !strings.ContainsAny(tok, "_&'!#+") {
			return pPlain
		}
		return pNo
	case productWords[tok]:
		return pLatin
	case len(tok) == 1 && tok[0] >= 'A' && tok[0] <= 'Z':
		return pLatin
	case digits && len(tok) <= 40 && dataCode.MatchString(tok):
		// asked before the English vocabulary: a code of the person's data is one in both languages,
		// as it was before there were two
		return pLatin
	case isEnglishWord(tok):
		return pEnglish
	}
	return pWord
}

// read is split with what the item declares applied: a declared word, and the
// name a document gives itself in front of its declared file.
func (r *Reader) read(text string) []piece {
	ps := split(text)
	if r == nil {
		return ps
	}
	for i := range ps {
		// a declared word is the person's data. Under the Thai rule that holds for a word an English
		// sentence of ours happens to use too (before AM11 it was a word like any other); under the
		// English rule such a word is already read, as a word of the sentence
		if (ps[i].kind == pWord || (ps[i].kind == pEnglish && r.thaiOnly)) && r.words[ps[i].s] {
			ps[i].kind = pLatin
		}
	}
	// "<name> (<file>):" — the file is the item's own
	for i := 0; i+2 < len(ps); i++ {
		if ps[i].s != "(" || !ps[i+1].file || !r.files[ps[i+1].s] || ps[i+2].s != ")" {
			continue
		}
		j := i + 3
		for j < len(ps) && ps[j].kind == pSpace {
			j++
		}
		if j >= len(ps) || ps[j].s != ":" {
			continue
		}
		start, n := i, 0
		for start > 0 {
			p := ps[start-1]
			if p.kind == pNo || strings.Contains(p.s, "\n") || (p.kind == pMark && strings.Contains(":;—·", p.s)) {
				break
			}
			if n += len([]rune(p.s)); n > 80 {
				break
			}
			start--
		}
		if n > 80 {
			continue
		}
		for k := start; k < i; k++ {
			if ps[k].kind == pWord || (ps[k].kind == pEnglish && r.thaiOnly) {
				ps[k].kind = pLatin
			}
		}
	}
	return ps
}

// refused: the piece is not on this reader's list.
func (r *Reader) refused(k pieceKind) bool {
	return k == pWord || k == pNo || (k == pEnglish && r != nil && r.thaiOnly)
}

// said: the piece is a word of a sentence in a language this reader reads.
func (r *Reader) said(p piece) bool {
	return p.kind == pThai || (p.kind == pEnglish && (r == nil || !r.thaiOnly))
}

// hasWords: the line has a word of a language this reader reads.
func (r *Reader) hasWords(line string) bool {
	for _, p := range r.read(line) {
		if r.said(p) {
			return true
		}
	}
	return false
}

// Reads: the text reads as written for a person, as it is.
func (r *Reader) Reads(text string) bool {
	words, latin := false, false
	for _, p := range r.read(text) {
		switch {
		case r.refused(p.kind):
			return false
		case r.said(p):
			words = true
		case p.kind == pLatin:
			latin = true
		}
	}
	// without a word of a language it is a value (a file's name, an amount), not a sentence
	return (words || !latin) && !LooksLikeError(text)
}

// Value: the text is a value a person may read — a period, an amount, a
// currency, a code of their data — and nothing else. It needs no Thai word.
func (r *Reader) Value(text string) bool {
	if len([]rune(text)) > 120 {
		return false
	}
	for _, p := range r.read(text) {
		if r.refused(p.kind) {
			return false
		}
	}
	return !LooksLikeError(text)
}

// Shown is what a person is shown of the text: the text itself when it reads
// as written for a person; else the text with what is not for a person
// withheld; "" when nothing of it is for a person. A text of several lines
// (an answer built from a figure's record) is read line by line: a line that
// shows nothing is left out.
func (r *Reader) Shown(text string) string {
	text = strings.TrimSpace(text)
	if text == "" || r.Reads(text) {
		return text
	}
	if !strings.Contains(text, "\n") {
		return r.shownLine(text)
	}
	var lines []string
	said := false
	for _, line := range strings.Split(text, "\n") {
		if strings.TrimSpace(line) == "" {
			if n := len(lines); n > 0 && lines[n-1] != "" {
				lines = append(lines, "")
			}
			continue
		}
		if r.Reads(line) {
			lines = append(lines, strings.TrimRight(line, " \t\r"))
			said = said || r.hasWords(line)
		} else if s := r.shownLine(line); s != "" {
			lines = append(lines, s)
			said = true
		}
	}
	if !said {
		return ""
	}
	return strings.TrimSpace(strings.Join(lines, "\n"))
}

// shownLine is Shown for one line.
func (r *Reader) shownLine(text string) string {
	ps := r.read(text)
	// AM11: an error's text in Thai has no Thai word and so says nothing. In English it is made of
	// ordinary words, some of them the product's own ("step", "failed", "status") — showing those
	// would show half an error. A line with no Thai word that has an error's shape is not shown at all.
	if (r == nil || !r.thaiOnly) && LooksLikeError(text) {
		thai := false
		for _, p := range ps {
			thai = thai || p.kind == pThai
		}
		if !thai {
			return ""
		}
	}
	// a parenthesis that holds anything refused is left out of its sentence
	for i := 0; i < len(ps); i++ {
		if ps[i].s != "(" {
			continue
		}
		end, bad := -1, false
		for j := i + 1; j < len(ps); j++ {
			if ps[j].s == "(" {
				break
			}
			if ps[j].s == ")" {
				end = j
				break
			}
			bad = bad || r.refused(ps[j].kind)
		}
		if end < 0 {
			// cut before it closed
			for j := i + 1; j < len(ps); j++ {
				bad = bad || r.refused(ps[j].kind)
			}
			end = len(ps) - 1
		}
		if !bad {
			continue
		}
		from := i
		if from > 0 && ps[from-1].kind == pSpace {
			from--
		}
		ps = append(ps[:from:from], ps[end+1:]...)
		i = from - 1
	}
	// what stands between two words of the sentence (or two "·") is readable as a whole, or withheld as a whole
	var out []piece
	for i := 0; i < len(ps); {
		if r.said(ps[i]) || ps[i].s == "·" {
			out = append(out, ps[i])
			i++
			continue
		}
		j := i
		for j < len(ps) && !r.said(ps[j]) && ps[j].s != "·" {
			j++
		}
		stretch := ps[i:j]
		i = j
		bad := false
		for _, p := range stretch {
			bad = bad || r.refused(p.kind)
		}
		if !bad {
			out = append(out, stretch...)
			continue
		}
		content := func(p piece) bool { return p.kind != pSpace && p.kind != pMark }
		first, last := -1, -1
		for k, p := range stretch {
			if content(p) {
				if first < 0 {
					first = k
				}
				last = k
			}
		}
		if first < 0 { // only a sign that is not the product's: it goes
			for _, p := range stretch {
				if p.kind != pNo {
					out = append(out, p)
				}
			}
			continue
		}
		// an opening mark straight before what is withheld goes with it
		for first > 0 && stretch[first-1].kind == pMark && strings.Contains("“\"'‘(", stretch[first-1].s) {
			first--
		}
		out = append(out, stretch[:first]...)
		out = append(out, piece{s: "…", kind: pMark})
		out = append(out, stretch[last+1:]...)
	}
	var b strings.Builder
	kept := 0
	for _, p := range out {
		if r.said(p) {
			kept += len([]rune(p.s))
		}
		b.WriteString(p.s)
	}
	shown := strings.Join(strings.Fields(b.String()), " ")
	for _, fix := range [][2]string{{"… …", "…"}, {"( ", "("}, {" )", ")"}, {" :", ":"}, {" ,", ","}, {"()", ""}} {
		shown = strings.ReplaceAll(shown, fix[0], fix[1])
	}
	shown = strings.TrimRight(strings.TrimLeft(strings.TrimSpace(shown), " ·,:—–"), " ·,:—–")
	// fewer letters of a sentence than a word has: nothing of it was written for a person
	if kept < 4 || LooksLikeError(shown) {
		return ""
	}
	return shown
}

// plainReader reads both languages: an English item's reader, and the reader
// of a text read back from a row whose language is not known there.
// thaiReader is the strict one — the rule as it was before AM11.
var (
	plainReader = NewReader()
	thaiReader  = readerFor(Thai)
)

func readerOf(l Lang, declared ...string) *Reader {
	switch {
	case len(declared) > 0:
		return readerFor(l, declared...)
	case l == English:
		return plainReader
	}
	return thaiReader
}

// ReadsForPerson: the text reads as written for a person — by the Thai rule,
// which is what every caller before AM11 means by it (ReadsIn for a text in
// another language). declared is what the text's own item carries as the
// person's data.
func ReadsForPerson(text string, declared ...string) bool { return ReadsIn(Thai, text, declared...) }

// ReadsIn: the text reads as written for a person, by the rule of l.
func ReadsIn(l Lang, text string, declared ...string) bool {
	return readerOf(l, declared...).Reads(text)
}

// ShownToPerson is what a person is shown of a text ("" = nothing), by the
// Thai rule (ShownIn for a text in another language).
func ShownToPerson(text string, declared ...string) string { return ShownIn(Thai, text, declared...) }

// ShownIn is what a person is shown of a text written in l ("" = nothing).
func ShownIn(l Lang, text string, declared ...string) string {
	return readerOf(l, declared...).Shown(text)
}
