package service

// FIX131 A — the REAL generators, end to end, through the hardened sandbox.
//
// NIGHT3: a PDF attached, the model proposed a Word plan whose
// source_file_id was the PDF, and "สร้างเอกสาร" answered 502 "doc: generator
// failed: script reported failure" — the generator's edit path copied the
// PDF to output.docx and opened it as a Word file. These tests run
// DocService/ExcelService.ExecutePlan against the docker sandbox image the
// service uses (FIX131_SANDBOX_IMAGE, default ai-gateway-sandbox:latest) and
// a per-test database. They SKIP when docker or the image is unavailable,
// like code_interpreter_live_test.go.

import (
	"archive/zip"
	"context"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

type fix131Live struct {
	docs   *DocService
	excel  *ExcelService
	files  *repository.FileRepo
	store  storage.Backend
	conv   *model.Conversation
	tenant uuid.UUID
	user   uuid.UUID
}

// fix131Sandbox builds a runner on the local docker, or skips.
func fix131Sandbox(t *testing.T) *sandbox.Runner {
	t.Helper()
	workRoot := "/tmp/ai-gw-sbx-fix131-test"
	if runtime.GOOS == "windows" {
		workRoot = "C:/ai-gw-sbx-fix131-test"
	}
	if err := os.MkdirAll(workRoot, 0o777); err != nil {
		t.Fatalf("mkdir work root: %v", err)
	}
	b, err := os.ReadFile(filepath.Join("..", "..", "sandbox", "seccomp.json"))
	if err != nil {
		t.Skipf("seccomp profile not found (%v) — run from repo", err)
	}
	seccomp := filepath.Join(workRoot, "seccomp.json")
	if err := os.WriteFile(seccomp, b, 0o644); err != nil {
		t.Fatalf("copy seccomp: %v", err)
	}
	image := os.Getenv("FIX131_SANDBOX_IMAGE")
	if image == "" {
		image = "ai-gateway-sandbox:latest"
	}
	r := sandbox.New(sandbox.Config{Image: image, WorkDir: workRoot, HostWorkDir: workRoot, SeccompPath: seccomp})
	if err := r.Available(context.Background()); err != nil {
		t.Skipf("sandbox unavailable: %v", err)
	}
	return r
}

func newFix131Live(t *testing.T) *fix131Live {
	t.Helper()
	sb := fix131Sandbox(t)
	pool := testdb.Fresh(t)
	cr := repository.NewConversationRepo(pool)
	files := repository.NewFileRepo(pool)
	be, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatalf("local storage: %v", err)
	}
	docs := NewDocService(files, cr, be, "", "")
	docs.SetSandbox(sb)
	excel := NewExcelService(files, cr, be, "", "")
	excel.SetSandbox(sb)
	tenant, user := uuid.New(), uuid.New()
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "FIX131"})
	if err != nil {
		t.Fatalf("conversation: %v", err)
	}
	return &fix131Live{docs: docs, excel: excel, files: files, store: be, conv: conv, tenant: tenant, user: user}
}

// fix131Fixture is a file under scripts/probes/FIX131/results/lane-docs/
// fixtures when the matrix has been built, else the named bytes.
func fix131FixtureBytes(t *testing.T, name string) []byte {
	t.Helper()
	dir := os.Getenv("FIX131_FIXTURES")
	if dir == "" {
		dir = filepath.Join("..", "..", "..", "..", "scripts", "probes", "FIX131", "results", "lane-docs", "fixtures")
	}
	b, err := os.ReadFile(filepath.Join(dir, name))
	if err != nil {
		t.Skipf("fixture %s not built (%v) — run make_fixtures.py", name, err)
	}
	return b
}

// upload stores bytes and a row the way an attached file ends up after its
// extraction: status + extracted text.
func (f *fix131Live) upload(t *testing.T, name, ct string, body []byte, status string, text *string) *model.ConversationFile {
	t.Helper()
	ctx := context.Background()
	key := storage.JoinKey(conversationFilesKeyPrefix, f.tenant.String(), f.conv.ID.String(), uuid.NewString()+"_"+name)
	if err := f.store.Upload(ctx, key, strings.NewReader(string(body)), ct, int64(len(body))); err != nil {
		t.Fatalf("upload %s: %v", name, err)
	}
	row, err := f.files.Create(ctx, &model.ConversationFile{
		ConversationID: f.conv.ID, TenantID: f.tenant, UserID: f.user,
		Filename: name, OriginalFilename: name, ContentType: ct, FileSizeBytes: int64(len(body)),
		StoragePath: key, ExtractionStatus: status,
	})
	if err != nil {
		t.Fatalf("row %s: %v", name, err)
	}
	if err := f.files.UpdateExtraction(ctx, row.ID, status, text); err != nil {
		t.Fatalf("extraction %s: %v", name, err)
	}
	row.ExtractedText = text
	return row
}

// zipPart reads one part of a generated Office file from storage.
func (f *fix131Live) zipPart(t *testing.T, key, part string) string {
	t.Helper()
	path, cleanup, err := f.store.OpenLocalCopy(context.Background(), key)
	if err != nil {
		t.Fatalf("open output: %v", err)
	}
	defer cleanup()
	zr, err := zip.OpenReader(path)
	if err != nil {
		t.Fatalf("output is not an Office file: %v", err)
	}
	defer zr.Close()
	for _, zf := range zr.File {
		if zf.Name == part {
			rc, err := zf.Open()
			if err != nil {
				t.Fatalf("open %s: %v", part, err)
			}
			defer rc.Close()
			b, _ := io.ReadAll(rc)
			return string(b)
		}
	}
	t.Fatalf("output has no %s", part)
	return ""
}

func fix131DocPlan(heading string) map[string]any {
	return map[string]any{
		"format":            "docx",
		"output_filename":   "summary.docx",
		"document_settings": map[string]any{"language": "th"},
		"actions": []any{
			map[string]any{"type": "add_heading", "level": 1, "text": heading},
			map[string]any{"type": "add_paragraph", "text": "ยอดขายเพิ่มขึ้นร้อยละ 12"},
		},
	}
}

func fix131Str(s string) *string { return &s }

// The NIGHT3 repro: a text PDF attached, a Word plan naming it as the
// source. The PDF is INPUT: a new .docx is built from the plan.
func TestFIX131_PdfSourceBuildsANewWordFile_Live(t *testing.T) {
	f := newFix131Live(t)
	pdf := f.upload(t, "fixture.pdf", pdfContentType, fix131FixtureBytes(t, "fixture.pdf"), "completed", fix131Str("--- Page 1 ---\nQuarterly Report"))
	row, _, _, err := f.docs.ExecutePlan(context.Background(), DocExecution{
		TenantID: f.tenant, ConversationID: f.conv.ID, UserID: f.user,
		SourceFileID: pdf.ID, Plan: fix131DocPlan("สรุปรายงานไตรมาส"), Format: "docx", OutputFilename: "summary.docx",
	})
	if err != nil {
		t.Fatalf("PDF → Word must build a new document, got: %v", err)
	}
	if row.ContentType != docxContentType || row.OriginalFilename != "summary.docx" {
		t.Fatalf("output = %s %s", row.OriginalFilename, row.ContentType)
	}
	if row.ParentFileID == nil || *row.ParentFileID != pdf.ID {
		t.Fatalf("provenance: parent_file_id = %v, want the PDF %s", row.ParentFileID, pdf.ID)
	}
	body := f.zipPart(t, row.StoragePath, "word/document.xml")
	if !strings.Contains(body, "สรุปรายงานไตรมาส") || !strings.Contains(body, "ยอดขายเพิ่มขึ้นร้อยละ 12") {
		t.Fatalf("the new document lacks the plan's heading/paragraph")
	}
}

// PDF → PowerPoint: the same rule for the pptx generator.
func TestFIX131_PdfSourceBuildsANewDeck_Live(t *testing.T) {
	f := newFix131Live(t)
	pdf := f.upload(t, "fixture.pdf", pdfContentType, fix131FixtureBytes(t, "fixture.pdf"), "completed", fix131Str("Quarterly Report"))
	row, _, _, err := f.docs.ExecutePlan(context.Background(), DocExecution{
		TenantID: f.tenant, ConversationID: f.conv.ID, UserID: f.user, SourceFileID: pdf.ID, Format: "pptx",
		OutputFilename: "deck.pptx",
		Plan: map[string]any{"format": "pptx", "slides": []any{
			map[string]any{"type": "title", "title": "รายงานไตรมาส"},
			map[string]any{"type": "content", "title": "ภาพรวม", "bullets": []any{"ยอดขาย +12%"}},
		}},
	})
	if err != nil {
		t.Fatalf("PDF → PowerPoint must build a new deck, got: %v", err)
	}
	if !strings.Contains(f.zipPart(t, row.StoragePath, "ppt/slides/slide1.xml"), "รายงานไตรมาส") {
		t.Fatalf("the deck lacks the title slide")
	}
}

// CSV → Excel: the executor must not load_workbook() a CSV.
func TestFIX131_CsvSourceBuildsANewWorkbook_Live(t *testing.T) {
	f := newFix131Live(t)
	csv := f.upload(t, "sales.csv", "text/csv", fix131FixtureBytes(t, "sales.csv"), "completed", fix131Str("month,sales\nJan,1200"))
	row, _, err := f.excel.ExecutePlan(context.Background(), PlannedExecution{
		TenantID: f.tenant, ConversationID: f.conv.ID, UserID: f.user, SourceFileID: csv.ID, OutputFilename: "sales.xlsx",
		Plan: map[string]any{"operation": "modify_workbook", "source_file_id": csv.ID.String(), "actions": []any{
			map[string]any{"type": "create_sheet", "sheet_name": "Sales"},
			map[string]any{"type": "write_table", "sheet_name": "Sales", "start_cell": "A1",
				"headers": []any{"month", "sales"}, "data": []any{[]any{"Jan", 1200}, []any{"Feb", 1350}}},
		}},
	})
	if err != nil {
		t.Fatalf("CSV → Excel must build a new workbook, got: %v", err)
	}
	if row.ContentType != xlsxContentType {
		t.Fatalf("output content type = %s", row.ContentType)
	}
	if !strings.Contains(f.zipPart(t, row.StoragePath, "xl/workbook.xml"), `name="Sales"`) {
		t.Fatalf("the workbook lacks the Sales sheet")
	}
}

// A .docx edited into a .docx keeps the edit path: the source's own content
// survives next to the plan's additions.
func TestFIX131_DocxSourceIsStillEdited_Live(t *testing.T) {
	f := newFix131Live(t)
	src := f.upload(t, "source.docx", docxContentType, fix131FixtureBytes(t, "source.docx"), "completed", fix131Str("Source Report"))
	row, _, gen, err := f.docs.ExecutePlan(context.Background(), DocExecution{
		TenantID: f.tenant, ConversationID: f.conv.ID, UserID: f.user, SourceFileID: src.ID, Format: "docx",
		Plan: map[string]any{"format": "docx", "actions": []any{map[string]any{"type": "add_paragraph", "text": "Added by the plan."}}},
	})
	if err != nil {
		t.Fatalf("DOCX → DOCX edit: %v", err)
	}
	if gen.Mode != "edit" {
		t.Fatalf("generator mode = %q, want edit", gen.Mode)
	}
	body := f.zipPart(t, row.StoragePath, "word/document.xml")
	if !strings.Contains(body, "Source Report") || !strings.Contains(body, "Added by the plan.") {
		t.Fatalf("the edited copy must keep the source and add the plan's paragraph")
	}
	if row.OriginalFilename != "source_edited.docx" || row.Version != 2 {
		t.Fatalf("edit lineage: %s v%d", row.OriginalFilename, row.Version)
	}
}

// DOCX → PDF keeps working (the PDF generator never takes a source).
func TestFIX131_DocxToPdfStillWorks_Live(t *testing.T) {
	f := newFix131Live(t)
	src := f.upload(t, "source.docx", docxContentType, fix131FixtureBytes(t, "source.docx"), "completed", fix131Str("Source Report"))
	plan := fix131DocPlan("รายงาน PDF")
	plan["format"] = "pdf"
	row, v, _, err := f.docs.ExecutePlan(context.Background(), DocExecution{
		TenantID: f.tenant, ConversationID: f.conv.ID, UserID: f.user, SourceFileID: src.ID, Format: "pdf",
		OutputFilename: "report.pdf", Plan: plan,
	})
	if err != nil {
		t.Fatalf("DOCX → PDF: %v", err)
	}
	if row.ContentType != pdfContentType || !v.Valid {
		t.Fatalf("output %s valid=%v", row.ContentType, v.Valid)
	}
}
