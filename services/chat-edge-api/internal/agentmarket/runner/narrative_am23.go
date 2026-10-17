package runner

import (
	"fmt"
	"regexp"
	"sort"
	"strings"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── What makes a commentary read machine-made (AM23 §3.3) ─────────────────
//
// Measured over AM22's 246 model commentaries (AM23_REPORT, the card's measurement of the same rule): ten points each where
// the rules write five; one point in eight only saying again what the round's facts, its figures' labels or the
// commentary's own summary already say; hedges with nothing to do in them ("ควรพิจารณาตรวจสอบเพิ่มเติม"); and the same
// headings on every document. The validator holds an answer to what a reader would:
//
//   - padding: a point of "movements" or "watch" that adds next to nothing to what is already said — in English at most
//     two words, in Thai (written without spaces between words) at most six character trigrams, filler aside — is sent
//     back with its place, once, the way a point too long is (AM22); the answer after the last attempt is used as it
//     is, never cut. A next step is something to do, never padding.
//   - hedges: a phrase of the package's narrative_hedges (its manifest, in both languages; the engine's gate reads the
//     same list in the finished files) is refused, the way a word the documents never say is.
//   - headings: each section's heading is written from what it found, within the catalogue's title limit; a section that
//     found something, under its generic heading or under none, is sent back once. (An engine that sends no heading
//     limit prints no headings of the model's, and is not asked for them.)

// paddingWords and paddingTrigrams: at or under, a point adds nothing (calibrated on AM22's run: AM23_REPORT §3.3).
const (
	paddingWords    = 2
	paddingTrigrams = 6
)

// narrativeParts are the commentary's parts, as the answer names them.
var narrativeParts = []string{"summary", "movements", "watch", "next_steps"}

// paddingFillerTH, paddingFillerEN: the words a sentence restating a figure is made of besides the figure's label.
var paddingFillerTH = []string{"ยอด", "เท่ากับ", "คือ", "อยู่ที่", "มี", "เป็น", "รวม", "จำนวน", "รายการ", "บาท", "งวดนี้", "ของ", "ที่",
	"และ", "ใน", "รอบนี้", "ทั้งหมด", "โดย", "ได้", "ทั้งสิ้น", "ฉบับ", "บรรทัด", "ไว้", "แล้ว", "ซึ่ง", "จาก", "ราย", "ข้อ"}

var paddingFillerEN = map[string]bool{}

func init() {
	for _, w := range strings.Fields("the a an of was is were are at in this period round total came to stood closed with and for amount " +
		"amounted reported recorded baht thb there items item lines line documents invoices claims on by its their all has have had " +
		"been which that these those it as") {
		paddingFillerEN[w] = true
	}
	sort.SliceStable(paddingFillerTH, func(i, j int) bool {
		return len([]rune(paddingFillerTH[i])) > len([]rune(paddingFillerTH[j]))
	})
}

var (
	paddingPunct = regexp.MustCompile(`[\s0-9\x{0E50}-\x{0E59}.,()%:;\-–—/·"'“”{}\[\]]+`)
	paddingWord  = regexp.MustCompile(`[a-z][a-z'\-]*`)
)

// thaiMark is a Thai vowel or tone mark written above or below its consonant (it takes no column, and a model writes
// the same word with and without one more often than a reader notices).
func thaiMark(r rune) bool {
	return r == 0x0E31 || (r >= 0x0E34 && r <= 0x0E3A) || (r >= 0x0E47 && r <= 0x0E4E)
}

func paddingCleanTH(s string) string {
	s = figureRe.ReplaceAllString(s, " ")
	for _, w := range paddingFillerTH {
		s = strings.ReplaceAll(s, w, " ")
	}
	return paddingPunct.ReplaceAllString(s, " ")
}

func trigrams(s string) map[string]bool {
	out := map[string]bool{}
	for _, chunk := range strings.Fields(s) {
		var rs []rune
		for _, r := range chunk {
			if !thaiMark(r) {
				rs = append(rs, r)
			}
		}
		for i := 0; i+3 <= len(rs); i++ {
			out[string(rs[i:i+3])] = true
		}
	}
	return out
}

// novelty is what a point says that the known text does not: in English its words (filler aside), in Thai its character
// trigrams.
func novelty(l am.Lang, point, known string) int {
	if l == am.English {
		kw := map[string]bool{}
		for _, w := range paddingWord.FindAllString(strings.ToLower(figureRe.ReplaceAllString(known, " ")), -1) {
			kw[w] = true
		}
		n := 0
		for _, w := range paddingWord.FindAllString(strings.ToLower(figureRe.ReplaceAllString(point, " ")), -1) {
			if !kw[w] && !paddingFillerEN[w] {
				n++
			}
		}
		return n
	}
	k := trigrams(paddingCleanTH(known))
	n := 0
	for g := range trigrams(paddingCleanTH(point)) {
		if !k[g] {
			n++
		}
	}
	return n
}

// knownToTheReader is what a commentary's reader already has before its points: the round's facts, the labels of its
// figures and the commentary's own summary.
func knownToTheReader(cv computedView, summary string) string {
	var b strings.Builder
	for _, f := range cv.NarrativeFacts {
		b.WriteString(f.Heading + "\n")
		for _, line := range f.Lines {
			b.WriteString(line + "\n")
		}
	}
	for _, f := range cv.Figures {
		b.WriteString(f.Label + "\n")
	}
	b.WriteString(summary)
	return b.String()
}

// isPadding: a point of the round's findings or of what to watch that adds next to nothing to what is known.
func isPadding(l am.Lang, part, point, known string) bool {
	if part == "next_steps" {
		return false
	}
	limit := paddingTrigrams
	if l == am.English {
		limit = paddingWords
	}
	return novelty(l, point, known) <= limit
}

// paddingReason is the reason a point that only restates is sent back, in the run's language.
func paddingReason(l am.Lang, part string, i int) string {
	return inLang(l, fmt.Sprintf("ข้อ %d ของ %s แค่ทวนสิ่งที่ข้อเท็จจริง ตาราง หรือ summary บอกไว้แล้ว: ให้บอกว่ามันหมายถึงอะไรสำหรับผู้อ่าน (การเปรียบเทียบ ความเสี่ยง หรือสิ่งที่ต้องทำ) หรือตัดข้อนั้นออก", i, part),
		fmt.Sprintf("point %d of %s only restates what the facts, the table or the summary already say: say what it means for the reader (a comparison, a risk or what to do), or leave it out", i, part))
}

// hedgeReason is the reason an answer with a hedge is sent back, in the run's language.
func hedgeReason(l am.Lang, w string) string {
	return inLang(l, fmt.Sprintf("“%s” ไม่ใช่สิ่งที่ผู้อ่านทำได้: ให้บอกว่าตรวจอะไร เทียบกับเอกสารอะไร หรือขออะไรจากใคร", w),
		fmt.Sprintf("“%s” is not something a reader can do: say what to check, against which document, or what to ask of whom", w))
}

// headingReason is the reason a section is sent back for its heading, in the run's language.
func headingReason(l am.Lang, part string) string {
	return inLang(l, fmt.Sprintf("หัวข้อของ %s ให้เขียนจากสิ่งที่ส่วนนั้นพบ (ใส่ใน headings.%s) ไม่ใช่ชื่อส่วนทั่วไป", part, part),
		fmt.Sprintf("write the heading of %s from what that part found (in headings.%s), not the part's generic name", part, part))
}

// headingTooLong is the reason a heading too long for the catalogue's title is sent back.
func headingTooLong(l am.Lang, part string, got, max int) string {
	return inLang(l, fmt.Sprintf("หัวข้อของ %s ยาว %d ตัวอักษร เกิน %d", part, got, max),
		fmt.Sprintf("the heading of %s is %d characters, over %d", part, got, max))
}

// squashed: a heading compared with the generic one, whatever its spacing and case.
func squashed(s string) string { return strings.ToLower(strings.Join(strings.Fields(s), "")) }

// checkHeadings reads an answer's headings (AM23 §3.3): what it returns is kept in the answer; the soft reasons are why
// it is asked once more (a heading too long, a part that found something under a generic heading or none).
func checkHeadings(l am.Lang, v map[string]any, cv computedView) (map[string]string, []string, error) {
	hs := map[string]string{}
	if v["headings"] != nil {
		m, ok := v["headings"].(map[string]any)
		if !ok {
			return nil, nil, fmt.Errorf("%s", notAList(l, "headings"))
		}
		for _, k := range narrativeParts {
			x, ok := m[k]
			if !ok || x == nil {
				continue
			}
			s, ok := x.(string)
			if !ok {
				return nil, nil, fmt.Errorf("%s", notASentence(l, "headings."+k, 1))
			}
			if strings.TrimSpace(s) != "" {
				hs[k] = strings.TrimSpace(s)
			}
		}
	}
	var soft []string
	lim := cv.WritingLimits["heading_chars"]
	if lim <= 0 || len(cv.HeadingsGeneric) == 0 {
		return hs, nil, nil // (an engine before AM23, or a package whose documents print no heading of the commentary's)
	}
	for _, k := range narrativeParts {
		if h, ok := hs[k]; ok && columns(h) > lim {
			soft = append(soft, headingTooLong(l, k, columns(h), lim))
		}
		found := false
		switch x := v[k].(type) {
		case string:
			found = strings.TrimSpace(x) != ""
		case []any:
			found = len(x) > 0
		}
		if found && (hs[k] == "" || squashed(hs[k]) == squashed(cv.HeadingsGeneric[k])) {
			soft = append(soft, headingReason(l, k))
		}
	}
	return hs, soft, nil
}
