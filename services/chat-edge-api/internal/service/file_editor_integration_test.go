package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
	"github.com/google/uuid"
)

func TestFileRevisionConcurrentSaveAndOwnership(t *testing.T) {
	db := testdb.Fresh(t)
	ctx := context.Background()
	repo := repository.NewFileRepo(db)
	convs := repository.NewConversationRepo(db)
	tenant, user := uuid.New(), uuid.New()
	conv, err := convs.Create(ctx, &model.Conversation{TenantID: tenant, UserID: user, ModelID: "test"})
	if err != nil {
		t.Fatal(err)
	}
	parent, err := repo.Create(ctx, &model.ConversationFile{TenantID: tenant, UserID: user, ConversationID: conv.ID, Filename: "original.txt", OriginalFilename: "original.txt", ContentType: "text/plain", StoragePath: "original"})
	if err != nil {
		t.Fatal(err)
	}
	makeRow := func() *model.ConversationFile {
		return &model.ConversationFile{TenantID: tenant, UserID: user, ConversationID: conv.ID, Filename: uuid.NewString() + ".txt", OriginalFilename: "original.txt", ContentType: "text/plain", StoragePath: "revision"}
	}
	bad := makeRow()
	bad.UserID = uuid.New()
	if _, err := repo.CreateRevision(ctx, parent.ID, bad); err == nil {
		t.Fatal("cross-user revision accepted")
	}
	results := make(chan error, 2)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); <-start; _, e := repo.CreateRevision(ctx, parent.ID, makeRow()); results <- e }()
	}
	close(start)
	wg.Wait()
	close(results)
	success, conflicts := 0, 0
	for err := range results {
		if err == nil {
			success++
		} else if errors.Is(err, repository.ErrFileRevisionConflict) {
			conflicts++
		} else {
			t.Fatal(err)
		}
	}
	if success != 1 || conflicts != 1 {
		t.Fatalf("success=%d conflicts=%d", success, conflicts)
	}
	versions, err := repo.ListVersions(ctx, tenant, parent.ID)
	if err != nil || len(versions) != 2 {
		t.Fatalf("versions=%v error=%v", versions, err)
	}
}

func TestFileEditorRealSandboxLifecycle(t *testing.T) {
	image := os.Getenv("EDITOR_REVIEW_SANDBOX")
	if image == "" {
		t.Skip("set EDITOR_REVIEW_SANDBOX to run real sandbox integration")
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
	profile, err := filepath.Abs("../../sandbox/seccomp.json")
	if err != nil {
		t.Fatal(err)
	}
	svc.SetSandbox(sandbox.New(sandbox.Config{Image: image, SeccompPath: profile, WorkDir: t.TempDir()}))
	tenant, user := uuid.New(), uuid.New()
	conv, err := convs.Create(ctx, &model.Conversation{TenantID: tenant, UserID: user, ModelID: "test"})
	if err != nil {
		t.Fatal(err)
	}
	original, err := svc.SaveGeneratedFile(ctx, GeneratedFileInput{TenantID: tenant, UserID: user, ConversationID: conv.ID, OriginalFilename: "note.txt", ContentType: "text/plain", Data: []byte("original")})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := svc.EditFile(ctx, tenant, uuid.New(), original.ID, FileEditRequest{Mode: "inspect"}); !errors.Is(err, ErrFileNotFound) {
		t.Fatalf("ownership: %v", err)
	}
	if _, err := svc.EditFile(ctx, tenant, user, original.ID, FileEditRequest{Mode: "inspect", ConversationID: uuid.New()}); !errors.Is(err, ErrFileNotFound) {
		t.Fatalf("conversation: %v", err)
	}
	inspection, err := svc.EditFile(ctx, tenant, user, original.ID, FileEditRequest{Mode: "inspect"})
	if err != nil {
		t.Fatal(err)
	}
	var data struct {
		SHA string `json:"sha256"`
	}
	if err = json.Unmarshal(inspection.Inspection, &data); err != nil {
		t.Fatal(err)
	}
	edited, err := svc.EditFile(ctx, tenant, user, original.ID, FileEditRequest{Mode: "edit", SHA256: data.SHA, Changes: []FileEditChange{{ID: "content", Value: "updated", Type: "text"}}})
	if err != nil {
		t.Fatal(err)
	}
	if edited.File.Version != 2 || edited.File.ParentFileID == nil || *edited.File.ParentFileID != original.ID {
		t.Fatal("missing lineage")
	}
	restored, err := svc.EditFile(ctx, tenant, user, edited.File.ID, FileEditRequest{Mode: "restore", TargetID: original.ID})
	if err != nil {
		t.Fatal(err)
	}
	if restored.File.Version != 3 {
		t.Fatal("restore overwrote a version")
	}
	for _, v := range []*model.ConversationFile{original, edited.File, restored.File} {
		path, cleanup, e := store.OpenLocalCopy(ctx, v.StoragePath)
		if e != nil {
			t.Fatal(e)
		}
		raw, e := os.ReadFile(path)
		cleanup()
		if e != nil {
			t.Fatal(e)
		}
		want := "original"
		if v.ID == edited.File.ID {
			want = "updated"
		}
		if string(raw) != want {
			t.Fatalf("version %d: %q", v.Version, raw)
		}
	}
	history, err := svc.EditFile(ctx, tenant, user, restored.File.ID, FileEditRequest{Mode: "history"})
	if err != nil || len(history.Versions) != 3 {
		t.Fatalf("history: %v %v", history, err)
	}
	// Wait for the same ingestion gate used by chat sends before dropping its DB.
	for _, v := range []*model.ConversationFile{edited.File, restored.File} {
		deadline := time.Now().Add(30 * time.Second)
		for {
			row, e := svc.Get(ctx, tenant, user, v.ID)
			if e != nil {
				t.Fatal(e)
			}
			if row.ExtractionStatus == "completed" {
				if row.ExtractedText == nil {
					t.Fatal("revision was not reingested")
				}
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("revision ingestion did not finish")
			}
			time.Sleep(100 * time.Millisecond)
		}
	}
}

func TestOfficeRepairRealRender(t *testing.T) {
	image, url := os.Getenv("EDITOR_REVIEW_SANDBOX"), os.Getenv("EDITOR_REVIEW_CONVERTER")
	if image == "" || url == "" {
		t.Skip("set review sandbox and converter to run visual integration")
	}
	ctx := context.Background()
	profile, _ := filepath.Abs("../../sandbox/seccomp.json")
	runner := sandbox.New(sandbox.Config{Image: image, SeccompPath: profile, WorkDir: t.TempDir()})
	res, err := runner.Execute(ctx, sandbox.Spec{Mode: sandbox.ModeCode, Code: `from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
p=Presentation();s=p.slides.add_slide(p.slide_layouts[6])
box=s.shapes.add_textbox(-Inches(.15),0,Inches(9),Inches(1))
run=box.text_frame.paragraphs[0].add_run();run.text='Quarterly results and next steps';run.font.size=Pt(48);run.font.color.rgb=RGBColor(0,0,0)
p.save('/job/out/edge.pptx')
`})
	if err != nil {
		t.Fatal(err)
	}
	defer res.Cleanup()
	art := res.Artifact("edge.pptx")
	if art == nil {
		t.Fatalf("sandbox: %s", res.Stderr)
	}
	raw, err := ReadArtifact(art.Path)
	if err != nil {
		t.Fatal(err)
	}
	q := NewOfficeQuality(url)
	q.SetRepairRunner(runner)
	before, err := q.reviewOnce(ctx, "edge.pptx", raw)
	if err != nil {
		t.Fatal(err)
	}
	after, err := q.review(ctx, "edge.pptx", raw)
	if err != nil {
		t.Fatal(err)
	}
	if !after.Report.Repaired || after.Report.EdgeInk >= before.Report.EdgeInk || after.Report.Pages != before.Report.Pages {
		t.Fatalf("before=%+v after=%+v", before.Report, after.Report)
	}
	t.Logf("edge ink %.5f -> %.5f; attempts %d", before.Report.EdgeInk, after.Report.EdgeInk, after.Report.RepairAttempts)
	if dir := os.Getenv("EDITOR_REVIEW_OUTPUT"); dir != "" {
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
		for name, raw := range map[string][]byte{"before.png": before.PNG, "after.png": after.PNG, "before.pptx": before.Data, "after.pptx": after.Data} {
			if err := os.WriteFile(filepath.Join(dir, name), raw, 0600); err != nil {
				t.Fatal(err)
			}
		}
	}
}
