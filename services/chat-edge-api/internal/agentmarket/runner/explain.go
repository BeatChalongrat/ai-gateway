package runner

import (
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// ── Why is this figure what it is (AM4) ──────────────────────────────────
//
// A model never produces a figure; it must never explain one either. In the
// AM3 walkthrough the register showed 0.00 and a model gave a confident,
// wrong reason — worse than silence, because it stops a person looking.
// So an answer about a figure is built from the round's own provenance
// (computed.json "citations": the figure's label, its formula in Thai, the
// parts it adds up — file, rows or document, each with its own value — and
// what was left out, with the reason), word for word, by this code. When no
// record matches the question and it asks why, the agent says it does not
// know and where to look.

type citeSource struct {
	File  string  `json:"file"`
	Sheet string  `json:"sheet"`
	Cells string  `json:"cells"`
	Rows  int     `json:"rows"`
	Row   int     `json:"row"`
	Value *string `json:"value"`
	Note  string  `json:"note"`
}

type citeLeftOut struct {
	File   string `json:"file"`
	Reason string `json:"reason"`
}

type citation struct {
	ID          string        `json:"id"`
	Label       string        `json:"label"`
	Value       *string       `json:"value"`
	Unit        string        `json:"unit"`
	Formula     string        `json:"formula"`
	Sources     []citeSource  `json:"sources"`
	DerivedFrom []string      `json:"derived_from"`
	LeftOut     []citeLeftOut `json:"left_out"`
	Aliases     []string      `json:"aliases"`
}

type excludedRows struct {
	Code   string `json:"code"`
	Reason string `json:"reason"`
	Name   string `json:"name"`
}

// provenance is the part of computed.json an explanation is built from.
type provenance struct {
	Citations []citation     `json:"citations"`
	Excluded  []excludedRows `json:"excluded"`
}

func parseProvenance(raw []byte) provenance {
	var p provenance
	_ = json.Unmarshal(raw, &p)
	return p
}

var whyWords = []string{"ทำไม", "เพราะอะไร", "สาเหตุ", "เหตุใด", "ไม่ใช่เหรอ", "ไม่ใช่หรือ", "ถึงเป็น", "ถึงได้", "มาจากไหน",
	"มาจากอะไร", "คิดยังไง", "คิดอย่างไร", "คำนวณยังไง", "คำนวณอย่างไร", "ที่มา", "อธิบาย", "why", "how come", "explain"}

// asksWhy: the question asks for a reason or an origin, not a value.
func asksWhy(text string) bool {
	t := strings.ToLower(text)
	for _, w := range whyWords {
		if strings.Contains(t, w) {
			return true
		}
	}
	return false
}

var numberInText = regexp.MustCompile(`-?\d[\d,]*(?:\.\d+)?`)

func squash(s string) string {
	return strings.ToLower(strings.Join(strings.Fields(s), ""))
}

var parenRe = regexp.MustCompile(`\s*\([^)]*\)`)

// labelKeys are the forms of a figure's name a question may use.
func (c citation) labelKeys() []string {
	core := strings.TrimSpace(parenRe.ReplaceAllString(c.Label, ""))
	core = strings.TrimSuffix(core, " งวดนี้")
	keys := []string{squash(c.Label), squash(core)}
	for _, a := range c.Aliases {
		keys = append(keys, squash(a))
	}
	return keys
}

// score is how strongly a question names this figure: the longest of its
// names (or of their first five-plus characters) found in the question.
func (c citation) score(q string) int {
	best := 0
	for _, k := range c.labelKeys() {
		if k == "" {
			continue
		}
		if strings.Contains(q, k) {
			if n := utf8.RuneCountInString(k); n > best {
				best = n
			}
			continue
		}
		// a question names "รายได้" for "รายได้จากการขายและบริการ": the
		// longest leading part of the name (5+ characters) it contains
		r := []rune(k)
		for n := len(r) - 1; n >= 5; n-- {
			if strings.Contains(q, string(r[:n])) {
				if n > best {
					best = n
				}
				break
			}
		}
	}
	return best
}

func decimalOf(s string) (float64, bool) {
	v, err := strconv.ParseFloat(strings.ReplaceAll(s, ",", ""), 64)
	return v, err == nil
}

// valueNamed: the question quotes this figure's value (never 0: too many figures are 0).
func (c citation) valueNamed(text string) bool {
	if c.Value == nil {
		return false
	}
	want, ok := decimalOf(*c.Value)
	if !ok || want == 0 {
		return false
	}
	for _, m := range numberInText.FindAllString(text, -1) {
		if v, ok := decimalOf(m); ok && math.Abs(v-want) < 0.005 {
			return true
		}
	}
	return false
}

// matchFigures finds the figures a question is about (at most two: the one it
// names, and one whose value it quotes).
func matchFigures(text string, p provenance) []citation {
	q := squash(text)
	type cand struct {
		c       citation
		score   int
		derived bool
		order   int
	}
	var named []cand
	for i, c := range p.Citations {
		if s := c.score(q); s >= 4 {
			named = append(named, cand{c, s, len(c.DerivedFrom) > 0, i})
		}
	}
	sort.SliceStable(named, func(i, j int) bool {
		if named[i].score != named[j].score {
			return named[i].score > named[j].score
		}
		if named[i].derived != named[j].derived {
			return named[i].derived // a report line before the account it is made of
		}
		return named[i].order < named[j].order
	})
	var out []citation
	if len(named) > 0 {
		out = append(out, named[0].c)
	}
	for _, c := range p.Citations {
		if len(out) >= 2 {
			break
		}
		if c.valueNamed(text) && (len(out) == 0 || out[0].ID != c.ID) {
			out = append(out, c)
		}
	}
	return out
}

func money(s string) string {
	v, ok := decimalOf(s)
	if !ok {
		return s
	}
	neg := v < 0
	v = math.Abs(v)
	whole := int64(v)
	frac := int64(math.Round((v - float64(whole)) * 100))
	if frac == 100 {
		whole, frac = whole+1, 0
	}
	ds := strconv.FormatInt(whole, 10)
	var b strings.Builder
	for i, ch := range ds {
		if i > 0 && (len(ds)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(ch)
	}
	out := fmt.Sprintf("%s.%02d", b.String(), frac)
	if neg {
		out = "−" + out
	}
	return out
}

func (c citation) valueText() string {
	if c.Value == nil {
		return "ไม่มีค่า"
	}
	switch c.Unit {
	case "pct":
		return money(*c.Value) + "%"
	case "count":
		return *c.Value + " ฉบับ"
	}
	return money(*c.Value) + " บาท"
}

// explainFigure is the answer, written only from the figure's record.
func explainFigure(c citation, p provenance) string {
	var b strings.Builder
	fmt.Fprintf(&b, "**%s = %s**\n\n", strings.TrimSpace(c.Label), c.valueText())
	if c.Formula != "" {
		fmt.Fprintf(&b, "วิธีคิด: %s\n\n", c.Formula)
	}
	if len(c.Sources) > 0 {
		b.WriteString("มาจาก:\n")
		shown := 0
		for _, s := range c.Sources {
			if shown == 8 {
				fmt.Fprintf(&b, "- และอีก %d รายการ (ดูทั้งหมดในหน้า “ที่มาของตัวเลข”)\n", len(c.Sources)-shown)
				break
			}
			shown++
			switch {
			case s.Rows > 0:
				line := fmt.Sprintf("- ไฟล์ “%s”", s.File)
				if s.Sheet != "" {
					line += " ชีต " + s.Sheet
				}
				line += fmt.Sprintf(" %d แถว", s.Rows)
				if s.Cells != "" && utf8.RuneCountInString(s.Cells) <= 60 {
					line += " (" + s.Cells + ")"
				}
				b.WriteString(line + "\n")
			default:
				line := "- “" + s.File + "”"
				if s.Row > 0 {
					line += fmt.Sprintf(" แถว %d", s.Row)
				}
				if s.Value != nil {
					line += ": " + money(*s.Value) + " บาท"
				}
				if s.Note != "" {
					line += " — " + s.Note
				}
				b.WriteString(line + "\n")
			}
		}
		b.WriteString("\n")
	} else if len(c.DerivedFrom) > 0 {
		b.WriteString("ประกอบจาก:\n")
		byID := map[string]citation{}
		for _, x := range p.Citations {
			byID[x.ID] = x
		}
		for i, id := range c.DerivedFrom {
			if i == 8 {
				fmt.Fprintf(&b, "- และอีก %d รายการ\n", len(c.DerivedFrom)-i)
				break
			}
			if x, ok := byID[id]; ok {
				fmt.Fprintf(&b, "- %s = %s\n", strings.TrimSpace(x.Label), x.valueText())
			}
		}
		b.WriteString("\n")
	} else if c.Value != nil {
		if v, ok := decimalOf(*c.Value); ok && v == 0 {
			b.WriteString("ไม่มีรายการใดในรอบนี้ที่นับเข้าตัวเลขนี้\n\n")
		}
	}
	left := c.LeftOut
	for _, e := range p.Excluded {
		if len(c.LeftOut) == 0 && e.Reason != "" {
			left = append(left, citeLeftOut{File: e.Name, Reason: e.Reason})
		}
	}
	if len(left) > 0 {
		b.WriteString("ไม่ได้นับ:\n")
		for i, l := range left {
			if i == 5 {
				fmt.Fprintf(&b, "- และอีก %d รายการ\n", len(left)-i)
				break
			}
			if l.File != "" {
				fmt.Fprintf(&b, "- “%s” — %s\n", l.File, l.Reason)
			} else {
				fmt.Fprintf(&b, "- %s\n", l.Reason)
			}
		}
		b.WriteString("\n")
	}
	return strings.TrimSpace(b.String())
}

const explainFooter = "คำตอบนี้สร้างจากบันทึกที่มาของตัวเลขในรอบนี้ ไม่ได้มาจากโมเดล — ดูทุกแถวได้ในหน้า “ที่มาของตัวเลข”"

// dontKnow is the answer to a "why" the record cannot support.
const dontKnow = "ผมไม่ทราบว่าทำไมตัวเลขนี้จึงเป็นเช่นนั้น — บันทึกที่มาของรอบนี้ไม่มีข้อมูลพอจะตอบ และผมจะไม่เดาเหตุผลให้ " +
	"ตรวจได้ในหน้า “ที่มาของตัวเลข” (ทุกตัวเลขบอกว่ามาจากไฟล์และแถวไหน) หรือกด “แจ้งปัญหา” ให้ผู้ดูแลช่วยดู"

// explainAnswer is the answer to a question about the round's figures, or ""
// when it is not about a figure the record knows and does not ask why (then
// a model may answer it, under the number guard).
func explainAnswer(text string, computed []byte) string {
	p := parseProvenance(computed)
	figs := matchFigures(text, p)
	if len(figs) == 0 {
		if asksWhy(text) {
			return dontKnow
		}
		return ""
	}
	parts := make([]string, 0, len(figs)+1)
	for _, c := range figs {
		parts = append(parts, explainFigure(c, p))
	}
	parts = append(parts, explainFooter)
	return strings.Join(parts, "\n\n")
}

// causal words a model's answer may not use: it has no record of any cause.
var causalWords = []string{"เพราะ", "เนื่องจาก", "สาเหตุ", "ส่งผลให้", "เป็นผลจาก", "because", "due to", "caused by"}

func givesACause(s string) bool {
	t := strings.ToLower(s)
	for _, w := range causalWords {
		if strings.Contains(t, w) {
			return true
		}
	}
	return false
}
