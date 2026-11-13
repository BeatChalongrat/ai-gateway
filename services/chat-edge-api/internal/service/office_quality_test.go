package service

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	amrunner "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket/runner"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

func officeWorkbook(t *testing.T, sheet string, external bool) []byte {
	t.Helper()
	var b bytes.Buffer
	w := zip.NewWriter(&b)
	for name, text := range map[string]string{"xl/workbook.xml": "<workbook/>", "xl/worksheets/sheet1.xml": "<worksheet><sheetData><row>" + sheet + "</row></sheetData></worksheet>"} {
		f, _ := w.Create(name)
		_, _ = f.Write([]byte(text))
	}
	if external {
		f, _ := w.Create("xl/externalLinks/externalLink1.xml")
		_, _ = f.Write([]byte("<externalLink/>"))
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

type fakeOfficeConverter struct {
	xlsx            []byte
	fail            error
	calls           int
	pages, rendered int
}

func (f *fakeOfficeConverter) Available() bool { return true }
func (f *fakeOfficeConverter) Convert(_ context.Context, _ string, _ []byte, to string) ([]byte, error) {
	f.calls++
	if f.fail != nil {
		return nil, f.fail
	}
	if to == "xlsx" {
		return f.xlsx, nil
	}
	return []byte("%PDF-test"), nil
}
func (f *fakeOfficeConverter) CheckWithFirstPNG(_ context.Context, _ []byte) (amrunner.CheckResult, error) {
	return amrunner.CheckResult{Pages: f.pages, RenderedPages: f.rendered, FirstPNG: []byte("PNG")}, f.fail
}

func TestOfficeRecalculatesThenChecksEveryPage(t *testing.T) {
	original := officeWorkbook(t, `<c r="A1"><f>1+2</f><v/></c>`, false)
	recalced := officeWorkbook(t, `<c r="A1"><f>1+2</f><v>3</v></c>`, false)
	conv := &fakeOfficeConverter{xlsx: recalced, pages: 2, rendered: 2}
	q := &OfficeQuality{converter: conv}
	out, err := q.review(context.Background(), "budget.xlsx", original)
	if err != nil {
		t.Fatal(err)
	}
	if !out.Report.Recalculated || out.Report.Formulas != 1 || out.Report.RenderedPages != 2 || !bytes.Equal(out.Data, recalced) || len(out.PDF) == 0 || len(out.PNG) == 0 {
		t.Fatalf("bad review: %+v", out.Report)
	}
	if conv.calls != 2 {
		t.Fatalf("wanted recalc + PDF, got %d", conv.calls)
	}
}

func TestOfficeRejectsFormulaErrorsMissingCachesAndLostFormulas(t *testing.T) {
	original := officeWorkbook(t, `<c r="A1"><f>1/0</f><v/></c>`, false)
	for _, cell := range []string{`<c r="A1" t="e"><f>1/0</f><v>#DIV/0!</v></c>`, `<c r="A1"><f>1/0</f><v/></c>`, `<c r="A1"><v>123</v></c>`} {
		q := &OfficeQuality{converter: &fakeOfficeConverter{xlsx: officeWorkbook(t, cell, false), pages: 1, rendered: 1}}
		if _, err := q.review(context.Background(), "bad.xlsx", original); err == nil {
			t.Fatal("invalid recalculation accepted")
		}
	}
}

func TestOfficeRefusesExternalLinksBeforeConversion(t *testing.T) {
	conv := &fakeOfficeConverter{}
	_, err := (&OfficeQuality{converter: conv}).review(context.Background(), "external.xlsx", officeWorkbook(t, `<c r="A1"><f>1+2</f></c>`, true))
	if err == nil || conv.calls != 0 {
		t.Fatal("external workbook reached converter")
	}
}

func TestOfficeWithoutConverterReportsMissingVerification(t *testing.T) {
	out, err := NewOfficeQuality("").review(context.Background(), "budget.xlsx", officeWorkbook(t, `<c r="A1"><f>1+2</f><v/></c>`, false))
	if err != nil {
		t.Fatal(err)
	}
	if out.Report.Recalculated || out.Report.Status != "partial" || len(out.Report.Warnings) != 2 {
		t.Fatalf("misleading report: %+v", out.Report)
	}
}

func TestOfficeRejectsIncompleteRendering(t *testing.T) {
	q := &OfficeQuality{converter: &fakeOfficeConverter{pages: 5, rendered: 2}}
	if _, err := q.review(context.Background(), "report.pdf", []byte("%PDF-test")); err == nil {
		t.Fatal("partial render was approved")
	}
}

func TestOfficeFailedArtifactsNotPublished(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	q := &OfficeQuality{converter: &fakeOfficeConverter{fail: errors.New("converter busy")}}
	res := &sandbox.Result{Artifacts: []sandbox.Artifact{{Name: "report.docx", Path: path}}}
	q.Apply(context.Background(), res)
	if res.Exit != 1 || len(res.Artifacts) != 0 || !strings.Contains(string(res.Stderr), "converter busy") {
		t.Fatalf("bad failure: %+v", res)
	}
	data, _ := os.ReadFile(path)
	if string(data) != "original" {
		t.Fatal("failure replaced input")
	}
}

func TestOfficePreviewsCannotOverwriteUserArtifacts(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "report.docx")
	if err := os.WriteFile(path, []byte("source"), 0600); err != nil {
		t.Fatal(err)
	}
	collision := filepath.Join(dir, "report.docx.preview.pdf")
	if err := os.WriteFile(collision, []byte("user preview"), 0600); err != nil {
		t.Fatal(err)
	}
	q := &OfficeQuality{converter: &fakeOfficeConverter{pages: 1, rendered: 1}}
	res := &sandbox.Result{Artifacts: []sandbox.Artifact{{Name: "report.docx", Path: path}, {Name: "report.docx.preview.pdf", Path: collision}}}
	q.Apply(context.Background(), res)
	got, _ := os.ReadFile(collision)
	if string(got) != "user preview" {
		t.Fatal("overwrote user preview")
	}
	if len(res.Artifacts) < 2 || res.Artifacts[0].Name != "report.docx" {
		t.Fatal("previews took original priority")
	}
}

func TestOfficePlanResponseKeepsJSONContract(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "report.pdf")
	if err := os.WriteFile(path, []byte("%PDF-test"), 0600); err != nil {
		t.Fatal(err)
	}
	q := &OfficeQuality{converter: &fakeOfficeConverter{pages: 1, rendered: 1}}
	raw, err := q.FinalizePlan(context.Background(), &sandbox.Artifact{Name: "report.pdf", Path: path}, []byte(`{"success":true,"change_log":[]}`))
	if err != nil {
		t.Fatal(err)
	}
	var report DocGenerationResult
	if json.Unmarshal(raw, &report) != nil || !report.Success || report.OfficeQuality == nil || report.OfficeQuality.RenderedPages != 1 {
		t.Fatalf("invalid response: %s", raw)
	}
}

func TestOfficeMalformedWorkbookDoesNotReachConverter(t *testing.T) {
	conv := &fakeOfficeConverter{}
	q := &OfficeQuality{converter: conv}
	if _, err := q.review(context.Background(), "fake.xlsx", []byte("not a ZIP")); err == nil || conv.calls != 0 {
		t.Fatal("accepted malformed workbook")
	}
}

func TestOfficeConverterRejectsPublicURL(t *testing.T) {
	for _, url := range []string{"https://example.com", "http://8.8.8.8", "file:///tmp/file", "http://user:password@localhost"} {
		if NewOfficeQuality(url).ConfigurationError() == nil {
			t.Fatalf("accepted %s", url)
		}
	}
	for _, url := range []string{"", "http://office-converter:8096", "http://127.0.0.1:18096"} {
		if err := NewOfficeQuality(url).ConfigurationError(); err != nil {
			t.Fatal(err)
		}
	}
}

type officeCodeRunner struct{ result *sandbox.Result }

func (r officeCodeRunner) Execute(_ context.Context, _ sandbox.Spec) (*sandbox.Result, error) {
	return r.result, nil
}

func TestOfficeCodeInterpreterAppliesGateBeforeDelivery(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "bad.xlsx")
	if err := os.WriteFile(path, []byte("not a workbook"), 0600); err != nil {
		t.Fatal(err)
	}
	interp := NewCodeInterpreter(officeCodeRunner{&sandbox.Result{Artifacts: []sandbox.Artifact{{Name: "bad.xlsx", Path: path}}}})
	interp.SetOfficeQuality(NewOfficeQuality(""))
	res, err := interp.Run(context.Background(), "print('done')", nil)
	if err != nil {
		t.Fatal(err)
	}
	if res.Exit != 1 || len(res.Artifacts) != 0 {
		t.Fatal("invalid Office result bypassed the shared code gate")
	}
}

// Optional real service acceptance; fixtures are generated by office_templates.
func TestOfficeLiveConverter(t *testing.T) {
	url, dir := os.Getenv("OFFICE_TEST_CONVERTER_URL"), os.Getenv("OFFICE_TEST_FIXTURES")
	if url == "" || dir == "" {
		t.Skip("set OFFICE_TEST_CONVERTER_URL and OFFICE_TEST_FIXTURES")
	}
	q := NewOfficeQuality(url)
	for _, name := range []string{"budget.xlsx", "report.docx", "deck.pptx", "report.pdf"} {
		t.Run(name, func(t *testing.T) {
			data, err := os.ReadFile(filepath.Join(dir, name))
			if err != nil {
				t.Fatal(err)
			}
			out, err := q.review(context.Background(), name, data)
			if err != nil {
				t.Fatal(err)
			}
			if out.Report.RenderedPages == 0 || out.Report.Status != "checked" {
				t.Fatalf("not checked: %+v", out.Report)
			}
			if name == "budget.xlsx" && !out.Report.Recalculated {
				t.Fatal("not recalculated")
			}
			if err := os.WriteFile(filepath.Join(dir, "checked-"+name), out.Data, 0600); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(dir, name+".png"), out.PNG, 0600); err != nil {
				t.Fatal(err)
			}
		})
	}
}
