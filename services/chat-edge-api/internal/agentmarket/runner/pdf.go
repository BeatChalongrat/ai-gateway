package runner

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
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
	return pdf, pdfGate(docx, chk)
}

// deliverPDF converts the rendered .docx, gates the PDF and stores it as an
// artifact of artifactRun. why lists the failed checks (nothing is stored);
// err is only for a storage failure.
func (rc *runCtx) deliverPDF(ctx context.Context, artifactRun uuid.UUID, docxPath string, names map[string]string,
	versions map[string]int) (renderedArt, []string, error) {
	if docxPath == "" {
		return renderedArt{}, []string{"pdf: แปลงเป็น PDF (agent ตัวนี้ไม่มีรายงาน .docx ให้แปลง)"}, nil
	}
	docx, err := os.ReadFile(docxPath)
	if err != nil {
		return renderedArt{}, nil, err
	}
	pdf, checks := rc.pdfFromDocx(ctx, names["docx"], docx)
	var why []string
	for _, c := range checks {
		if !c.OK {
			why = append(why, checkLine("pdf", c.Label.String(), c.Detail.String()))
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
	q, _ := json.Marshal(map[string]any{"passed": true, "checks": checks})
	return renderedArt{Name: name, Format: "pdf", Kind: "report", Key: key, Size: int64(len(pdf)),
		SHA256: hex.EncodeToString(sum[:]), Version: versions["pdf"], Quality: q}, nil, nil
}

func pdfGate(docx []byte, chk CheckResult) []gateCheck {
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
	return []gateCheck{pages, thai}
}

// docxThaiParagraphs is the text of every body paragraph of a .docx that
// carries Thai (at least four Thai characters), in order.
func docxThaiParagraphs(docx []byte) []string {
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
				if thaiCount(s) >= 4 && len(out) < maxThaiParagraphs {
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
