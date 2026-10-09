package runner

import (
	"math/big"
	"regexp"
	"strings"
	"unicode"
)

// ── Numbers, as a page prints them and as an engine states them (AM14) ────
//
// The print proof asks whether a row's amounts are on the page its name is
// on. An amount is a number, and a number is printed in more than one shape:
// 3600 is 3,600.00, ฿3,600.00, 3.600,00 and 3 600,00; minus 1234.5 is
// (1,234.50), -1,234.50 and 1,234.50-. Looking for the text of one shape in
// the text of the page found none of the others — and found 3,600.00 inside
// 13,600.00. So the page is read: every number printed on it is parsed into
// its value, and a row's numbers are looked for among those values.

// decimal is a number in decimal digits, as written.
type decimal struct {
	neg  bool
	ip   string // the digits before the decimal mark
	frac string // the digits after it
}

// canonical is the value with nothing of how it was written left: no leading
// or trailing zeros, no mark without decimals, no sign on a zero.
func (d decimal) canonical() string {
	ip := strings.TrimLeft(d.ip, "0")
	frac := strings.TrimRight(d.frac, "0")
	if ip == "" {
		ip = "0"
	}
	s := ip
	if frac != "" {
		s += "." + frac
	}
	if d.neg && s != "0" {
		s = "-" + s
	}
	return s
}

func (d decimal) isZero() bool { return d.canonical() == "0" }

// rounded is the value at k decimals, a half rounded away from zero — how a
// number format prints it and how the working papers round an amount.
func (d decimal) rounded(k int) string {
	frac := d.frac
	for len(frac) <= k {
		frac += "0"
	}
	n, _ := new(big.Int).SetString("0"+d.ip+frac[:k], 10)
	if frac[k] >= '5' {
		n.Add(n, big.NewInt(1))
	}
	s := n.String()
	for len(s) <= k {
		s = "0" + s
	}
	return decimal{neg: d.neg, ip: s[:len(s)-k], frac: s[len(s)-k:]}.canonical()
}

var (
	// a cell's own value, as an engine writes it into a row's identity
	plainNumber = regexp.MustCompile(`^-?\d+(?:\.\d+)?$`)
	// that, or the working papers' money format: 3,600.00 · (1,234.50)
	statedNumber = regexp.MustCompile(`^(\()?(-)?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(\))?$`)
)

// stated reads a number an engine states: a plain decimal ("3600",
// "-8560.5") or an amount in the working papers' own format ("3,600.00",
// "(1,234.50)"). Anything else — a dash for a zero, a word — is not one.
func stated(s string) (decimal, bool) {
	m := statedNumber.FindStringSubmatch(strings.TrimSpace(s))
	if m == nil || (m[1] == "") != (m[5] == "") {
		return decimal{}, false
	}
	return decimal{neg: m[1] != "" || m[2] != "", ip: strings.ReplaceAll(m[3], ",", ""), frac: m[4]}, true
}

// printedToken is one run of digits and the marks between them, where it
// stands on the page.
type printedToken struct {
	start, end int // in runes; end is one past the last digit
	groups     []string
	seps       []rune // ',' · '.' · '\'' · ' ' (a space of any width)
}

func isDigitRune(r rune) bool { return r >= '0' && r <= '9' }

// markBetweenDigits is a mark a number is printed with between two digits:
// a comma, a point, an apostrophe, or a space that does not break.
func markBetweenDigits(r rune) (rune, bool) {
	switch r {
	case ',', '.':
		return r, true
	case '\'', '’':
		return '\'', true
	case 0x00A0, 0x2009, 0x202F:
		return ' ', true
	}
	return 0, false
}

func scanNumbers(rs []rune) []printedToken {
	var out []printedToken
	for i := 0; i < len(rs); {
		if !isDigitRune(rs[i]) {
			i++
			continue
		}
		t := printedToken{start: i}
		for {
			j := i
			for j < len(rs) && isDigitRune(rs[j]) {
				j++
			}
			t.groups = append(t.groups, string(rs[i:j]))
			i = j
			if i+1 < len(rs) && isDigitRune(rs[i+1]) {
				if k, ok := markBetweenDigits(rs[i]); ok {
					t.seps = append(t.seps, k)
					i++
					continue
				}
			}
			break
		}
		t.end = i
		out = append(out, t)
	}
	return out
}

// readings are the values a token can be. The last mark is the decimal mark
// when the marks before it are another kind and stand between thousands; all
// the marks stand between thousands when they are one kind and every group
// after the first has three digits. "1,234" is both, and is read both ways.
// A token that is neither (a date, a list: 11.09.2569 · 1,2,3) is its parts.
func (t printedToken) readings() []decimal {
	n := len(t.seps)
	if n == 0 {
		return []decimal{{ip: t.groups[0]}}
	}
	thousands := func(groups []string, seps []rune) bool {
		if len(seps) == 0 {
			return true
		}
		if len(groups[0]) > 3 {
			return false
		}
		for i, g := range groups[1:] {
			if len(g) != 3 || seps[i] != seps[0] {
				return false
			}
		}
		return true
	}
	var out []decimal
	if last := t.seps[n-1]; (last == ',' || last == '.') && thousands(t.groups[:n], t.seps[:n-1]) && (n == 1 || t.seps[0] != last) {
		out = append(out, decimal{ip: strings.Join(t.groups[:n], ""), frac: t.groups[n]})
	}
	if thousands(t.groups, t.seps) {
		out = append(out, decimal{ip: strings.Join(t.groups, "")})
	}
	if len(out) == 0 {
		for _, g := range t.groups {
			out = append(out, decimal{ip: g})
		}
	}
	return out
}

func isMinusRune(r rune) bool { return r == '-' || r == 0x2212 }

func isCurrencyRune(r rune) bool {
	switch r {
	case '฿', '$', '€', '£', '¥':
		return true
	}
	return false
}

// beforeCurrency steps left over a currency printed in front of a number —
// a symbol, or a code of three capitals, with or without a space after it —
// and returns the place before it; j itself when there is none.
func beforeCurrency(rs []rune, j int) int {
	k := j
	if k >= 0 && (rs[k] == ' ' || rs[k] == 0x00A0) {
		k--
	}
	if k >= 0 && isCurrencyRune(rs[k]) {
		return k - 1
	}
	upper := func(i int) bool { return i >= 0 && rs[i] >= 'A' && rs[i] <= 'Z' }
	if upper(k) && upper(k-1) && upper(k-2) && (k-3 < 0 || !unicode.IsLetter(rs[k-3])) {
		return k - 3
	}
	return j
}

// sign says whether the number printed at [start, end) is a negative: in
// brackets, or with a minus sign against it, before or after. A dash with a
// space after it is a cell that printed a zero, and a hyphen after a letter
// or a digit joins the parts of a document's number: neither is a sign.
// both: the number is also read as a positive — a whole number in brackets,
// (3), is a marker as often as a negative.
func sign(rs []rune, start, end int, whole bool) (neg, both bool) {
	j := beforeCurrency(rs, start-1)
	if j >= 0 && isMinusRune(rs[j]) && (j == 0 || !(unicode.IsLetter(rs[j-1]) || unicode.IsDigit(rs[j-1]))) {
		return true, false
	}
	if j >= 0 && rs[j] == '(' {
		k := end
		for k < len(rs) && isCurrencyRune(rs[k]) {
			k++
		}
		if k < len(rs) && rs[k] == ')' {
			return true, whole
		}
	}
	// a minus printed after the number (1,234.50-); a line that breaks at a hyphen ends the same way, so
	// the number is read both ways
	if end < len(rs) && isMinusRune(rs[end]) && (end+1 == len(rs) || unicode.IsSpace(rs[end+1])) {
		return true, true
	}
	return false, false
}

// printedNumbers is every number a page prints, by its canonical value.
func printedNumbers(page string) map[string]bool {
	rs := []rune(page)
	out := map[string]bool{}
	add := func(t printedToken) {
		neg, both := sign(rs, t.start, t.end, len(t.seps) == 0)
		for _, d := range t.readings() {
			d.neg = neg
			out[d.canonical()] = true
			if both {
				d.neg = false
				out[d.canonical()] = true
			}
		}
	}
	toks := scanNumbers(rs)
	for i, t := range toks {
		add(t)
		// thousands set apart by a plain space (2 345 678,90): read as one number as well, where a
		// group of one to three digits is followed, one space on, by groups of exactly three
		if len(t.seps) > 0 || len(t.groups[0]) > 3 {
			continue
		}
		chain := t
		for _, nx := range toks[i+1:] {
			if nx.start != chain.end+1 || rs[chain.end] != ' ' || len(nx.groups[0]) != 3 {
				break
			}
			chain = printedToken{start: chain.start, end: nx.end,
				groups: append(append([]string{}, chain.groups...), nx.groups...),
				seps:   append(append(append([]rune{}, chain.seps...), ' '), nx.seps...)}
			add(chain)
			if len(nx.seps) > 0 {
				break
			}
		}
	}
	return out
}
