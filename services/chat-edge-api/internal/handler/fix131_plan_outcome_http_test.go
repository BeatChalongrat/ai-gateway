package handler

// FIX131 A3/A4 — after a reload, GET /messages still says what a plan card's
// execution did: a failure rides the plan's message as plan_outcomes (the
// card shows "ไม่สำเร็จ — <reason>" + retry, not a pristine "สร้างเอกสาร"),
// the unreadable input says why on its own chip, and a generated file names
// the file it was made from.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type fix131ListBody struct {
	Messages []struct {
		ID           string `json:"id"`
		Role         string `json:"role"`
		PlanOutcomes []struct {
			Fence          string `json:"fence"`
			Format         string `json:"format"`
			OutputFilename string `json:"output_filename"`
			Status         string `json:"status"`
			Reason         string `json:"reason"`
			SourceFileID   string `json:"source_file_id"`
			SourceFilename string `json:"source_filename"`
		} `json:"plan_outcomes"`
		Files []struct {
			ID               string `json:"id"`
			Filename         string `json:"filename"`
			UnreadableReason string `json:"unreadable_reason"`
			MadeFrom         *struct {
				ID       string `json:"id"`
				Filename string `json:"filename"`
				Role     string `json:"role"`
			} `json:"made_from"`
		} `json:"files"`
	} `json:"messages"`
}

func (s *fix131Stack) list(t *testing.T, outcomes service.PlanOutcomeStore) fix131ListBody {
	t.Helper()
	h := NewMessageHandler(s.messages)
	h.SetFileRepo(s.files)
	h.SetPlanOutcomeStore(outcomes)
	rec := httptest.NewRecorder()
	h.List(rec, s.request(http.MethodGet, "/api/v1/conversations/"+s.conv.ID.String()+"/messages", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("list: %d %s", rec.Code, rec.Body.String())
	}
	var out fix131ListBody
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("list body: %v", err)
	}
	return out
}

func (s *fix131Stack) attach(t *testing.T, msg *model.Message, files ...*model.ConversationFile) {
	t.Helper()
	ids := make([]uuid.UUID, 0, len(files))
	for _, f := range files {
		ids = append(ids, f.ID)
	}
	if n, err := s.files.AttachToMessage(context.Background(), s.tenant, s.conv.ID, msg.ID, ids); err != nil || n != int64(len(ids)) {
		t.Fatalf("attach: %d %v", n, err)
	}
}

const fix131PlanMessage = "สรุปเป็นไฟล์ Word ให้แล้ว\n\n:::doc-plan\n{\"format\":\"docx\",\"output_filename\":\"summary.docx\",\"actions\":[]}\n:::\n"

func TestFIX131_AFailedExecutionSurvivesAReload(t *testing.T) {
	s := newFix131Stack(t)
	outcomes := service.NewMemoryPlanOutcomeStore()
	s.docs.SetPlanOutcomeStore(outcomes)

	userMsg := s.message(t, "user", "ช่วยสรุป PDF นี้เป็น Word")
	locked := s.file(t, "locked.pdf", "application/pdf", "failed", fix131Placeholder("locked.pdf", "encrypted"))
	s.attach(t, userMsg, locked)
	plan := s.message(t, "assistant", fix131PlanMessage)

	code, _, raw := s.executeDoc(t, map[string]any{"source_file_id": locked.ID.String(), "format": "docx",
		"output_filename": "summary.docx", "plan": fix131DocPlan, "message_id": plan.ID.String()})
	if code != http.StatusUnprocessableEntity {
		t.Fatalf("execute: %d %s", code, raw)
	}

	// The reload.
	body := s.list(t, outcomes)
	var sawPlan, sawFile bool
	for _, m := range body.Messages {
		switch m.ID {
		case plan.ID.String():
			sawPlan = true
			if len(m.PlanOutcomes) != 1 {
				t.Fatalf("the plan message must carry its failed execution: %+v", m.PlanOutcomes)
			}
			o := m.PlanOutcomes[0]
			if o.Fence != "doc-plan" || o.Format != "docx" || o.OutputFilename != "summary.docx" || o.Status != "failed" ||
				o.Reason != "source_encrypted" || o.SourceFileID != locked.ID.String() || o.SourceFilename != "locked.pdf" {
				t.Fatalf("outcome = %+v", o)
			}
		case userMsg.ID.String():
			for _, f := range m.Files {
				if f.ID == locked.ID.String() {
					sawFile = true
					if f.UnreadableReason != "encrypted" {
						t.Fatalf("the locked PDF's chip must say why: %q", f.UnreadableReason)
					}
				}
			}
		default:
			if len(m.PlanOutcomes) > 0 {
				t.Fatalf("an outcome landed on another message: %s", m.ID)
			}
		}
	}
	if !sawPlan || !sawFile {
		t.Fatalf("list lost a message (plan %v, file %v)", sawPlan, sawFile)
	}

	// A later success clears it.
	owner := plan.ID
	recordPlanOutcome(context.Background(), outcomes, s.conv.ID, &owner, "docx", "summary.docx", locked.ID, nil)
	for _, m := range s.list(t, outcomes).Messages {
		if len(m.PlanOutcomes) > 0 {
			t.Fatalf("a success must clear the failure: %+v", m.PlanOutcomes)
		}
	}
}

func TestFIX131_NoOwnerNothingKept(t *testing.T) {
	// No message carries the plan's fence → no owner → nothing is kept
	// (the same rule as the generated file's link).
	s := newFix131Stack(t)
	outcomes := service.NewMemoryPlanOutcomeStore()
	s.docs.SetPlanOutcomeStore(outcomes)
	locked := s.file(t, "locked.pdf", "application/pdf", "failed", fix131Placeholder("locked.pdf", "encrypted"))
	if code, _, _ := s.executeDoc(t, map[string]any{"source_file_id": locked.ID.String(), "format": "docx", "plan": fix131DocPlan}); code != 422 {
		t.Fatalf("execute: %d", code)
	}
	if got := outcomes.List(context.Background(), s.conv.ID); len(got) != 0 {
		t.Fatalf("an unowned failure was kept: %+v", got)
	}
}

func TestFIX131_GeneratedFileNamesWhatItWasMadeFrom(t *testing.T) {
	s := newFix131Stack(t)
	userMsg := s.message(t, "user", "ทำเป็น Word")
	pdf := s.file(t, "fixture.pdf", "application/pdf", "completed", fix131Text("Quarterly"))
	docx := s.file(t, "draft.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "completed", fix131Text("Draft"))
	s.attach(t, userMsg, pdf, docx)
	plan := s.message(t, "assistant", fix131PlanMessage)
	gen := func(name string, parent *model.ConversationFile) *model.ConversationFile {
		pid := parent.ID
		row, err := s.files.Create(context.Background(), &model.ConversationFile{ConversationID: s.conv.ID, MessageID: &plan.ID,
			TenantID: s.tenant, UserID: s.user, Filename: name, OriginalFilename: name,
			ContentType:   "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
			FileSizeBytes: 10, StoragePath: "k/" + name, ExtractionStatus: "completed", IsGenerated: true, ParentFileID: &pid, Version: 2})
		if err != nil {
			t.Fatalf("generated row: %v", err)
		}
		return row
	}
	fromPDF := gen("summary.docx", pdf)
	edited := gen("draft_edited.docx", docx)

	var checked int
	for _, m := range s.list(t, nil).Messages {
		for _, f := range m.Files {
			switch f.ID {
			case fromPDF.ID.String():
				checked++
				if f.MadeFrom == nil || f.MadeFrom.Filename != "fixture.pdf" || f.MadeFrom.Role != "input" || f.MadeFrom.ID != pdf.ID.String() {
					t.Fatalf("summary.docx made_from = %+v, want fixture.pdf (input)", f.MadeFrom)
				}
			case edited.ID.String():
				checked++
				if f.MadeFrom == nil || f.MadeFrom.Filename != "draft.docx" || f.MadeFrom.Role != "edit" {
					t.Fatalf("draft_edited.docx made_from = %+v, want draft.docx (edit)", f.MadeFrom)
				}
			case pdf.ID.String(), docx.ID.String():
				if f.MadeFrom != nil || f.UnreadableReason != "" {
					t.Fatalf("an upload has no provenance / is readable: %+v", f)
				}
			}
		}
	}
	if checked != 2 {
		t.Fatalf("generated files not listed (%d)", checked)
	}
}
