package service

// Office post-processing is shared by web tools, desktop /code/run and plan
// executors. Only the isolated converter runs LibreOffice; chat-edge never
// shells out with its credentials. Workbook XML inspection is bounded.
import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"encoding/xml"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	amrunner "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket/runner"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

type officeConverter interface {
	Available() bool
	Convert(context.Context, string, []byte, string) ([]byte, error)
	CheckWithFirstPNG(context.Context, []byte) (amrunner.CheckResult, error)
}

type OfficeQuality struct {
	converter    officeConverter
	configErr    error
	repairRunner sandboxRunner
}

func NewOfficeQuality(url string) *OfficeQuality {
	if strings.TrimSpace(url) == "" {
		return &OfficeQuality{}
	}
	// Reuse the existing private-address dial guard, including DNS checks and
	// no environment proxy. Redirects must not forward document bytes.
	internal, err := NewKBModelsClient(url)
	if err != nil {
		return &OfficeQuality{configErr: fmt.Errorf("OFFICE_CONVERTER_URL: %w", err)}
	}
	internal.http.CheckRedirect = func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	return &OfficeQuality{converter: &amrunner.Converter{BaseURL: internal.base, HTTP: internal.http, Timeout: 60 * time.Second}}
}

func (q *OfficeQuality) ConfigurationError() error { return q.configErr }

type OfficeQualityReport struct {
	RepairAttempts int      `json:"repair_attempts,omitempty"`
	Repaired       bool     `json:"repaired"`
	EdgeInk        float64  `json:"edge_ink"`
	Filename       string   `json:"filename"`
	Status         string   `json:"status"`
	Recalculated   bool     `json:"recalculated"`
	Formulas       int      `json:"formulas,omitempty"`
	Pages          int      `json:"pages,omitempty"`
	RenderedPages  int      `json:"rendered_pages,omitempty"`
	Warnings       []string `json:"warnings,omitempty"`
}

type officeReviewed struct {
	RenderText string
	BlankPages int
	Data       []byte
	PDF        []byte
	PNG        []byte
	Report     OfficeQualityReport
}

type workbookFacts struct {
	Formulas     int
	MissingCache int
	Errors       []string
	External     bool
}

func inspectWorkbook(data []byte) (workbookFacts, error) {
	var facts workbookFacts
	z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return facts, fmt.Errorf("invalid workbook archive")
	}
	if len(z.File) > 10000 {
		return facts, fmt.Errorf("workbook has too many parts")
	}
	var expanded uint64
	main := false
	seen := map[string]bool{}
	for _, f := range z.File {
		if seen[f.Name] {
			return facts, fmt.Errorf("duplicate workbook archive part")
		}
		seen[f.Name] = true
		if f.Name == "xl/workbook.xml" {
			main = true
		}
		if strings.HasPrefix(f.Name, "xl/externalLinks/") || strings.HasSuffix(strings.ToLower(f.Name), "vbaproject.bin") {
			facts.External = true
		}
		if !strings.HasPrefix(f.Name, "xl/worksheets/") || !strings.HasSuffix(f.Name, ".xml") {
			continue
		}
		if f.UncompressedSize64 > (40<<20)-expanded {
			return facts, fmt.Errorf("workbook XML exceeds inspection limit")
		}
		expanded += f.UncompressedSize64
		r, err := f.Open()
		if err != nil {
			return facts, err
		}
		dec := xml.NewDecoder(io.LimitReader(r, 40<<20))
		for {
			token, readErr := dec.Token()
			if readErr == io.EOF {
				break
			}
			if readErr != nil {
				r.Close()
				return facts, fmt.Errorf("invalid worksheet XML")
			}
			start, ok := token.(xml.StartElement)
			if !ok || start.Name.Local != "c" {
				continue
			}
			var cell struct {
				Ref     string  `xml:"r,attr"`
				Type    string  `xml:"t,attr"`
				Formula *string `xml:"f"`
				Value   *string `xml:"v"`
			}
			if err := dec.DecodeElement(&cell, &start); err != nil {
				r.Close()
				return facts, err
			}
			if cell.Formula != nil {
				facts.Formulas++
				if cell.Value == nil || (*cell.Value == "" && cell.Type != "str") {
					facts.MissingCache++
				}
			}
			if cell.Type == "e" && len(facts.Errors) < 100 {
				value := "formula error"
				if cell.Value != nil {
					value = *cell.Value
				}
				facts.Errors = append(facts.Errors, fmt.Sprintf("%.120s!%.24s: %.120s", f.Name, cell.Ref, value))
			}
		}
		r.Close()
	}
	if !main {
		return facts, fmt.Errorf("archive is not an XLSX workbook")
	}
	return facts, nil
}

func officeExtension(name string) bool {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".xlsx", ".docx", ".pptx", ".pdf":
		return true
	}
	return false
}

func (q *OfficeQuality) reviewOnce(ctx context.Context, name string, data []byte) (officeReviewed, error) {
	out := officeReviewed{Data: data, Report: OfficeQualityReport{Filename: name, Status: "partial"}}
	if q != nil && q.configErr != nil {
		return out, q.configErr
	}
	ext := strings.ToLower(filepath.Ext(name))
	ctx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()
	available := q != nil && q.converter != nil && q.converter.Available()
	if len(data) > 25<<20 {
		return out, fmt.Errorf("Office review supports files up to 25 MiB")
	}
	if ext == ".xlsx" {
		facts, err := inspectWorkbook(data)
		if err != nil {
			return out, err
		}
		out.Report.Formulas = facts.Formulas
		if facts.External {
			return out, fmt.Errorf("automatic recalculation refuses external workbook links or macros; preserve and review the original separately")
		}
		if facts.Formulas > 0 && available {
			converted, err := q.converter.Convert(ctx, name, data, "xlsx")
			if err != nil {
				return out, fmt.Errorf("workbook recalculation failed: %w", err)
			}
			checked, err := inspectWorkbook(converted)
			if err != nil {
				return out, err
			}
			if checked.Formulas != facts.Formulas || checked.External || checked.MissingCache > 0 {
				return out, fmt.Errorf("recalculated workbook lost formulas, introduced links, or still has missing cached values")
			}
			facts, out.Data = checked, converted
			out.Report.Recalculated = true
		}
		if len(facts.Errors) > 0 {
			return out, fmt.Errorf("spreadsheet errors: %s", strings.Join(facts.Errors, "; "))
		}
		if facts.MissingCache > 0 {
			out.Report.Warnings = append(out.Report.Warnings, "Formula values are not evaluated; converter is not configured")
		}
	}
	if !available {
		out.Report.Warnings = append(out.Report.Warnings, "Rendering not performed: OFFICE_CONVERTER_URL is not configured")
		return out, nil
	}
	pdf := out.Data
	if ext != ".pdf" {
		var err error
		pdf, err = q.converter.Convert(ctx, name, out.Data, "pdf")
		if err != nil {
			return out, fmt.Errorf("Office rendering failed: %w", err)
		}
		out.PDF = pdf
	}
	check, err := q.converter.CheckWithFirstPNG(ctx, pdf)
	if err != nil {
		return out, fmt.Errorf("PDF inspection failed: %w", err)
	}
	if check.Pages < 1 || check.RenderedPages != check.Pages {
		return out, fmt.Errorf("not every document page could be rendered (%d/%d)", check.RenderedPages, check.Pages)
	}
	out.Report.Pages, out.Report.RenderedPages = check.Pages, check.RenderedPages
	for i, ink := range check.PageInk {
		if ink < .0001 {
			out.BlankPages++
			out.Report.Warnings = append(out.Report.Warnings, fmt.Sprintf("Page %d may be blank", i+1))
		}
	}
	for i, ink := range check.MarginInk {
		if ink > .001 {
			out.Report.EdgeInk += ink
		}
		if ink > .001 {
			out.Report.Warnings = append(out.Report.Warnings, fmt.Sprintf("Page %d has content near its edge; inspect clipping", i+1))
		}
	}
	out.RenderText = check.RawText
	out.PNG = check.FirstPNG
	out.Report.Status = "checked"
	out.Report.Warnings = append(out.Report.Warnings, "Rendered and structurally checked; visual layout and business accuracy still require review")
	return out, nil
}

// Apply is called before artifacts are persisted. Failed Office files are not
// attached as successful deliverables; the model receives a repairable error.
// Original artifacts retain priority over additional previews under the cap.
func (q *OfficeQuality) Apply(ctx context.Context, res *sandbox.Result) {
	if q == nil || res == nil || res.Exit != 0 || res.TimedOut {
		return
	}
	ctx, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()
	kept := make([]sandbox.Artifact, 0, len(res.Artifacts))
	if len(res.Artifacts) > maxArtifacts {
		res.Artifacts = res.Artifacts[:maxArtifacts]
		res.Truncated = true
		res.Stderr = append(res.Stderr, []byte("\nOnly the first 8 artifacts were reviewed and delivered; generate fewer files per call.")...)
	}
	var extras []sandbox.Artifact
	var reports []OfficeQualityReport
	used := map[string]bool{}
	for _, art := range res.Artifacts {
		used[art.Name] = true
	}
	for _, art := range res.Artifacts {
		if !officeExtension(art.Name) {
			kept = append(kept, art)
			continue
		}
		data, err := ReadArtifact(art.Path)
		var reviewed officeReviewed
		if err == nil {
			reviewed, err = q.review(ctx, art.Name, data)
		}
		if err == nil {
			err = os.WriteFile(art.Path, reviewed.Data, 0600)
		}
		if err != nil {
			res.Exit = 1
			res.Stderr = append(res.Stderr, []byte("\nOffice validation failed for "+art.Name+": "+err.Error())...)
			continue
		}
		kept = append(kept, art)
		reports = append(reports, reviewed.Report)
		for _, extra := range []struct {
			suffix string
			data   []byte
		}{{".preview.pdf", reviewed.PDF}, {".page-1.png", reviewed.PNG}} {
			name := art.Name + extra.suffix
			if len(extra.data) == 0 || used[name] {
				continue
			}
			path := filepath.Join(filepath.Dir(art.Path), name)
			// Do not follow a model-created path that was excluded as an artifact.
			f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if err != nil {
				continue
			}
			_, err = f.Write(extra.data)
			closeErr := f.Close()
			if err == nil && closeErr == nil {
				extras = append(extras, sandbox.Artifact{Name: name, Path: path})
				used[name] = true
			}
		}
	}
	res.Artifacts = kept
	for _, art := range extras {
		if len(res.Artifacts) < maxArtifacts {
			res.Artifacts = append(res.Artifacts, art)
		}
	}
	if len(reports) > 0 {
		raw, _ := json.Marshal(reports)
		res.Stdout = append(res.Stdout, append([]byte("\nOffice quality report: "), raw...)...)
	}
}

// FinalizePlan keeps the existing plan JSON response contract while adding
// review evidence. Plan output is not published if the quality gate fails.
func (q *OfficeQuality) FinalizePlan(ctx context.Context, art *sandbox.Artifact, stdout []byte) ([]byte, error) {
	if q == nil {
		return stdout, nil
	}
	data, err := ReadArtifact(art.Path)
	if err != nil {
		return stdout, err
	}
	reviewed, err := q.review(ctx, art.Name, data)
	if err != nil {
		return stdout, fmt.Errorf("Office validation: %w", err)
	}
	if err := os.WriteFile(art.Path, reviewed.Data, 0600); err != nil {
		return stdout, err
	}
	var result map[string]any
	if json.Unmarshal(stdout, &result) == nil && result != nil {
		result["office_quality"] = reviewed.Report
		result["file_size_bytes"] = len(reviewed.Data)
		stdout, _ = json.Marshal(result)
	}
	return stdout, nil
}
