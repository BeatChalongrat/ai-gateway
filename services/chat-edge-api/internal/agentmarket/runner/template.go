package runner

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strings"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── The customer's own template (AM2 Phase E) ────────────────────────────
//
// inspectTemplate reads an uploaded template in the sandbox (the mapping
// screen's data: slots, a first mapping, the diff against the saved
// version); templateInput hands the saved template to the render step,
// which fills it with the run's values; an .xlsx comes back needing
// recalculation and the doc-converter recalculates it (openpyxl writes no
// computed values) before it is delivered.

func (r *Runner) inspectTemplate(ctx context.Context, rc *runCtx, job am.Run, f am.File) error {
	defs, _ := r.Store.ListDefaults(ctx, job.InstallID)
	saved, hasSaved := am.SavedTemplate(defs)
	var savedArg any
	if hasSaved {
		savedArg = saved
	}
	key := am.StepKey(job.ID, "template", f.SHA256, savedArg, rc.pkg.Checksum)
	_, err := rc.step(ctx, "template:"+f.Name, "ingest", key, map[string]any{"file": f.Name}, false,
		func(ctx context.Context) (any, float64, string, string, error) {
			path, cleanup, err := r.Files.OpenLocalCopy(ctx, f.StorageKey)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			input := "template" + filepath.Ext(f.Name)
			res, err := rc.exec(ctx, map[string]any{"op": "inspect_template", "package": rc.pkg.PyPackage,
				"file": map[string]any{"name": f.Name, "input": input}, "saved": savedArg,
				"spec": json.RawMessage(rc.pkg.Manifest.Templates)}, []Input{{Name: input, LocalPath: path}})
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			return res.Result, rc.sandboxCredits(res.Elapsed), "deliverables/template_out.py", "", nil
		})
	if err != nil {
		return r.fail(ctx, job, "template", err)
	}
	return r.finish(rc, am.RunSucceeded, "")
}

// templateInput is the saved template as a render input, or nil.
func (rc *runCtx) templateInput(ctx context.Context) (*am.TemplateDefault, *am.File, error) {
	if len(rc.pkg.Manifest.Templates) == 0 {
		return nil, nil, nil
	}
	defs, err := rc.r.Store.ListDefaults(ctx, rc.install.ID)
	if err != nil {
		return nil, nil, err
	}
	t, ok := am.SavedTemplate(defs)
	if !ok {
		return nil, nil, nil
	}
	f, err := rc.r.Store.GetFile(ctx, rc.run.TenantID, rc.run.UserID, t.FileID)
	if err != nil || f.Status == "removed" {
		return nil, nil, nil // the file is gone: the run delivers without it
	}
	return &t, &f, nil
}

// recalcTemplate has the converter recalculate a filled .xlsx template in
// place (openpyxl writes formulas but never their values) and adds the
// outcome to the artifact's checks. Without a converter, or when it fails,
// the file is delivered as filled — it already asks Excel to recalculate
// every formula when opened — and the check says so.
func (rc *runCtx) recalcTemplate(ctx context.Context, path, name string, meta, quality json.RawMessage) (json.RawMessage, error) {
	var m struct {
		Ext         string `json:"ext"`
		NeedsRecalc bool   `json:"needs_recalc"`
	}
	_ = json.Unmarshal(meta, &m)
	if m.Ext != "xlsx" || !m.NeedsRecalc {
		return quality, nil
	}
	var q map[string]any
	if err := json.Unmarshal(quality, &q); err != nil {
		return quality, err
	}
	check := map[string]any{"name": "template.recalculated", "ok": true, "label": "คำนวณสูตรในแบบฟอร์มใหม่"}
	if !rc.r.Converter.Available() {
		check["detail"] = "Excel จะคำนวณสูตรใหม่เมื่อเปิดไฟล์ (ยังไม่มีตัวแปลงเอกสาร)"
	} else if data, err := os.ReadFile(path); err != nil {
		return quality, err
	} else if out, err := rc.r.Converter.Convert(ctx, filepath.Base(name), data, "xlsx"); err != nil {
		rc.technical(ctx, "template.recalculate", err)
		check["detail"] = "คำนวณล่วงหน้าไม่สำเร็จ Excel จะคำนวณสูตรเองเมื่อเปิดไฟล์"
	} else if merged, n, bad, err := mergeCachedValues(data, out); err != nil {
		rc.technical(ctx, "template.merge", err)
		check["detail"] = "อ่านผลคำนวณล่วงหน้าไม่ได้ Excel จะคำนวณสูตรเองเมื่อเปิดไฟล์"
	} else if err := os.WriteFile(path, merged, 0o644); err != nil {
		return quality, err
	} else if len(bad) > 0 {
		check["ok"] = false
		check["detail"] = "สูตรให้ค่าผิดพลาด: " + strings.Join(bad, ", ")
	} else {
		check["detail"] = fmt.Sprintf("คำนวณสูตรใหม่ด้วย LibreOffice แล้ว %d สูตร", n)
	}
	if ok, _ := check["ok"].(bool); !ok {
		q["passed"] = false
	}
	checks, _ := q["checks"].([]any)
	q["checks"] = append(checks, check)
	return json.Marshal(q)
}

func fileSHA(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	h := sha256.New()
	_, _ = io.Copy(h, f)
	return hex.EncodeToString(h.Sum(nil))
}

// mergeCachedValues copies the computed value of every formula cell from
// LibreOffice's recalculated copy into our own filled workbook, which is
// otherwise delivered byte for byte as filled: the customer's formatting
// never goes through LibreOffice's export. Sheets are matched by name.
// Returns the merged file, the number of formula values written, and the
// cells whose formula gave an error (#REF!, #NAME?, …).
func mergeCachedValues(filled, recalculated []byte) ([]byte, int, []string, error) {
	src, err := zip.NewReader(bytes.NewReader(filled), int64(len(filled)))
	if err != nil {
		return nil, 0, nil, err
	}
	lo, err := zip.NewReader(bytes.NewReader(recalculated), int64(len(recalculated)))
	if err != nil {
		return nil, 0, nil, err
	}
	ours, err := sheetParts(src)
	if err != nil {
		return nil, 0, nil, err
	}
	theirs, err := sheetParts(lo)
	if err != nil {
		return nil, 0, nil, err
	}
	values := map[string]map[string]cellValue{} // our part → cell → value
	for name, part := range ours {
		lp, ok := theirs[name]
		if !ok {
			continue
		}
		body, err := readZip(lo, lp)
		if err != nil {
			return nil, 0, nil, err
		}
		values[part] = formulaValues(body)
	}
	var out bytes.Buffer
	zw := zip.NewWriter(&out)
	n := 0
	var bad []string
	for _, f := range src.File {
		body, err := readZip(src, f.Name)
		if err != nil {
			return nil, 0, nil, err
		}
		if vals, ok := values[f.Name]; ok {
			var k int
			var errs []string
			body, k, errs = setFormulaValues(body, vals)
			n += k
			bad = append(bad, errs...)
		}
		w, err := zw.CreateHeader(&zip.FileHeader{Name: f.Name, Method: f.Method, Modified: f.Modified})
		if err != nil {
			return nil, 0, nil, err
		}
		if _, err := w.Write(body); err != nil {
			return nil, 0, nil, err
		}
	}
	if err := zw.Close(); err != nil {
		return nil, 0, nil, err
	}
	return out.Bytes(), n, bad, nil
}

type cellValue struct {
	T string // "", "str", "b", "e"
	V string
}

var (
	sheetRe    = regexp.MustCompile(`<sheet\b[^>]*\bname="([^"]*)"[^>]*\br:id="([^"]*)"`)
	relRe      = regexp.MustCompile(`<Relationship\b[^>]*\bId="([^"]*)"[^>]*\bTarget="([^"]*)"`)
	relRe2     = regexp.MustCompile(`<Relationship\b[^>]*\bTarget="([^"]*)"[^>]*\bId="([^"]*)"`)
	cellRe     = regexp.MustCompile(`(?s)<c\b([^>]*?)(/>|>(.*?)</c>)`)
	refRe      = regexp.MustCompile(`\br="([A-Z]+[0-9]+)"`)
	typeRe     = regexp.MustCompile(`\bt="([^"]*)"`)
	valueRe    = regexp.MustCompile(`(?s)<v>(.*?)</v>`)
	valueTagRe = regexp.MustCompile(`(?s)<v>.*?</v>|<v/>`)
)

// sheetParts maps each sheet's name to its part in the package.
func sheetParts(z *zip.Reader) (map[string]string, error) {
	wb, err := readZip(z, "xl/workbook.xml")
	if err != nil {
		return nil, err
	}
	rels, err := readZip(z, "xl/_rels/workbook.xml.rels")
	if err != nil {
		return nil, err
	}
	target := map[string]string{}
	for _, m := range relRe.FindAllSubmatch(rels, -1) {
		target[string(m[1])] = string(m[2])
	}
	for _, m := range relRe2.FindAllSubmatch(rels, -1) {
		if _, ok := target[string(m[2])]; !ok {
			target[string(m[2])] = string(m[1])
		}
	}
	out := map[string]string{}
	for _, m := range sheetRe.FindAllSubmatch(wb, -1) {
		t := target[string(m[2])]
		if t == "" {
			continue
		}
		if strings.HasPrefix(t, "/") {
			t = strings.TrimPrefix(t, "/")
		} else {
			t = path.Join("xl", t)
		}
		out[xmlUnescape(string(m[1]))] = t
	}
	return out, nil
}

func xmlUnescape(s string) string {
	return strings.NewReplacer("&amp;", "&", "&lt;", "<", "&gt;", ">", "&quot;", `"`, "&apos;", "'").Replace(s)
}

func readZip(z *zip.Reader, name string) ([]byte, error) {
	for _, f := range z.File {
		if f.Name == name {
			rc, err := f.Open()
			if err != nil {
				return nil, err
			}
			defer rc.Close()
			return io.ReadAll(io.LimitReader(rc, 64<<20))
		}
	}
	return nil, fmt.Errorf("%s not in the workbook", name)
}

// formulaValues reads the value LibreOffice computed for every formula cell.
func formulaValues(sheet []byte) map[string]cellValue {
	out := map[string]cellValue{}
	for _, m := range cellRe.FindAllSubmatch(sheet, -1) {
		inner := m[3]
		if !bytes.Contains(inner, []byte("<f")) {
			continue
		}
		ref := refRe.FindSubmatch(m[1])
		v := valueRe.FindSubmatch(inner)
		if ref == nil || v == nil {
			continue
		}
		t := ""
		if tm := typeRe.FindSubmatch(m[1]); tm != nil {
			t = string(tm[1])
		}
		out[string(ref[1])] = cellValue{T: t, V: string(v[1])}
	}
	return out
}

// setFormulaValues writes those values into our own formula cells.
func setFormulaValues(sheet []byte, vals map[string]cellValue) ([]byte, int, []string) {
	n := 0
	var bad []string
	res := cellRe.ReplaceAllFunc(sheet, func(c []byte) []byte {
		m := cellRe.FindSubmatch(c)
		inner := m[3]
		if !bytes.Contains(inner, []byte("<f")) {
			return c
		}
		ref := refRe.FindSubmatch(m[1])
		if ref == nil {
			return c
		}
		v, ok := vals[string(ref[1])]
		if !ok {
			return c
		}
		if v.T == "e" {
			bad = append(bad, string(ref[1])+"="+v.V)
		}
		attrs := typeRe.ReplaceAll(m[1], nil)
		if v.T != "" {
			attrs = append(bytes.TrimRight(attrs, " "), []byte(` t="`+v.T+`"`)...)
		}
		inner = valueTagRe.ReplaceAll(inner, nil)
		inner = append(inner, []byte("<v>"+v.V+"</v>")...)
		n++
		return []byte("<c" + string(attrs) + ">" + string(inner) + "</c>")
	})
	return res, n, bad
}
