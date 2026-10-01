package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── The print proof (AM9) ────────────────────────────────────────────────
//
// A working paper is read on paper. The package sets its page setup from the
// sheet's shape and its own gate checks those settings; this is the proof
// that does not trust settings: the finished workbook is printed — converted
// to PDF by LibreOffice in the doc-converter service, the same renderer the
// report's PDF comes from — and every row the package lists (its identity:
// number and description; its amounts as printed) must be found together on
// one rendered page. A description on page 5 and its amount on page 6 fails.

// printRow is one row the package says carries an amount (workpaper.print_rows).
type printRow struct {
	Sheet    string   `json:"sheet"`
	Row      int      `json:"row"`
	Identity []string `json:"identity"`
	Amounts  []string `json:"amounts"`
}

// identityKeyRunes is how much of an identity text is looked for: enough to
// name the row, short of where a long description may be cut by its cell.
const identityKeyRunes = 24

// printProof prints the workbook and looks for every row on one page. No
// converter: no check (the package's own setup check still ran); a converter
// that does not answer: a failed check, like the PDF's.
func (rc *runCtx) printProof(ctx context.Context, name, path string, rows []printRow) (*gateCheck, error) {
	if len(rows) == 0 || rc.r.Converter == nil || !rc.r.Converter.Available() {
		return nil, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	label := am.Text("พิมพ์จริงแล้วตรวจ: รายการกับจำนวนเงินอยู่หน้าเดียวกัน")
	pdf, err := rc.r.Converter.Convert(ctx, name, data, "pdf")
	if err != nil {
		rc.technical(ctx, "xlsx.printed", err)
		return &gateCheck{Name: "xlsx.printed", Label: label, Detail: am.Text(converterDown)}, nil
	}
	chk, err := rc.r.Converter.Check(ctx, pdf)
	if err != nil {
		rc.technical(ctx, "xlsx.printed", err)
		return &gateCheck{Name: "xlsx.printed", Label: label, Detail: am.Text(converterDown)}, nil
	}
	c := checkPrinted(chk, rows)
	c.Label = label
	return &c, nil
}

// checkPrinted is the proof itself, on what the converter read back: the
// PDF's text layer page by page (pdftotext -raw separates pages with a form
// feed), compared in thaiKey form.
func checkPrinted(chk CheckResult, rows []printRow) gateCheck {
	pages := strings.Split(chk.RawText, "\f")
	keys := make([]string, len(pages))
	for i, p := range pages {
		keys[i] = thaiKey(p)
	}
	var bad []string
	for _, r := range rows {
		var needles []string
		for _, t := range r.Identity {
			k := []rune(thaiKey(t))
			if len(k) > identityKeyRunes {
				k = k[:identityKeyRunes]
			}
			if len(k) > 0 {
				needles = append(needles, string(k))
			}
		}
		for _, a := range r.Amounts {
			needles = append(needles, thaiKey(a))
		}
		found := false
		for _, page := range keys {
			all := true
			for _, n := range needles {
				if !strings.Contains(page, n) {
					all = false
					break
				}
			}
			if all {
				found = true
				break
			}
		}
		if !found {
			bad = append(bad, fmt.Sprintf("%s แถว %d (%s)", r.Sheet, r.Row, strings.Join(r.Identity, " · ")))
		}
	}
	out := gateCheck{Name: "xlsx.printed", OK: len(bad) == 0 && chk.Pages > 0,
		Detail: am.Text("พิมพ์ %s หน้า · %s แถวที่มีจำนวนเงิน รายการกับจำนวนเงินอยู่หน้าเดียวกันทุกแถว",
			am.Count(chk.Pages), am.Count(len(rows)))}
	if len(bad) > 0 {
		more := ""
		if len(bad) > 3 {
			more = fmt.Sprintf(" และอีก %d แถว", len(bad)-3)
			bad = bad[:3]
		}
		out.Detail = am.Text("พิมพ์ %s หน้า · รายการกับจำนวนเงินไม่อยู่หน้าเดียวกัน: %s%s",
			am.Count(chk.Pages), am.Typed(strings.Join(bad, "; "), 300), am.Name(more))
	}
	return out
}

// withCheck adds a runner-made check to a file's quality (and fails it when the check failed).
func withCheck(quality json.RawMessage, c gateCheck) json.RawMessage {
	var q map[string]any
	if json.Unmarshal(quality, &q) != nil || q == nil {
		q = map[string]any{"passed": true}
	}
	checks, _ := q["checks"].([]any)
	q["checks"] = append(checks, c)
	if !c.OK {
		q["passed"] = false
	}
	return mustJSON(q)
}
