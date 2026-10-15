package runner

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/xml"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"
	"unicode"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── PDF (AM2 Phase E) ────────────────────────────────────────────────────
//
// A PDF is the finished .docx converted by LibreOffice in the doc-converter
// service — never a second renderer (AM1b removed ReportLab's, whose Thai
// shaping was wrong). Every PDF passes two gates before it is delivered:
//
//	pdf.pages  every page rasterises and has ink on it (a blank or an
//	           all-dark page fails): "rasterise the pages and look at them";
//	pdf.thai   the PDF's text layer, in content order, holds every Thai
//	           paragraph of the .docx after normalisation — broken shaping
//	           shows up as reordered or split characters and fails.

const (
	minPageInk        = 0.0002 // a blank page is exactly 0
	maxPageInk        = 0.95   // an all-dark page is a failed render
	maxMarginInk      = 0.001  // AM21: the outer 0.3 in of a page is paper; ink there is text past its frame
	maxCheckedPages   = 50     // the converter renders at most this many (CHECK_MAX_PAGES)
	maxThaiParagraphs = 400
)

// converterDown is what a check says when the document converter did not
// answer; what it answered instead is kept on the run (technical).
const converterDown = "ตัวแปลงเอกสารของระบบไม่ตอบในตอนนี้"

// technical keeps an error that a quality check could only summarise: in the
// log and on the run, where the run page and a problem report show it.
func (rc *runCtx) technical(ctx context.Context, what string, err error) {
	if err == nil {
		return
	}
	rc.r.logger().Warn("quality check", "run", rc.run.ID, "check", what, "err", err)
	rc.r.noteStep(ctx, rc.run, "check:"+what, err.Error())
}

// gateCheck is one check of a file the runner makes itself (a PDF, a
// template's formulas), in the shape a package's own checks have. Its label
// and detail are read on the files card: texts for a person.
type gateCheck struct {
	Name   string        `json:"name"`
	OK     bool          `json:"ok"`
	Detail am.PersonText `json:"detail"`
	Label  am.PersonText `json:"label"`
}

// pdfFromDocx converts and checks; a converter that cannot be reached is a
// failed check (the PDF is not delivered), not a crash.
func (rc *runCtx) pdfFromDocx(ctx context.Context, name string, docx []byte) ([]byte, []gateCheck) {
	if !rc.r.Converter.Available() {
		return nil, []gateCheck{{Name: "pdf.convert", Label: am.Text("แปลงเป็น PDF"), Detail: am.Text("ยังไม่มีตัวแปลงเอกสารในระบบ")}}
	}
	pdf, err := rc.r.Converter.Convert(ctx, name, docx, "pdf")
	if err != nil {
		rc.technical(ctx, "pdf.convert", err)
		return nil, []gateCheck{{Name: "pdf.convert", Label: am.Text("แปลงเป็น PDF"), Detail: am.Text(converterDown)}}
	}
	chk, err := rc.r.Converter.Check(ctx, pdf)
	if err != nil {
		rc.technical(ctx, "pdf.check", err)
		return nil, []gateCheck{{Name: "pdf.check", Label: am.Text("ตรวจ PDF"), Detail: am.Text(converterDown)}}
	}
	return pdf, pdfGate(rc.docLang(ctx), docx, chk)
}

// deliverPDF converts the rendered .docx, gates the PDF and stores it as an
// artifact of artifactRun. why lists the failed checks (nothing is stored);
// err is only for a storage failure.
func (rc *runCtx) deliverPDF(ctx context.Context, artifactRun uuid.UUID, docxPath string, names map[string]string,
	versions map[string]int) (renderedArt, []string, error) {
	l := am.LangOf(ctx)
	if docxPath == "" {
		return renderedArt{}, []string{am.T(l, "pdf: แปลงเป็น PDF (agent ตัวนี้ไม่มีรายงาน .docx ให้แปลง)")}, nil
	}
	docx, err := os.ReadFile(docxPath)
	if err != nil {
		return renderedArt{}, nil, err
	}
	pdf, checks := rc.pdfFromDocx(ctx, names["docx"], docx)
	var why []string
	for _, c := range checks {
		if !c.OK {
			why = append(why, checkLine(l, "pdf", c.Label.In(l), c.Detail.In(l)))
		}
	}
	if pdf == nil || len(why) > 0 {
		return renderedArt{}, why, nil
	}
	name := names["pdf"]
	key := am.ArtifactKey(rc.run.TenantID, artifactRun, name)
	if err := rc.r.Files.Upload(ctx, key, bytes.NewReader(pdf), "application/pdf", int64(len(pdf))); err != nil {
		return renderedArt{}, nil, err
	}
	sum := sha256.Sum256(pdf)
	// the checks are read on the files card: kept in the run's language (their texts carry both)
	q, _ := am.MarshalIn(l, map[string]any{"passed": true, "checks": checks})
	return renderedArt{Name: name, Format: "pdf", Kind: "report", Key: key, Size: int64(len(pdf)),
		SHA256: hex.EncodeToString(sum[:]), Version: versions["pdf"], Quality: q}, nil, nil
}

// pdfGate checks a PDF against the report it was converted from. AM11: which
// text of the report must be in the PDF goes by the language the report is
// written in — l, the package's (runCtx.docLang), never the run's: a run
// started from an English screen still gets the Thai report of a package
// that speaks only Thai, and that report is checked exactly as before
// (pdf.thai: every Thai paragraph, and there must be one). A report a package
// wrote in English has no Thai paragraph of its own, so its check is the same
// promise in its own language (pdf.text: every paragraph of the report, in
// the order the report has them, and there must be one).
func pdfGate(l am.Lang, docx []byte, chk CheckResult) []gateCheck {
	want := chk.Pages
	if want > maxCheckedPages {
		want = maxCheckedPages
	}
	var bad []string
	for i, ink := range chk.PageInk {
		if ink < minPageInk || ink > maxPageInk {
			bad = append(bad, fmt.Sprint(i+1))
		}
	}
	pages := gateCheck{Name: "pdf.pages", Label: am.Text("ทุกหน้าแสดงผลได้"),
		OK:     chk.Pages > 0 && chk.RenderedPages == want && len(chk.PageInk) == want && len(bad) == 0,
		Detail: am.Text("%s หน้า แสดงผลได้ %s หน้า", am.Count(chk.Pages), am.Count(chk.RenderedPages))}
	if len(bad) > 0 {
		pages.Detail = am.Text("%s · หน้าว่างหรือผิดปกติ: %s", am.Words(pages.Detail), am.Name(strings.Join(bad, ", ")))
	}
	look := pdfLookChecks(chk)
	if l == am.English {
		return append([]gateCheck{pages, pdfTextCheck(docx, chk)}, look...)
	}
	paras := docxThaiParagraphs(docx)
	text := thaiKey(chk.RawText)
	var missing []string
	for _, p := range paras {
		if !strings.Contains(text, thaiKey(p)) {
			missing = append(missing, p)
		}
	}
	thai := gateCheck{Name: "pdf.thai", Label: am.Text("ภาษาไทยใน PDF ตรงกับรายงาน"), OK: len(paras) > 0 && len(missing) == 0,
		Detail: am.Text("ตรง %s จาก %s ย่อหน้า", am.Count(len(paras)-len(missing)), am.Count(len(paras)))}
	if len(missing) > 0 {
		thai.Detail = am.Text("%s · ไม่ตรง เช่น “%s”", am.Words(thai.Detail), am.Typed(missing[0], 60))
	}
	if len(paras) == 0 {
		thai.Detail = am.Text("ไม่พบข้อความภาษาไทยในรายงาน")
	}
	return append([]gateCheck{pages, thai}, look...)
}

// pdfLookChecks (AM21 §3.4): nothing clipped at a margin — the outer 0.3 in of every page is paper;
// every font embedded, so the file reads the same on a machine without Sarabun; the metadata carries
// the organisation's name, never a build marker or our own. A converter that did not measure the
// margins fails the check rather than passing it unmeasured.
func pdfLookChecks(chk CheckResult) []gateCheck {
	var inked []string
	for i, ink := range chk.MarginInk {
		if ink > maxMarginInk {
			inked = append(inked, fmt.Sprint(i+1))
		}
	}
	margins := gateCheck{Name: "pdf.margins", Label: am.Text("ไม่มีข้อความเลยขอบกระดาษ"),
		OK:     len(chk.MarginInk) > 0 && len(inked) == 0,
		Detail: am.Text("ขอบกระดาษทุกหน้า (%s หน้า) ว่าง", am.Count(len(chk.MarginInk)))}
	if len(chk.MarginInk) == 0 {
		margins.Detail = am.Text("ตัวแปลงเอกสารไม่ได้วัดขอบกระดาษ")
	} else if len(inked) > 0 {
		margins.Detail = am.Text("มีหมึกในขอบกระดาษของหน้า %s", am.Name(strings.Join(inked, ", ")))
	}
	var loose []string
	for _, f := range chk.Fonts {
		if !f.Embedded {
			loose = append(loose, f.Name)
		}
	}
	fonts := gateCheck{Name: "pdf.fonts", Label: am.Text("ฝังฟอนต์ทุกแบบในไฟล์"),
		OK:     len(chk.Fonts) > 0 && len(loose) == 0,
		Detail: am.Text("ฟอนต์ %s แบบ ฝังครบ", am.Count(len(chk.Fonts)))}
	if len(chk.Fonts) == 0 {
		fonts.Detail = am.Text("ไม่พบฟอนต์ในไฟล์")
	} else if len(loose) > 0 {
		fonts.Detail = am.Text("ไม่ได้ฝัง: %s", am.Name(strings.Join(loose, ", ")))
	}
	author := strings.TrimSpace(chk.Meta["author"])
	mark := am.MarkerIn(author + " " + chk.Meta["title"] + " " + chk.Meta["subject"])
	meta := gateCheck{Name: "pdf.meta", Label: am.Text("ข้อมูลกำกับไฟล์เป็นชื่อองค์กร ไม่มีรหัสงานหรือชื่อผู้ผลิตระบบ"),
		OK:     author != "" && mark == "",
		Detail: am.Text("ผู้จัดทำ: %s", am.Name(author))}
	if author == "" {
		meta.Detail = am.Text("ไฟล์ไม่มีชื่อผู้จัดทำ")
	} else if mark != "" {
		meta.Detail = am.Text("พบ “%s” ในข้อมูลกำกับไฟล์", am.Name(mark))
	}
	return []gateCheck{margins, fonts, meta}
}

// countsAsText: a paragraph long enough to be looked for in the PDF.
func countsAsText(s string) bool { return letterCount(s) >= 4 }

// pdfTextCheck is the text check of an English report (AM11): every paragraph
// of the .docx that has at least four letters must be in the PDF's text, and
// in the order the report has them — a paragraph found only before the one
// that precedes it is not where the report put it. Both sides are compared
// in textKey form. The customer's own Thai (an account's name, a company's)
// is in those paragraphs too, and is held to the same.
func pdfTextCheck(docx []byte, chk CheckResult) gateCheck {
	paras := docxParagraphs(docx, countsAsText)
	text := textKey(chk.RawText)
	var missing []string
	at := 0
	for _, p := range paras {
		key := textKey(p)
		i := strings.Index(text[at:], key)
		if i < 0 {
			missing = append(missing, p)
			continue
		}
		at += i + len(key)
	}
	c := gateCheck{Name: "pdf.text", Label: am.Text("ข้อความใน PDF ตรงกับรายงาน"), OK: len(paras) > 0 && len(missing) == 0,
		Detail: am.Text("ตรง %s จาก %s ย่อหน้า", am.Count(len(paras)-len(missing)), am.Count(len(paras)))}
	if len(missing) > 0 {
		c.Detail = am.Text("%s · ไม่ตรง เช่น “%s”", am.Words(c.Detail), am.Typed(missing[0], 60))
	}
	if len(paras) == 0 {
		c.Detail = am.Text("ไม่พบข้อความในรายงาน")
	}
	return c
}

// ligatures are the joined letters a PDF's text layer may give back for the
// plain letters a report was written with ("ﬁ" for "fi").
var ligatures = strings.NewReplacer("ﬀ", "ff", "ﬁ", "fi", "ﬂ", "fl", "ﬃ", "ffi", "ﬄ", "ffl")

// textKey is thaiKey for a text that may be English: joined letters apart.
func textKey(s string) string { return thaiKey(ligatures.Replace(s)) }

func letterCount(s string) int {
	n := 0
	for _, r := range s {
		if unicode.IsLetter(r) {
			n++
		}
	}
	return n
}

// docxThaiParagraphs is the text of every body paragraph of a .docx that
// carries Thai (at least four Thai characters), in order.
func docxThaiParagraphs(docx []byte) []string {
	return docxParagraphs(docx, func(s string) bool { return thaiCount(s) >= 4 })
}

// docxParagraphs is the text of every body paragraph of a .docx that counts,
// in order (at most maxThaiParagraphs of them).
func docxParagraphs(docx []byte, counts func(string) bool) []string {
	zr, err := zip.NewReader(bytes.NewReader(docx), int64(len(docx)))
	if err != nil {
		return nil
	}
	var body []byte
	for _, f := range zr.File {
		if f.Name == "word/document.xml" {
			rc, err := f.Open()
			if err != nil {
				return nil
			}
			body, _ = io.ReadAll(io.LimitReader(rc, 32<<20))
			rc.Close()
		}
	}
	const w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
	dec := xml.NewDecoder(bytes.NewReader(body))
	var out []string
	var stack []*strings.Builder
	inText := false
	for {
		tok, err := dec.Token()
		if err != nil {
			break
		}
		switch t := tok.(type) {
		case xml.StartElement:
			if t.Name.Space != w {
				continue
			}
			switch t.Name.Local {
			case "p":
				stack = append(stack, &strings.Builder{})
			case "t":
				inText = true
			case "tab", "br", "cr":
				if len(stack) > 0 {
					stack[len(stack)-1].WriteByte(' ')
				}
			}
		case xml.EndElement:
			if t.Name.Space != w {
				continue
			}
			switch t.Name.Local {
			case "t":
				inText = false
			case "p":
				if len(stack) == 0 {
					continue
				}
				s := strings.TrimSpace(stack[len(stack)-1].String())
				stack = stack[:len(stack)-1]
				if counts(s) && len(out) < maxThaiParagraphs {
					out = append(out, s)
				}
			}
		case xml.CharData:
			if inText && len(stack) > 0 {
				stack[len(stack)-1].Write(t)
			}
		}
	}
	return out
}

func thaiCount(s string) int {
	n := 0
	for _, r := range s {
		if r >= 0x0E00 && r <= 0x0E7F {
			n++
		}
	}
	return n
}

// sara am, which a PDF text layer can give back as nikhahit + sara aa (with a
// tone mark before or after the nikhahit).
var (
	saraAmSplit    = regexp.MustCompile(`\x{0E4D}([\x{0E48}-\x{0E4B}]?)\x{0E32}`)
	saraAm         = string(rune(0x0E33))
	saraAmToneLast = regexp.MustCompile(`([\x{0E48}-\x{0E4B}])\x{0E4D}\x{0E32}`)
)

// thaiKey is the form both sides are compared in: no whitespace or invisible
// characters (pdftotext spaces around shifted tone marks; our own explicit
// line breaks), sara am in one piece.
func thaiKey(s string) string {
	s = saraAmToneLast.ReplaceAllString(s, "${1}"+saraAm)
	s = saraAmSplit.ReplaceAllString(s, "${1}"+saraAm)
	var b strings.Builder
	for _, r := range s {
		if unicode.IsSpace(r) || r == 0x200B || r == 0x00AD || r == 0xFEFF || r == 0x2060 {
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

// docLang is the language this run's documents are written in: the run's own
// when the package says it speaks it (manifest "languages"), Thai otherwise.
// A package's tables, labels and fixed text are its own words; what the
// platform adds to a document — a model's commentary, a preparer's name, the
// check of the PDF's text — follows them, so a document is never half one
// language and half another. The thread around the run is the run's language.
func (rc *runCtx) docLang(ctx context.Context) am.Lang {
	if l := am.LangOf(ctx); rc.pkg.Manifest.Speaks(l) {
		return l
	}
	return am.Thai
}
