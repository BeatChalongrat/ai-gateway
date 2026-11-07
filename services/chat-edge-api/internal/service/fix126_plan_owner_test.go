package service

// FIX126 F4 — a file generated from a plan card is linked to the assistant
// message that wrote the plan (PlanOwnerResolver), so GET /messages returns
// it on that message after a reload.

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func docPlanText(filename string) string {
	return "นี่คือแผนเอกสาร\n\n:::doc-plan\n{\"format\":\"docx\",\"output_filename\":\"" + filename + "\",\"actions\":[]}\n:::\n"
}

func TestFIX126_PlanOwnerIsTheMessageThatWroteThePlan(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	files := repository.NewFileRepo(pool)
	tenant, user := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "FIX126"})
	if err != nil {
		t.Fatalf("conv: %v", err)
	}
	other, err := cr.Create(ctx, &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "other"})
	if err != nil {
		t.Fatalf("conv: %v", err)
	}
	add := func(c *model.Conversation, role, content string) *model.Message {
		t.Helper()
		m, err := mr.Create(ctx, &model.Message{ConversationID: c.ID, TenantID: tenant, UserID: user,
			Role: role, ContentMasked: content, Status: "completed", IsActive: true})
		if err != nil {
			t.Fatalf("message: %v", err)
		}
		time.Sleep(5 * time.Millisecond)
		return m
	}
	add(conv, "user", "ทำรายงานให้หน่อย")
	planA := add(conv, "assistant", docPlanText("report_a.docx"))
	add(conv, "user", "อีกฉบับ")
	planB := add(conv, "assistant", docPlanText("report_b.docx"))
	add(conv, "assistant", "ข้อความธรรมดา ไม่มีแผน")
	foreign := add(other, "assistant", docPlanText("x.docx"))

	r := NewPlanOwnerResolver(mr)
	fence := PlanFenceForFormat("docx")
	cases := []struct {
		name      string
		requested uuid.UUID
		hint      string
		want      uuid.UUID
	}{
		{"newest plan when nothing names one", uuid.Nil, "", planB.ID},
		{"the plan that names the output file", uuid.Nil, "report_a.docx", planA.ID},
		{"the client's message id", planA.ID, "", planA.ID},
		{"another conversation's id is ignored", foreign.ID, "", planB.ID},
	}
	for _, c := range cases {
		got := r.Resolve(ctx, tenant, user, conv.ID, c.requested, fence, c.hint)
		if got == nil || *got != c.want {
			t.Fatalf("%s: owner = %v, want %s", c.name, got, c.want)
		}
	}
	if got := r.Resolve(ctx, tenant, user, conv.ID, uuid.Nil, PlanFenceForFormat("pptx"), ""); got != nil {
		t.Fatalf("no :::pptx-plan in the conversation, owner = %s", *got)
	}
	if got := r.Resolve(ctx, tenant, uuid.New(), conv.ID, uuid.Nil, fence, ""); got != nil {
		t.Fatalf("another user's conversation must not resolve: %s", *got)
	}

	// The generated row ExecutePlan writes carries the owner, and the
	// message listing's file grouping returns it on that message.
	owner := r.Resolve(ctx, tenant, user, conv.ID, uuid.Nil, fence, "report_a.docx")
	row, err := files.Create(ctx, &model.ConversationFile{
		ConversationID: conv.ID, MessageID: owner, TenantID: tenant, UserID: user,
		Filename: "f_report_a.docx", OriginalFilename: "report_a.docx",
		ContentType: docxContentType, FileSizeBytes: 10, StoragePath: "k/report_a.docx",
		ExtractionStatus: "completed", IsGenerated: true, Version: 1,
	})
	if err != nil {
		t.Fatalf("generated row: %v", err)
	}
	byMsg, err := files.ListByMessageIDs(ctx, tenant, []uuid.UUID{planA.ID, planB.ID})
	if err != nil {
		t.Fatalf("list by message: %v", err)
	}
	if got := byMsg[planA.ID]; len(got) != 1 || got[0].ID != row.ID || !got[0].IsGenerated {
		t.Fatalf("the plan message's files = %+v, want the generated report", got)
	}
	if len(byMsg[planB.ID]) != 0 {
		t.Fatalf("the other plan message got files: %+v", byMsg[planB.ID])
	}
}

func TestFIX126_PlanFenceForFormat(t *testing.T) {
	for in, want := range map[string]string{"docx": "doc-plan", "pdf": "doc-plan", "PPTX": "pptx-plan", "excel": "excel-plan", "": "doc-plan"} {
		if got := PlanFenceForFormat(in); got != want {
			t.Fatalf("%q → %q, want %q", in, got, want)
		}
	}
}
