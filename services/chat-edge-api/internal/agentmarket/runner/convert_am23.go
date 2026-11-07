package runner

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── .xls and .doc come in; they never go out (AM23 §1.6) ─────────────────
//
// Thai accounting still passes both around — an old bank statement export, an old ERP report — and until AM23 they
// were refused at the door. Now an .xls is converted to .xlsx and a .doc to PDF by the converter already there, on the
// way in: the converted file is a copy, read under its converted kind; the original's name and SHA-256 are what the run
// records and cites, and the round says the file was converted. A file that will not convert is refused by name with
// what failed — never half-read — and the round goes on without it (asking for it, when it was the one thing the round
// needed). Neither kind is ever written: a request for one is answered with why (plan_am23.go xlsSaid).

// convertedOnTheWayIn: the extensions converted before they are read, and what to.
var convertedOnTheWayIn = map[string]string{".xls": "xlsx", ".doc": "pdf"}

// errNotConverted: a file was refused because it would not convert (the round has said so; it goes on without it).
var errNotConverted = errors.New("the file would not convert and was left out")

// converted is a file's converted copy, kept as a blob of the run.
type converted struct {
	Ref     blobRef `json:"ref"`
	From    string  `json:"from"` // ".xls"
	To      string  `json:"to"`   // "xlsx"
	As      string  `json:"as"`   // the name it is read under: "statement.xlsx"
	Refused bool    `json:"refused,omitempty"`
}

// convertIn converts a file of a kind read only through a copy, once (a step, recorded): nil for any other file;
// errNotConverted when the converter could not make the copy — said by name with what failed.
func (rc *runCtx) convertIn(ctx context.Context, f am.File) (*converted, error) {
	from := strings.ToLower(filepath.Ext(f.Name))
	to, ok := convertedOnTheWayIn[from]
	if !ok {
		return nil, nil
	}
	as := strings.TrimSuffix(f.Name, filepath.Ext(f.Name)) + "." + to
	key := am.StepKey(rc.run.ID, "convert", f.SHA256, f.Name)
	out, err := rc.step(ctx, "convert:"+f.Name, "ingest", key, map[string]any{"file": f.Name, "sha256": f.SHA256, "to": to}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			path, cleanup, err := rc.r.Files.OpenLocalCopy(ctx, f.StorageKey)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			data, err := os.ReadFile(path)
			if err != nil {
				return nil, 0, "", "", err
			}
			if rc.r.Converter == nil || !rc.r.Converter.Available() {
				return nil, 0, "", "", ErrNoConverter // (a converter that is not there is waited for, not a refusal)
			}
			copyOf, err := rc.r.Converter.Convert(ctx, f.Name, data, to)
			var ce *ConvertError
			if errors.As(err, &ce) && (ce.Status == http.StatusUnsupportedMediaType || ce.Status == http.StatusUnprocessableEntity) {
				// the file's own: its content is not the kind its name says, or nothing came out of it
				why := am.Text("ตัวแปลงเอกสารเปิดไฟล์นี้ไม่ได้ ไฟล์อาจเสียหายหรือตั้งรหัสผ่านไว้")
				if ce.Status == http.StatusUnsupportedMediaType {
					why = am.Text("เนื้อไฟล์ไม่ใช่ไฟล์ %s จริง", am.Name(from))
				}
				_ = rc.r.Store.PostText(ctx, rc.run, am.Text("ไฟล์ “%s” อ่านไม่ได้: แปลงเป็น %s ไม่สำเร็จ — %s จึงไม่ได้ใช้ไฟล์นี้ในรอบนี้ ส่งไฟล์ที่บันทึกเป็น %s มาแทนได้",
					am.Name(f.Name), am.Name("."+to), am.Words(why), am.Name("."+to)))
				return converted{From: from, To: to, As: as, Refused: true}, 0, "", "", nil
			}
			if err == nil {
				if ok, why := rc.copyReads(ctx, to, copyOf); !ok {
					// LibreOffice "converts" a damaged file without a word — reading its bytes as text, or into an
					// empty sheet: such a copy is the file not converted, refused, never read as if it were the file
					_ = rc.r.Store.PostText(ctx, rc.run, am.Text("ไฟล์ “%s” อ่านไม่ได้: แปลงเป็น %s ไม่สำเร็จ — %s จึงไม่ได้ใช้ไฟล์นี้ในรอบนี้ ส่งไฟล์ที่บันทึกเป็น %s มาแทนได้",
						am.Name(f.Name), am.Name("."+to), am.Words(why), am.Name("."+to)))
					return converted{From: from, To: to, As: as, Refused: true}, 0, "", "", nil
				}
			}
			if err != nil {
				return nil, 0, "", "", err
			}
			ref, err := rc.putBlob(ctx, key, "converted."+to, copyOf)
			if err != nil {
				return nil, 0, "", "", err
			}
			_ = rc.r.Store.PostText(ctx, rc.run, am.Text("ไฟล์ “%s” เป็นไฟล์ %s รุ่นเก่า จึงแปลงเป็น %s ก่อนอ่าน ตัวเลขอ่านจากสำเนาที่แปลง ส่วนที่มาที่อ้างถึงคือไฟล์ต้นฉบับและ SHA-256 ของไฟล์นั้น",
				am.Name(f.Name), am.Name(from), am.Name("."+to)))
			return converted{Ref: ref, From: from, To: to, As: as}, 0, "", "", nil
		})
	if err != nil {
		return nil, err
	}
	var c converted
	if err := json.Unmarshal(out, &c); err != nil {
		return nil, err
	}
	if c.Refused {
		return nil, errNotConverted
	}
	return &c, nil
}

// copyReads: a converted copy is the file read — a workbook with a value in at least one cell and no character the
// import could not decode (U+FFFD: a damaged file's bytes read as text), a PDF with text on a page — and why not.
func (rc *runCtx) copyReads(ctx context.Context, to string, data []byte) (bool, am.PersonText) {
	empty := am.Text("สำเนาที่แปลงไม่มีข้อมูลเลย ไฟล์อาจเสียหาย")
	switch to {
	case "xlsx":
		z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if err != nil {
			return false, empty
		}
		some := false
		for _, f := range z.File {
			if !(strings.HasPrefix(f.Name, "xl/worksheets/") && strings.HasSuffix(f.Name, ".xml")) && f.Name != "xl/sharedStrings.xml" {
				continue
			}
			r, err := f.Open()
			if err != nil {
				continue
			}
			x, _ := io.ReadAll(io.LimitReader(r, 64<<20))
			r.Close()
			if bytes.Contains(x, []byte{0xEF, 0xBF, 0xBD}) /* U+FFFD */ {
				return false, am.Text("สำเนาที่แปลงไม่ใช่ตารางที่อ่านได้ ตัวแปลงอ่านเนื้อไฟล์เป็นตัวอักษรที่ถอดไม่ได้ ไฟล์อาจเสียหาย")
			}
			if strings.HasPrefix(f.Name, "xl/worksheets/") && (bytes.Contains(x, []byte("<v>")) || bytes.Contains(x, []byte("<is>"))) {
				some = true
			}
		}
		return some, empty
	case "pdf":
		chk, err := rc.r.Converter.Check(ctx, data)
		return err == nil && chk.Pages > 0 && strings.TrimSpace(chk.Text) != "", empty
	}
	return true, am.NoText
}
