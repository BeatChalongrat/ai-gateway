package handler

// FIX131 A2 — what POST /document/execute and /excel/execute answer when a
// plan cannot be run.
//
// NIGHT3's card showed "doc: generator failed: script reported failure": the
// handler wrote err.Error() — the service's raw English, and for a sandbox
// failure docker's own stderr — straight to the user. Now the envelope keeps
// code/message and adds `reason` (a closed set the page words in Thai with a
// next step) plus the input file it names; message is a fixed developer line.
//
// No sandbox is wired here: the unreadable source is refused before any
// generator runs, and a readable one meets the "sandbox unavailable" path.

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

type fix131Stack struct {
	docs     *DocumentHandler
	excel    *ExcelHandler
	cr       *repository.ConversationRepo
	mr       *repository.MessageRepo
	files    *repository.FileRepo
	store    storage.Backend
	conv     *model.Conversation
	tenant   uuid.UUID
	user     uuid.UUID
	messages *service.MessageService
}

func newFix131Stack(t *testing.T) *fix131Stack {
	t.Helper()
	pool := testdb.Fresh(t)
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	files := repository.NewFileRepo(pool)
	be, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatalf("storage: %v", err)
	}
	excelSvc := service.NewExcelService(files, cr, be, "", "")
	docSvc := service.NewDocService(files, cr, be, "", "")
	owners := service.NewPlanOwnerResolver(mr)
	dh := NewDocumentHandler(files, excelSvc, docSvc)
	dh.SetPlanOwnerResolver(owners)
	eh := NewExcelHandler(excelSvc, files)
	eh.SetPlanOwnerResolver(owners)
	tenant, user := uuid.New(), uuid.New()
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "FIX131"})
	if err != nil {
		t.Fatalf("conversation: %v", err)
	}
	return &fix131Stack{docs: dh, excel: eh, cr: cr, mr: mr, files: files, store: be, conv: conv, tenant: tenant, user: user,
		messages: service.NewMessageService(cr, mr, nil, nil, probeModels{}, 10)}
}

// file stores bytes + a row in the state extraction left it.
func (s *fix131Stack) file(t *testing.T, name, ct, status string, text *string) *model.ConversationFile {
	t.Helper()
	ctx := context.Background()
	key := "conversations/" + s.tenant.String() + "/" + s.conv.ID.String() + "/" + uuid.NewString() + "_" + name
	body := "%PDF-1.7 fixture " + name
	if err := s.store.Upload(ctx, key, strings.NewReader(body), ct, int64(len(body))); err != nil {
		t.Fatalf("upload: %v", err)
	}
	row, err := s.files.Create(ctx, &model.ConversationFile{ConversationID: s.conv.ID, TenantID: s.tenant, UserID: s.user,
		Filename: name, OriginalFilename: name, ContentType: ct, FileSizeBytes: int64(len(body)), StoragePath: key, ExtractionStatus: status})
	if err != nil {
		t.Fatalf("file row: %v", err)
	}
	if err := s.files.UpdateExtraction(ctx, row.ID, status, text); err != nil {
		t.Fatalf("extraction: %v", err)
	}
	row.ExtractedText = text
	return row
}

func (s *fix131Stack) message(t *testing.T, role, content string) *model.Message {
	t.Helper()
	m, err := s.mr.Create(context.Background(), &model.Message{ConversationID: s.conv.ID, TenantID: s.tenant, UserID: s.user,
		Role: role, ContentMasked: content, Status: "completed", IsActive: true})
	if err != nil {
		t.Fatalf("message: %v", err)
	}
	return m
}

func (s *fix131Stack) request(method, path string, body any) *http.Request {
	var buf bytes.Buffer
	if body != nil {
		_ = json.NewEncoder(&buf).Encode(body)
	}
	r := httptest.NewRequest(method, path, &buf)
	rc := chi.NewRouteContext()
	rc.URLParams.Add("convID", s.conv.ID.String())
	rc.URLParams.Add("id", s.conv.ID.String())
	ctx := context.WithValue(r.Context(), chi.RouteCtxKey, rc)
	ctx = middleware.WithCaller(ctx, middleware.Caller{UserID: s.user, TenantUUID: s.tenant, TenantID: s.tenant.String()})
	return r.WithContext(ctx)
}

type fix131ErrBody struct {
	Error struct {
		Code           string `json:"code"`
		Message        string `json:"message"`
		Reason         string `json:"reason"`
		SourceFileID   string `json:"source_file_id"`
		SourceFilename string `json:"source_filename"`
	} `json:"error"`
}

func (s *fix131Stack) executeDoc(t *testing.T, body map[string]any) (int, fix131ErrBody, string) {
	t.Helper()
	rec := httptest.NewRecorder()
	s.docs.Execute(rec, s.request(http.MethodPost, "/api/v1/conversations/"+s.conv.ID.String()+"/document/execute", body))
	var out fix131ErrBody
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	return rec.Code, out, rec.Body.String()
}

func (s *fix131Stack) executeExcel(t *testing.T, body map[string]any) (int, fix131ErrBody, string) {
	t.Helper()
	rec := httptest.NewRecorder()
	s.excel.Execute(rec, s.request(http.MethodPost, "/api/v1/conversations/"+s.conv.ID.String()+"/excel/execute", body))
	var out fix131ErrBody
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	return rec.Code, out, rec.Body.String()
}

func fix131Text(s string) *string { return &s }

// A failed extraction the FIX131 extractor explained: the placeholder ends
// with the machine reason.
func fix131Placeholder(name, reason string) *string {
	return fix131Text("[File: " + name + ", application/pdf, 2 KB. The file could not be read: x. reason=" + reason + "]")
}

var fix131DocPlan = map[string]any{"format": "docx", "output_filename": "summary.docx",
	"actions": []any{map[string]any{"type": "add_heading", "level": 1, "text": "สรุป"}}}

// rawLeak is what the card must never show: the service's English error or
// the generator/sandbox internals.
func fix131AssertNoRawError(t *testing.T, raw string) {
	t.Helper()
	for _, bad := range []string{"doc:", "excel:", "generator failed", "executor failed", "script", "sandbox:", "stderr"} {
		if strings.Contains(raw, bad) {
			t.Fatalf("the response leaks %q: %s", bad, raw)
		}
	}
}

func TestFIX131_ExecuteNamesAPasswordProtectedSource(t *testing.T) {
	s := newFix131Stack(t)
	locked := s.file(t, "locked.pdf", "application/pdf", "failed", fix131Placeholder("locked.pdf", "encrypted"))
	code, body, raw := s.executeDoc(t, map[string]any{"source_file_id": locked.ID.String(), "format": "docx", "plan": fix131DocPlan})
	if code != http.StatusUnprocessableEntity || body.Error.Reason != "source_encrypted" {
		t.Fatalf("a locked PDF: %d reason=%q, want 422 source_encrypted\n%s", code, body.Error.Reason, raw)
	}
	if body.Error.SourceFilename != "locked.pdf" || body.Error.SourceFileID != locked.ID.String() {
		t.Fatalf("the card must name the file: %+v", body.Error)
	}
	fix131AssertNoRawError(t, raw)
}

func TestFIX131_ExecuteNamesAScanWithNoText(t *testing.T) {
	s := newFix131Stack(t)
	scan := s.file(t, "scan.pdf", "application/pdf", "failed", fix131Placeholder("scan.pdf", "scanned"))
	code, body, raw := s.executeExcel(t, map[string]any{"source_file_id": scan.ID.String(), "output_filename": "data.xlsx",
		"plan": map[string]any{"operation": "create_workbook", "actions": []any{}}})
	if code != http.StatusUnprocessableEntity || body.Error.Reason != "source_scanned" || body.Error.SourceFilename != "scan.pdf" {
		t.Fatalf("a scan with no text → Excel: %d %+v\n%s", code, body.Error, raw)
	}
	fix131AssertNoRawError(t, raw)
}

func TestFIX131_ExecuteOnAnOldUnreadableRowStillSaysUnreadable(t *testing.T) {
	// A row written before FIX131 carries no reason marker.
	s := newFix131Stack(t)
	old := s.file(t, "old.pdf", "application/pdf", "failed", fix131Text("[File: old.pdf. The file could not be read (damaged or password-protected).]"))
	code, body, raw := s.executeDoc(t, map[string]any{"source_file_id": old.ID.String(), "format": "pdf", "plan": fix131DocPlan})
	if code != http.StatusUnprocessableEntity || body.Error.Reason != "source_unreadable" {
		t.Fatalf("old unreadable row: %d %+v\n%s", code, body.Error, raw)
	}
}

func TestFIX131_ExecuteWithTheGeneratorDownSaysSoWithoutInternals(t *testing.T) {
	s := newFix131Stack(t)
	pdf := s.file(t, "fixture.pdf", "application/pdf", "completed", fix131Text("--- Page 1 ---\nQuarterly"))
	code, body, raw := s.executeDoc(t, map[string]any{"source_file_id": pdf.ID.String(), "format": "docx", "plan": fix131DocPlan})
	if code != http.StatusServiceUnavailable || body.Error.Reason != "sandbox_unavailable" {
		t.Fatalf("no sandbox: %d %+v\n%s", code, body.Error, raw)
	}
	fix131AssertNoRawError(t, raw)
}

func TestFIX131_ExecuteOnAMissingSourceIsStillA404(t *testing.T) {
	s := newFix131Stack(t)
	code, body, _ := s.executeDoc(t, map[string]any{"source_file_id": uuid.NewString(), "format": "docx", "plan": fix131DocPlan})
	if code != http.StatusNotFound || body.Error.Code != CodeNotFound || body.Error.Reason != "source_not_found" {
		t.Fatalf("missing source: %d %+v", code, body.Error)
	}
}
