package runner

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// AM14: the print proof compares a number as a number. It looked for a row's amounts as text on the
// printed page, so 3600 was "not on the page" that printed 3,600.00 — and a run of the invoice-to-PO
// agent with anything received and not billed delivered nothing. The fixtures in testdata/printproof
// are the shapes a number is printed in (and the shapes that must still fail), each kept as a page and
// the rows listed for it.

type printFixture struct {
	What  string     `json:"what"`
	OK    bool       `json:"ok"`
	Bad   []int      `json:"bad"`
	Pages []string   `json:"pages"`
	Rows  []printRow `json:"rows"`
}

func printFixtures(t *testing.T) map[string]printFixture {
	t.Helper()
	files, err := filepath.Glob(filepath.Join("testdata", "printproof", "*.json"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no print proof fixtures: %v", err)
	}
	out := map[string]printFixture{}
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var fx printFixture
		if err := json.Unmarshal(raw, &fx); err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		if fx.What == "" || len(fx.Pages) == 0 || len(fx.Rows) == 0 {
			t.Fatalf("%s: a fixture says what it is, and has pages and rows", f)
		}
		out[strings.TrimSuffix(filepath.Base(f), ".json")] = fx
	}
	return out
}

func TestThePrintProofReadsANumberAsANumber(t *testing.T) {
	fixtures := printFixtures(t)
	// every shape the card names has its fixture, kept
	for _, name := range []string{"po_received_not_billed", "po_received_not_billed_values", "grouping", "currency_symbol",
		"negative_in_brackets", "decimal_mark", "zero_as_dash", "another_page", "not_the_number"} {
		if _, ok := fixtures[name]; !ok {
			t.Errorf("the fixture %q is gone", name)
		}
	}
	names := make([]string, 0, len(fixtures))
	for n := range fixtures {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, name := range names {
		fx := fixtures[name]
		t.Run(name, func(t *testing.T) {
			c := checkPrinted(CheckResult{Pages: len(fx.Pages), RawText: strings.Join(fx.Pages, "\f")}, fx.Rows)
			detail := c.Detail.String()
			if c.OK != fx.OK {
				t.Fatalf("%s\nok = %v, want %v: %s", fx.What, c.OK, fx.OK, detail)
			}
			// the rows it names are the rows that are not on one page, and no other (it names three at most)
			for _, r := range fx.Rows {
				named := strings.Contains(detail, fmt.Sprintf("แถว %d ", r.Row))
				want := false
				for i, b := range fx.Bad {
					want = want || (b == r.Row && i < 3)
				}
				if named != want {
					t.Errorf("row %d named = %v, want %v: %s", r.Row, named, want, detail)
				}
			}
		})
	}
}

func TestANumberIsReadOffAPageInTheShapesItIsPrintedIn(t *testing.T) {
	for _, c := range []struct {
		page string
		has  []string // numbers the page prints, in canonical form
		not  []string // numbers it does not print
	}{
		{"3,600.00", []string{"3600"}, []string{"3", "600", "0"}},
		{"13,600.00 3,606.00", []string{"13600", "3606"}, []string{"3600"}},
		{"฿3,600.00 · THB 980.00 · 12,000.00 บาท · $1,200.50", []string{"3600", "980", "12000", "1200.5"}, nil},
		{"(45,000.00) -1,234.50 −980.00 75.25- (฿450.00) -฿75.5", []string{"-45000", "-1234.5", "-980", "-75.25", "-450", "-75.5"}, []string{"45000", "1234.5", "980", "450"}},
		// a dash is a zero cell and a hyphen joins the parts of a number of a document: neither is a sign
		{"360.00 10 - 10 3,600.00 - -", []string{"360", "10", "3600"}, []string{"-10", "-3600"}},
		{"PO6909-008 IV6909-0210 2026-10-09", []string{"6909", "8", "210", "2026", "10", "9"}, []string{"-8", "-210", "-10", "-9"}},
		// a small whole number in brackets is a marker as often as a negative
		{"(3) ดอกเบี้ยรับ", []string{"3", "-3"}, nil},
		// another locale's marks
		{"3.600,00", []string{"3600"}, []string{"3.6"}},
		{"1 250,50 · 12 000,00 · 1 234 567,89 · 2 345 678,90 · 1'250.50", []string{"1250.5", "12000", "1234567.89", "2345678.9", "1250.5"}, nil},
		{"12,5 และ 0,07", []string{"12.5", "0.07"}, nil},
		// one mark and three digits after it can be read two ways, and is
		{"1,234 · 3.600", []string{"1234", "1.234", "3600", "3.6"}, nil},
		// a date and a list are not a number with thousands in it
		{"11.09.2569 · A4, B4, C4 · 1,2,3", []string{"11", "9", "2569", "4", "1", "2", "3"}, []string{"11092569", "123"}},
	} {
		got := printedNumbers(c.page)
		for _, n := range c.has {
			if !got[n] {
				t.Errorf("%q prints %s: read %v", c.page, n, numbersRead(got))
			}
		}
		for _, n := range c.not {
			if got[n] {
				t.Errorf("%q does not print %s: read %v", c.page, n, numbersRead(got))
			}
		}
	}
}

func TestANumberTheEngineStatesIsReadPlainOrInThePapersOwnFormat(t *testing.T) {
	for in, want := range map[string]string{
		"3600": "3600", "3600.00": "3600", "-8560.5": "-8560.5", "0.070": "0.07", "0": "0", "-0.00": "0",
		"3,600.00": "3600", "(1,234.50)": "-1234.5", "1,234,567.89": "1234567.89", "0210": "210",
	} {
		d, ok := stated(in)
		if !ok || d.canonical() != want {
			t.Errorf("stated(%q) = %q %v, want %q", in, d.canonical(), ok, want)
		}
	}
	for _, in := range []string{"-", "", "PO6909-008", "12.5%", "(1,234.50", "1,23", "1.2.3", "3 600"} {
		if d, ok := stated(in); ok {
			t.Errorf("stated(%q) read %q; it is not a number an engine states", in, d.canonical())
		}
	}
	// an amount is the same amount at the two decimals a working paper prints
	for in, want := range map[string]string{"3852.004999": "3852", "3852.005": "3852.01", "-0.005": "-0.01", "12.5": "12.5", "0.004": "0"} {
		if d, _ := stated(in); d.rounded(2) != want {
			t.Errorf("%s at two decimals = %s, want %s", in, d.rounded(2), want)
		}
	}
}

func numbersRead(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
