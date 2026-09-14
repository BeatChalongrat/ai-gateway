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
// excelContext used to keep a "" gate because ExecutePlan required a
// resolvable SourceFileID, so advertising create-from-scratch would have
// produced cards that 404 on Execute. Phase 8 Task 1 added create-mode to
// ExcelService.ExecutePlan (source_file_id optional → operation forced to
// create_workbook), so excel now behaves like doc/pptx: zero files still
// gets the headerless contract.

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
	got := s.docContext(context.Background(), uuid.New(), uuid.New(), true)

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
	got := s.pptxContext(context.Background(), uuid.New(), uuid.New(), true)

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
	got := s.docContext(context.Background(), uuid.New(), uuid.New(), true)
	if !strings.Contains(got, ":::doc-plan") {
		t.Errorf("a failed lookup should degrade to the headerless contract, got:\n%.200s", got)
	}
}

func TestDocContext_WithInspectedDocx_UnchangedFullBlock(t *testing.T) {
	f := docxFile()
	s := docGateService([]model.ConversationFile{f}, nil)
	got := s.docContext(context.Background(), uuid.New(), uuid.New(), true)

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
	got := s.docContext(context.Background(), uuid.New(), uuid.New(), true)
	if !strings.Contains(got, "No document is currently uploaded") {
		t.Errorf("files-but-no-doc path should still return the headerless prompt:\n%.200s", got)
	}
}

// ─── Phase 8 Task 1 — excel from-scratch ────────────────────────────

func TestExcelContext_ZeroFiles_InjectsHeaderlessContract(t *testing.T) {
	// Replaces TestExcelContext_ZeroFiles_StaysEmpty: create-mode exists
	// now, so the contract must be advertised (a plan card in an empty
	// chat executes instead of 404ing).
	s := docGateService(nil, nil)
	got := s.excelContext(context.Background(), uuid.New(), uuid.New(), true)

	if got == "" {
		t.Fatal("excelContext returned empty for a zero-file conversation — Excel from-scratch is gated off again")
	}
	if !strings.Contains(got, ":::excel-plan") {
		t.Errorf("headerless excel prompt must carry the :::excel-plan contract:\n%.300s", got)
	}
	if !strings.Contains(got, "create_workbook") {
		t.Errorf("headerless excel prompt must steer to create_workbook:\n%.300s", got)
	}
	if !strings.Contains(got, "OMIT source_file_id") {
		t.Errorf("headerless excel prompt must tell the model to omit source_file_id:\n%.300s", got)
	}
}

func TestExcelContext_FilesButNoWorkbook_KeepsHeaderless(t *testing.T) {
	img := model.ConversationFile{
		ID:               uuid.New(),
		ContentType:      "image/png",
		OriginalFilename: "photo.png",
	}
	s := docGateService([]model.ConversationFile{img}, nil)
	got := s.excelContext(context.Background(), uuid.New(), uuid.New(), true)
	if !strings.Contains(got, "No workbook is currently uploaded") {
		t.Errorf("files-but-no-workbook should return the headerless prompt, got:\n%.300s", got)
	}
}

func TestExcelContext_WithInspectedWorkbook_UnchangedFullBlock(t *testing.T) {
	// The upload path must NOT regress into the headerless prompt: an
	// attached workbook still gets its structure + file_id.
	inspection := `{"sheet_count": 2}`
	f := model.ConversationFile{
		ID:               uuid.New(),
		ContentType:      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		OriginalFilename: "sales.xlsx",
		InspectionJSON:   &inspection,
	}
	s := docGateService([]model.ConversationFile{f}, nil)
	got := s.excelContext(context.Background(), uuid.New(), uuid.New(), true)

	if !strings.Contains(got, "You have access to an Excel workbook") {
		t.Errorf("uploaded-workbook path must keep the full structure block:\n%.300s", got)
	}
	if !strings.Contains(got, f.ID.String()) {
		t.Errorf("uploaded-workbook path must embed the file_id:\n%.300s", got)
	}
	if strings.Contains(got, "No workbook is currently uploaded") {
		t.Errorf("uploaded-workbook path must NOT use the headerless preamble:\n%.300s", got)
	}
}
