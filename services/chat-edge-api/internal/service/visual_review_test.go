package service

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
	"github.com/google/uuid"
)

func TestVisualAssessmentStrictSchema(t *testing.T) {
	for _, raw := range []string{`{}`, `null`, `{"score":100,"passed":true,"issues":[],"changes":[],"image_prompt":"","code":"evil"}`, `{"score":101,"passed":true,"issues":[],"changes":[],"image_prompt":""}`, `{"score":100,"passed":true,"issues":["still clipped"],"changes":[],"image_prompt":""}`, `{"score":100,"passed":true,"issues":[],"changes":[],"image_prompt":""} {}`} {
		if _, err := decodeVisualAssessment(raw); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
	if _, err := decodeVisualAssessment(`{"score":95,"passed":true,"issues":[],"changes":[],"image_prompt":""}`); err != nil {
		t.Fatal(err)
	}
	view := &VisualInspection{Kind: "docx", Fields: []VisualField{{ID: "p", Value: "exact"}}}
	for _, change := range []FileEditChange{{ID: "p", Value: "changed"}, {ID: "missing", Value: "exact"}, {ID: "p", Value: "exact", Box: &VisualBox{Width: 20}}} {
		if validateVisualChanges(view, VisualAssessment{Changes: []FileEditChange{change}}) == nil {
			t.Fatal("unsafe AI change accepted")
		}
	}
}

type mockedVisualGateway struct {
	t             *testing.T
	calls, images int
	worse         bool
	image         []byte
}

func (m *mockedVisualGateway) SendChatCompletion(_ context.Context, call GatewayCallContext, req GatewayChatRequest) (*GatewayChatResponse, error) {
	m.t.Helper()
	m.calls++
	if !req.VisualReview || call.TenantID == uuid.Nil || req.Model != "mock-vision" {
		m.t.Fatal("missing review policy or attribution")
	}
	blocks, ok := req.Messages[1].Content.([]GatewayContentBlock)
	if !ok {
		m.t.Fatal("no multimodal content")
	}
	pages := 0
	for _, b := range blocks {
		if b.ImageURL != nil {
			pages++
			if !strings.HasPrefix(b.ImageURL.URL, "data:image/") {
				m.t.Fatal("external/unrendered image")
			}
		}
	}
	if pages < 1 {
		m.t.Fatal("no actual rendered pages")
	}
	var metadata VisualInspection
	if err := json.Unmarshal([]byte(blocks[0].Text), &metadata); err != nil {
		m.t.Fatal(err)
	}
	a := VisualAssessment{Score: 60, Issues: []string{"Text is too small"}, Changes: []FileEditChange{}}
	if m.calls == 1 {
		if metadata.Kind == "image" {
			a.ImagePrompt = "Improve contrast while preserving subject and transparency"
		} else {
			for _, f := range metadata.Fields {
				if f.Value != "" {
					a.Changes = []FileEditChange{{ID: f.ID, Value: f.Value, Style: map[string]any{"size": 24.0}}}
					break
				}
			}
		}
	} else {
		a.Score = 95
		a.Passed = true
		a.Issues = []string{}
		if m.worse {
			a.Score = 50
			a.Passed = false
			a.Issues = []string{"Still clipped"}
		}
	}
	raw, _ := json.Marshal(a)
	return &GatewayChatResponse{Model: "mock-vision", Choices: []GatewayChatChoice{{Message: GatewayMessage{Content: string(raw)}, FinishReason: "stop"}}}, nil
}
func (m *mockedVisualGateway) GenerateImages(_ context.Context, _ GatewayCallContext, req GatewayImagesRequest) (*GatewayImagesResponse, error) {
	m.images++
	if len(req.SourceImages) != 1 || !strings.HasPrefix(req.SourceImages[0], "data:image/png;base64,") {
		m.t.Fatal("missing actual image reference")
	}
	return &GatewayImagesResponse{Data: []GatewayImageDatum{{B64JSON: base64.StdEncoding.EncodeToString(m.image)}}}, nil
}

// Real isolated rendering/storage/revisions, mocked vision and image providers.
// This test never requires or reads a provider key.
func TestVisualEditorRealSandboxAndMockedAI(t *testing.T) {
	image := os.Getenv("VISUAL_REVIEW_SANDBOX")
	if image == "" {
		t.Skip("set VISUAL_REVIEW_SANDBOX and EDITOR_REVIEW_CONVERTER for local rendering integration")
	}
	db := testdb.Fresh(t)
	ctx := context.Background()
	repo := repository.NewFileRepo(db)
	convs := repository.NewConversationRepo(db)
	store, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	svc := NewFileService(repo, convs, store, Knobs{})
	profile, _ := filepath.Abs("../../sandbox/seccomp.json")
	runner := sandbox.New(sandbox.Config{Image: image, SeccompPath: profile, WorkDir: t.TempDir()})
	svc.SetSandbox(runner)
	converter := os.Getenv("EDITOR_REVIEW_CONVERTER")
	if converter == "" {
		t.Fatal("local converter required")
	}
	svc.SetOfficeQuality(NewOfficeQuality(converter))
	fixture, err := runner.Execute(ctx, sandbox.Spec{Mode: sandbox.ModeCode, Code: `from docx import Document
from pptx import Presentation
from pptx.util import Inches
from reportlab.pdfgen import canvas
from PIL import Image,ImageDraw
d=Document();d.add_paragraph('Keep exact facts 123');d.save('/job/out/source.docx')
p=Presentation();s=p.slides.add_slide(p.slide_layouts[6]);s.shapes.add_textbox(Inches(1),Inches(1),Inches(6),Inches(2)).text='Keep exact facts 123';p.save('/job/out/source.pptx')
c=canvas.Canvas('/job/out/source.pdf');c.drawString(72,700,'Original PDF');c.drawString(72,650,'Keep searchable');c.save()
for name,color in [('source',(80,80,80,128)),('candidate',(160,160,160,128))]:
 im=Image.new('RGBA',(120,80),color);ImageDraw.Draw(im).rectangle((20,20,60,60),fill=(220,30,30,128));im.save('/job/out/'+name+'.png')
`})
	if err != nil {
		t.Fatal(err)
	}
	defer fixture.Cleanup()
	if fixture.Exit != 0 {
		t.Fatalf("fixture: %s", fixture.Stderr)
	}
	bytes := func(name string) []byte {
		t.Helper()
		art := fixture.Artifact(name)
		if art == nil {
			t.Fatal("missing fixture", name)
		}
		raw, e := ReadArtifact(art.Path)
		if e != nil {
			t.Fatal(e)
		}
		return raw
	}
	tenant, user := uuid.New(), uuid.New()
	conv, err := convs.Create(ctx, &model.Conversation{TenantID: tenant, UserID: user, ModelID: "mock-vision"})
	if err != nil {
		t.Fatal(err)
	}
	save := func(name string) *model.ConversationFile {
		t.Helper()
		f, e := svc.SaveGeneratedFile(ctx, GeneratedFileInput{TenantID: tenant, UserID: user, ConversationID: conv.ID, OriginalFilename: name, ContentType: map[string]string{".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pdf": "application/pdf", ".png": "image/png"}[filepath.Ext(name)], Data: bytes(name)})
		if e != nil {
			t.Fatal(e)
		}
		return f
	}
	edit := func(f *model.ConversationFile, req FileEditRequest) *FileEditResult {
		t.Helper()
		r, e := svc.EditFile(ctx, tenant, user, f.ID, req)
		if e != nil {
			t.Fatal(e)
		}
		return r
	}
	// Wait for asynchronous revision extraction before the isolated DB is dropped.
	var revisions []*model.ConversationFile
	defer func() {
		for _, f := range revisions {
			deadline := time.Now().Add(40 * time.Second)
			for {
				row, e := svc.Get(ctx, tenant, user, f.ID)
				if e != nil {
					t.Error(e)
					break
				}
				if row.ExtractionStatus != "processing" && row.ExtractionStatus != "pending" {
					break
				}
				if time.Now().After(deadline) {
					t.Error("extraction did not settle")
					break
				}
				time.Sleep(100 * time.Millisecond)
			}
		}
	}()
	t.Run("native PDF and immutable history", func(t *testing.T) {
		original := save("source.pdf")
		view := edit(original, FileEditRequest{Mode: "visual_inspect"}).Visual
		var target VisualField
		for _, field := range view.Fields {
			if strings.Contains(field.Value, "Original") {
				target = field
				break
			}
		}
		if target.ID == "" {
			t.Fatal("no native PDF field")
		}
		result := edit(original, FileEditRequest{Mode: "visual_edit", SHA256: view.SHA256, Changes: []FileEditChange{{ID: target.ID, Value: "Edited PDF", Box: &VisualBox{Left: 72, Top: target.Box.Top, Width: 260, Height: 30}}}})
		revisions = append(revisions, result.File)
		if result.File.Version != 2 {
			t.Fatal("not a revision")
		}
		next := edit(result.File, FileEditRequest{Mode: "visual_inspect"}).Visual
		joined := ""
		for _, field := range next.Fields {
			joined += field.Value
		}
		if strings.Contains(joined, "Original PDF") || !strings.Contains(joined, "Edited PDF") || !strings.Contains(joined, "Keep searchable") {
			t.Fatal(joined)
		}
		// Old versions remain renderable; preview is read-only and needs no SHA.
		if len(edit(original, FileEditRequest{Mode: "visual_preview"}).Visual.Pages) != 1 {
			t.Fatal("missing old revision preview")
		}
	})
	for _, tc := range []struct {
		name  string
		worse bool
	}{{"source.docx", false}, {"source.pptx", false}, {"source.png", false}, {"source.docx", true}} {
		t.Run(fmt.Sprintf("%s/worse=%v", tc.name, tc.worse), func(t *testing.T) {
			original := save(tc.name)
			mock := &mockedVisualGateway{t: t, worse: tc.worse, image: bytes("candidate.png")}
			svc.SetVisualGateway(mock, "mock-vision", "mock-image")
			view := edit(original, FileEditRequest{Mode: "visual_inspect"}).Visual
			if !view.AIAvailable {
				t.Fatal("review unavailable")
			}
			if tc.name == "source.pptx" && view.Background == "" {
				t.Fatal("no rendered slide background")
			}
			result := edit(original, FileEditRequest{Mode: "ai_repair", SHA256: view.SHA256})
			if mock.calls != 2 {
				t.Fatalf("vision calls %d", mock.calls)
			}
			if tc.worse {
				if result.File != nil || result.Review.Status != "unchanged" {
					t.Fatal("unverified candidate published")
				}
				history := edit(original, FileEditRequest{Mode: "history"})
				if len(history.Versions) != 1 {
					t.Fatal("declined repair created revision")
				}
				return
			}
			if result.File == nil || result.Review.Status != "repaired" || result.File.Version != 2 {
				t.Fatalf("not repaired: %+v", result)
			}
			revisions = append(revisions, result.File)
			next := edit(result.File, FileEditRequest{Mode: "visual_inspect"}).Visual
			if view.Kind != "image" && !sameVisualText(view, next) {
				t.Fatal("AI changed original text")
			}
			if view.Kind == "image" && (mock.images != 1 || !next.Transparent) {
				t.Fatal("reference edit/transparency lost")
			}
			before := mock.calls
			if _, e := svc.EditFile(ctx, tenant, user, original.ID, FileEditRequest{Mode: "ai_repair", SHA256: view.SHA256}); e == nil || mock.calls != before {
				t.Fatal("stale revision invoked paid model")
			}
		})
	}
}
