package service

// docContext / pptxContext zero-file gate tests (Phase-1 quick-win fix).
//
// Before the fix both functions returned "" when the conversation had
// no files at all, so "create a PDF report about X" in a brand-new
// conversation never received the :::doc-plan / :::pptx-plan contract
// and the model answered with prose instead of a plan card. The
// headerless capabilities prompt was only reachable when the
// conversation already had at least one file of ANY type.
//
// excelContext intentionally keeps its "" gate: ExcelService.ExecutePlan
// requires a resolvable SourceFileID (excel_service.go), so advertising
// create-from-scratch there would produce cards that 404 on Execute.

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type stubExcelLookup struct {
	files []model.ConversationFile
	err   error
}

func (s stubExcelLookup) ListByConversation(
	_ context.Context, _, _ uuid.UUID,
) ([]model.ConversationFile, error) {
	return s.files, s.err
}

func docGateService(files []model.ConversationFile, err error) *MessageService {
	return &MessageService{excelLookup: stubExcelLookup{files: files, err: err}}
}

func docxFile() model.ConversationFile {
	inspection := `{"paragraphs": 3}`
	return model.ConversationFile{
		ID:               uuid.New(),
		ContentType:      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
		OriginalFilename: "report.docx",
		InspectionJSON:   &inspection,
	}
}

func TestDocContext_ZeroFiles_InjectsHeaderlessContract(t *testing.T) {
	s := docGateService(nil, nil)
	got := s.docContext(context.Background(), uuid.New(), uuid.New())

	if got == "" {
		t.Fatal("docContext returned empty for a zero-file conversation — from-scratch generation is gated off again")
	}
	if !strings.Contains(got, ":::doc-plan") {
		t.Errorf("headerless doc prompt must carry the :::doc-plan contract:\n%.200s", got)
	}
	if !strings.Contains(got, "No document is currently uploaded") {
		t.Errorf("expected the headerless preamble, got:\n%.200s", got)
	}
}

func TestPptxContext_ZeroFiles_InjectsHeaderlessContract(t *testing.T) {
	s := docGateService(nil, nil)
	got := s.pptxContext(context.Background(), uuid.New(), uuid.New())

	if got == "" {
		t.Fatal("pptxContext returned empty for a zero-file conversation")
	}
	if !strings.Contains(got, ":::pptx-plan") {
		t.Errorf("headerless pptx prompt must carry the :::pptx-plan contract:\n%.200s", got)
	}
	if !strings.Contains(got, "No presentation is currently uploaded") {
		t.Errorf("expected the headerless preamble, got:\n%.200s", got)
	}
}

func TestDocContext_LookupError_StillInjectsHeaderless(t *testing.T) {
	s := docGateService(nil, context.DeadlineExceeded)
	got := s.docContext(context.Background(), uuid.New(), uuid.New())
	if !strings.Contains(got, ":::doc-plan") {
		t.Errorf("a failed lookup should degrade to the headerless contract, got:\n%.200s", got)
	}
}

func TestDocContext_WithInspectedDocx_UnchangedFullBlock(t *testing.T) {
	f := docxFile()
	s := docGateService([]model.ConversationFile{f}, nil)
	got := s.docContext(context.Background(), uuid.New(), uuid.New())

	if !strings.Contains(got, "## Document Context") {
		t.Errorf("uploaded-doc path must keep the full structure block:\n%.200s", got)
	}
	if !strings.Contains(got, f.ID.String()) {
		t.Errorf("uploaded-doc path must embed the file_id:\n%.200s", got)
	}
	if strings.Contains(got, "No document is currently uploaded") {
		t.Errorf("uploaded-doc path must NOT use the headerless preamble:\n%.200s", got)
	}
}

func TestDocContext_FilesButNoDoc_KeepsHeaderless(t *testing.T) {
	// Pre-fix behavior for "has files, none of them docs" was already
	// the headerless prompt — pin that it survived the change.
	img := model.ConversationFile{
		ID:               uuid.New(),
		ContentType:      "image/png",
		OriginalFilename: "photo.png",
	}
	s := docGateService([]model.ConversationFile{img}, nil)
	got := s.docContext(context.Background(), uuid.New(), uuid.New())
	if !strings.Contains(got, "No document is currently uploaded") {
		t.Errorf("files-but-no-doc path should still return the headerless prompt:\n%.200s", got)
	}
}

func TestExcelContext_ZeroFiles_StaysEmpty(t *testing.T) {
	// Documented asymmetry: excel from-scratch is NOT advertised
	// because ExecutePlan requires a source workbook.
	s := docGateService(nil, nil)
	if got := s.excelContext(context.Background(), uuid.New(), uuid.New()); got != "" {
		t.Errorf("excelContext should stay empty without an uploaded workbook, got:\n%.200s", got)
	}
}
