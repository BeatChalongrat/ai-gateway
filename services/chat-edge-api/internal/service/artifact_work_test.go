package service

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
	"github.com/google/uuid"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWorkspaceSelectionUTF16(t *testing.T) {
	text := "งาน 😀 today"
	before, after, e := selectionText(text, WorkSelection{Start: 4, End: 6, Text: "😀"})
	if e != nil || before != "งาน " || after != " today" {
		t.Fatalf("%q %q %v", before, after, e)
	}
	for _, sel := range []WorkSelection{{Start: 4, End: 5, Text: "😀"}, {Start: 5, End: 6, Text: "😀"}, {Start: -1, End: 1}, {Start: 0, End: 0}, {Start: 0, End: 3, Text: "wrong"}} {
		if _, _, e := selectionText(text, sel); e == nil {
			t.Fatal("accepted invalid selection", sel)
		}
	}
}

type workspaceMock struct {
	calls      int
	failVerify bool
}

func (m *workspaceMock) SendChatCompletion(_ context.Context, _ GatewayCallContext, r GatewayChatRequest) (*GatewayChatResponse, error) {
	m.calls++
	if m.failVerify && m.calls == 2 {
		return nil, errors.New("mock transient failure")
	}
	text := `{"replacement":"new"}`
	if r.VisualReview {
		a := VisualAssessment{Score: 95, Passed: true, Issues: []string{}, Changes: []FileEditChange{}}
		if m.calls == 1 {
			a.Score = 50
			a.Passed = false
			a.Issues = []string{"Small text"}
			var v VisualInspection
			blocks := r.Messages[1].Content.([]GatewayContentBlock)
			_ = json.Unmarshal([]byte(blocks[0].Text), &v)
			for _, f := range v.Fields {
				a.Changes = append(a.Changes, FileEditChange{ID: f.ID, Value: f.Value, Style: map[string]any{"size": 18.0}})
			}
		}
		b, _ := json.Marshal(a)
		text = string(b)
	}
	return &GatewayChatResponse{Model: "mock-vision", Choices: []GatewayChatChoice{{Message: GatewayMessage{Content: text}, FinishReason: "stop"}}}, nil
}
func (m *workspaceMock) GenerateImages(context.Context, GatewayCallContext, GatewayImagesRequest) (*GatewayImagesResponse, error) {
	return nil, errors.New("unexpected image call")
}

func TestWorkspaceRealStorageMockProviders(t *testing.T) {
	image := os.Getenv("VISUAL_REVIEW_SANDBOX")
	if image == "" {
		t.Skip("set local sandbox and converter for integration")
	}
	db := testdb.Fresh(t)
	ctx := context.Background()
	repo := repository.NewFileRepo(db)
	convs := repository.NewConversationRepo(db)
	local, e := storage.NewLocalBackend(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	svc := NewFileService(repo, convs, local, Knobs{})
	profile, _ := filepath.Abs("../../sandbox/seccomp.json")
	runner := sandbox.New(sandbox.Config{Image: image, SeccompPath: profile, WorkDir: t.TempDir()})
	svc.SetSandbox(runner)
	svc.SetOfficeQuality(NewOfficeQuality(os.Getenv("EDITOR_REVIEW_CONVERTER")))
	mock := &workspaceMock{}
	svc.visualGateway = mock
	svc.visualModel = "mock-vision"
	tenant, user := uuid.New(), uuid.New()
	conv, e := convs.Create(ctx, &model.Conversation{TenantID: tenant, UserID: user, ModelID: "mock-vision"})
	if e != nil {
		t.Fatal(e)
	}
	create := func(name string) *model.ConversationFile {
		t.Helper()
		raw := json.RawMessage(`{"title":"Workspace review","sections":[{"heading":"Facts","body":"Keep exact facts 123"},{"heading":"Actions","body":"Keep task 456"}]}`)
		result, e := svc.CreateWorkspaceTemplate(ctx, tenant, user, conv.ID, WorkspaceTemplateRequest{Template: name, Data: raw})
		if e != nil {
			t.Fatal(e)
		}
		return result.File
	}
	source := create("docx.business_report")
	raw, e := svc.sourceBytes(ctx, source)
	if e != nil {
		t.Fatal(e)
	}
	sha := visualHash(raw)
	t.Run("owned draft CAS", func(t *testing.T) {
		d := repository.FileDraft{SHA: sha, Body: json.RawMessage(`{"changes":{"p":"local"}}`)}
		saved, e := svc.ArtifactDraft(ctx, tenant, user, source.ID, "PUT", d)
		if e != nil || saved.Revision != 1 {
			t.Fatal(saved, e)
		}
		if _, e = svc.ArtifactDraft(ctx, tenant, user, source.ID, "PUT", d); !errors.Is(e, repository.ErrDraftConflict) {
			t.Fatal("missing conflict", e)
		}
		if _, e = svc.ArtifactDraft(ctx, tenant, uuid.New(), source.ID, "GET", d); !errors.Is(e, ErrFileNotFound) {
			t.Fatal("cross-owner access", e)
		}
		if _, e = svc.ArtifactDraft(ctx, tenant, user, source.ID, "DELETE", d); !errors.Is(e, repository.ErrDraftConflict) {
			t.Fatal("stale delete", e)
		}
		if _, e = svc.ArtifactDraft(ctx, tenant, user, source.ID, "DELETE", *saved); e != nil {
			t.Fatal(e)
		}
	})
	request, _ := json.Marshal(ArtifactWorkRequest{Action: "repair", SHA: sha})
	work, e := repo.CreateWork(ctx, tenant, user, source.ID, request)
	if e != nil {
		t.Fatal(e)
	}
	mock.failVerify = true
	if e = svc.runArtifactWork(ctx, work); e == nil {
		t.Fatal("expected mocked verify failure")
	}
	if mock.calls != 2 {
		t.Fatal(mock.calls)
	}
	work.Status = "failed"
	work.Error = "mock transient failure"
	if e = repo.SaveWork(ctx, work, "running"); e != nil {
		t.Fatal(e)
	}
	// Resume from persisted checkpoint: no second correction or first review.
	work, e = repo.GetWork(ctx, tenant, user, work.ID)
	if e != nil {
		t.Fatal(e)
	}
	if e = repo.WorkTransition(ctx, tenant, user, work.ID, "failed", "running"); e != nil {
		t.Fatal(e)
	}
	work.Status = "running"
	work.Error = ""
	mock.failVerify = false
	if e = svc.runArtifactWork(ctx, work); e != nil {
		t.Fatal(e)
	}
	if mock.calls != 3 {
		t.Fatalf("replayed completed model stage: %d", mock.calls)
	}
	view, e := svc.GetArtifactWork(ctx, tenant, user, work.ID)
	if e != nil || view.Status != "ready" || len(view.Changes) < 2 {
		t.Fatalf("proposal %+v %v", view, e)
	}
	versions, e := repo.ListVersions(ctx, tenant, source.ID)
	if e != nil || len(versions) != 1 {
		t.Fatal("published before acceptance", len(versions), e)
	}
	if _, e = svc.GetArtifactWork(ctx, tenant, uuid.New(), work.ID); !errors.Is(e, ErrFileNotFound) {
		t.Fatal("cross-owner proposal", e)
	}
	if _, e = svc.ControlArtifactWork(ctx, tenant, user, work.ID, "accept", []string{"unknown"}); !errors.Is(e, ErrEditorInput) {
		t.Fatal(e)
	}
	accepted, e := svc.ControlArtifactWork(ctx, tenant, user, work.ID, "accept", []string{view.Changes[0].ID})
	if e != nil {
		t.Fatal(e)
	}
	again, e := svc.ControlArtifactWork(ctx, tenant, user, work.ID, "accept", []string{view.Changes[0].ID})
	if e != nil || again.File.ID != accepted.File.ID {
		t.Fatal("non-idempotent accept", e)
	}
	v, e := svc.EditFile(ctx, tenant, user, accepted.File.ID, FileEditRequest{Mode: "visual_inspect"})
	if e != nil {
		t.Fatal(e)
	}
	for _, f := range v.Visual.Fields {
		if f.ID == view.Changes[0].ID {
			if f.Style["size"] != 18.0 {
				t.Fatal("selected format not applied", f)
			}
		} else {
			for _, d := range view.Changes[1:] {
				if f.ID == d.ID && f.Style["size"] == 18.0 {
					t.Fatal("unselected change applied")
				}
			}
		}
	}
	library, e := svc.ArtifactLibrary(ctx, tenant, user, "Workspace", "docx", 0)
	if e != nil || len(library) != 1 || library[0].ID != accepted.File.ID {
		t.Fatal("library not latest-only", library, e)
	}
	other, e := svc.ArtifactLibrary(ctx, tenant, uuid.New(), "", "", 0)
	if e != nil || len(other) != 0 {
		t.Fatal("library ownership", e)
	}
	// Cancel wins over a worker's late update, including candidate bytes.
	source2 := create("docx.business_report")
	raw2, _ := svc.sourceBytes(ctx, source2)
	req2, _ := json.Marshal(ArtifactWorkRequest{Action: "repair", SHA: visualHash(raw2)})
	cancelled, e := repo.CreateWork(ctx, tenant, user, source2.ID, req2)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = svc.ControlArtifactWork(ctx, tenant, user, cancelled.ID, "cancel", nil); e != nil {
		t.Fatal(e)
	}
	if e = repo.SaveWork(ctx, cancelled, "running"); !errors.Is(e, context.Canceled) {
		t.Fatal("late worker won", e)
	}
	latest, e := repo.GetWork(ctx, tenant, user, cancelled.ID)
	if e != nil || latest.Status != "cancelled" || len(latest.Candidate) != 0 {
		t.Fatal("candidate retained", e)
	}
	for _, template := range []string{"pdf.business_report", "docx.business_report", "pptx.project_update"} {
		t.Run("selection rewrite "+template, func(t *testing.T) {
			pdf := create(template)
			inspected, err := svc.EditFile(ctx, tenant, user, pdf.ID, FileEditRequest{Mode: "visual_inspect"})
			if err != nil {
				t.Fatal(err)
			}
			var field VisualField
			for _, f := range inspected.Visual.Fields {
				if strings.Contains(f.Value, "exact") {
					field = f
					break
				}
			}
			start := strings.Index(field.Value, "exact")
			if start < 0 {
				t.Fatal("fixture text missing")
			}
			request, _ := json.Marshal(ArtifactWorkRequest{Action: "rewrite", SHA: inspected.Visual.SHA256, Instruction: "replace selected adjective", Selection: &WorkSelection{ID: field.ID, Start: start, End: start + 5, Text: "exact"}})
			job, err := repo.CreateWork(ctx, tenant, user, pdf.ID, request)
			if err != nil {
				t.Fatal(err)
			}
			if err = svc.runArtifactWork(ctx, job); err != nil {
				t.Fatal(err)
			}
			proposal, err := svc.GetArtifactWork(ctx, tenant, user, job.ID)
			if err != nil || proposal.Status != "ready" || len(proposal.Changes) != 1 {
				t.Fatal(proposal, err)
			}
			if proposal.Changes[0].ID != field.ID || proposal.Changes[0].After.Value != strings.Replace(field.Value, "exact", "new", 1) {
				t.Fatal("selection boundary changed", proposal.Changes)
			}
			accepted, err := svc.ControlArtifactWork(ctx, tenant, user, job.ID, "accept", []string{field.ID})
			if err != nil {
				t.Fatal(err)
			}
			result, err := svc.EditFile(ctx, tenant, user, accepted.File.ID, FileEditRequest{Mode: "visual_inspect"})
			if err != nil {
				t.Fatal(err)
			}
			var text string
			for _, f := range result.Visual.Fields {
				text += f.Value + "\n"
				if template != "pdf.business_report" && f.ID == field.ID && f.Style["size"] != field.Style["size"] {
					t.Fatal("rewrite changed font size", f.Style, field.Style)
				}
			}
			if template == "pptx.project_update" && len(proposal.Before) != inspected.Visual.PageCount {
				t.Fatal("rewrite preview omitted slides")
			}
			if !strings.Contains(text, "Keep new facts 123") || strings.Contains(text, "Keep exact facts 123") {
				t.Fatal("selected text not replaced", text)
			}
		})
	}
}
