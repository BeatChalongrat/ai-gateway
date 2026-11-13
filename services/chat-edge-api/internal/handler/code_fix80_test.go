package handler

// FIX80 — /code/run gains a delivery path: naming a conversation persists
// every produced artifact as a ConversationFile (ownership-checked) and
// returns each row's id, so the desktop's chart or .xlsx has a durable,
// downloadable home in the chat. Without a conversation the endpoint
// stays byte-identical to the old inline-and-discard behaviour. These
// tests go red if the persistence, the ids, or the ownership gate drop
// out.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type fix80Runner struct{ res *sandbox.Result }

func (f *fix80Runner) Execute(_ context.Context, _ sandbox.Spec) (*sandbox.Result, error) {
	return f.res, nil
}

type fix80Files struct {
	ownedConv  uuid.UUID
	saved      []service.GeneratedFileInput
	savedIDs   []uuid.UUID
	assertConv []uuid.UUID
	inputConv  uuid.UUID
	inputFile  uuid.UUID
	inputCalls int
}

func (f *fix80Files) OpenUserFileCopy(_ context.Context, _, _, _ uuid.UUID) (string, string, func(), error) {
	return "", "", func() {}, service.ErrFileNotFound
}
func (f *fix80Files) OpenConversationFileCopy(_ context.Context, _, _, convID, fileID uuid.UUID) (string, string, func(), error) {
	f.inputCalls++
	if convID != f.inputConv || fileID != f.inputFile {
		return "", "", nil, service.ErrFileNotFound
	}
	return "input.xlsx", "input.xlsx", func() {}, nil
}
func (f *fix80Files) AssertConversationOwned(_ context.Context, _, _, convID uuid.UUID) error {
	f.assertConv = append(f.assertConv, convID)
	if convID != f.ownedConv {
		return service.ErrFileConvNotFound
	}
	return nil
}
func (f *fix80Files) SaveGeneratedFile(_ context.Context, in service.GeneratedFileInput) (*model.ConversationFile, error) {
	f.saved = append(f.saved, in)
	id := uuid.New()
	f.savedIDs = append(f.savedIDs, id)
	return &model.ConversationFile{ID: id}, nil
}

func fix80Handler(t *testing.T, files *fix80Files) *CodeHandler {
	t.Helper()
	dir := t.TempDir()
	png := filepath.Join(dir, "chart.png")
	xlsx := filepath.Join(dir, "report.xlsx")
	if err := os.WriteFile(png, []byte("PNGBYTES"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(xlsx, []byte("XLSXBYTES"), 0o600); err != nil {
		t.Fatal(err)
	}
	runner := &fix80Runner{res: &sandbox.Result{
		Exit:   0,
		Stdout: []byte("ok"),
		Artifacts: []sandbox.Artifact{
			{Name: "chart.png", Path: png},
			{Name: "report.xlsx", Path: xlsx},
		},
	}}
	return NewCodeHandler(service.NewCodeInterpreter(runner), files)
}

func fix80Post(t *testing.T, h *CodeHandler, body string) (*httptest.ResponseRecorder, runCodeResponse) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/code/run", strings.NewReader(body))
	rec := httptest.NewRecorder()
	h.Run(rec, req)
	var resp runCodeResponse
	if rec.Code == http.StatusOK {
		if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
			t.Fatalf("bad response JSON: %v", err)
		}
	}
	return rec, resp
}

func TestCodeRun_ConversationNamed_PersistsEveryArtifactWithIDs(t *testing.T) {
	conv := uuid.New()
	files := &fix80Files{ownedConv: conv}
	h := fix80Handler(t, files)

	rec, resp := fix80Post(t, h, `{"code":"x","conversation_id":"`+conv.String()+`"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", rec.Code, rec.Body.String())
	}
	if len(files.saved) != 2 {
		t.Fatalf("expected BOTH artifacts persisted (png AND xlsx), got %d", len(files.saved))
	}
	for _, in := range files.saved {
		if in.ConversationID != conv {
			t.Errorf("persisted into conversation %s, want %s", in.ConversationID, conv)
		}
		if in.MessageID != uuid.Nil {
			t.Errorf("MessageID must stay nil (the desktop links it at import), got %s", in.MessageID)
		}
	}
	if len(resp.Images) != 1 || resp.Images[0].ID == "" {
		t.Errorf("image row must carry its ConversationFile id, got %+v", resp.Images)
	}
	if len(resp.Files) != 1 || resp.Files[0].ID == "" || resp.Files[0].Filename != "report.xlsx" {
		t.Errorf("file row must carry its ConversationFile id, got %+v", resp.Files)
	}
	if resp.Files[0].SizeBytes != len("XLSXBYTES") {
		t.Errorf("file size = %d, want %d", resp.Files[0].SizeBytes, len("XLSXBYTES"))
	}
}

func TestCodeRun_NoConversation_OldBehaviourNothingPersisted(t *testing.T) {
	files := &fix80Files{ownedConv: uuid.New()}
	h := fix80Handler(t, files)

	rec, resp := fix80Post(t, h, `{"code":"x"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	if len(files.saved) != 0 {
		t.Fatalf("nothing may be persisted without a conversation, got %d saves", len(files.saved))
	}
	if resp.Images[0].ID != "" || resp.Files[0].ID != "" {
		t.Errorf("rows must carry no ids without persistence: %+v %+v", resp.Images, resp.Files)
	}
	// non-image files are still LISTED (metadata) so the model can name them
	if resp.Files[0].Filename != "report.xlsx" {
		t.Errorf("files metadata missing: %+v", resp.Files)
	}
}

func TestCodeRun_ConversationGate_RefusesBadAndForeignIDs(t *testing.T) {
	files := &fix80Files{ownedConv: uuid.New()}
	h := fix80Handler(t, files)

	rec, _ := fix80Post(t, h, `{"code":"x","conversation_id":"not-a-uuid"}`)
	if rec.Code != http.StatusBadRequest {
		t.Errorf("malformed conversation_id: status = %d, want 400", rec.Code)
	}
	rec2, _ := fix80Post(t, h, `{"code":"x","conversation_id":"`+uuid.New().String()+`"}`)
	if rec2.Code != http.StatusNotFound {
		t.Errorf("foreign conversation: status = %d, want 404 (never persist into it)", rec2.Code)
	}
	if len(files.saved) != 0 {
		t.Fatalf("a refused conversation must persist nothing, got %d saves", len(files.saved))
	}
}

func TestCodeRun_InputsBelongToNamedConversation(t *testing.T) {
	conv, file := uuid.New(), uuid.New()
	files := &fix80Files{ownedConv: conv, inputConv: conv, inputFile: file}
	h := fix80Handler(t, files)
	rec, _ := fix80Post(t, h, `{"code":"edit()","conversation_id":"`+conv.String()+`","files":["`+file.String()+`","`+file.String()+`"]}`)
	if rec.Code != http.StatusOK || files.inputCalls != 1 {
		t.Fatalf("expected one scoped input and a successful run, status=%d calls=%d body=%s", rec.Code, files.inputCalls, rec.Body.String())
	}
	files.saved = nil
	rec, _ = fix80Post(t, h, `{"code":"edit()","conversation_id":"`+conv.String()+`","files":["`+uuid.New().String()+`"]}`)
	if rec.Code != http.StatusNotFound || len(files.saved) != 0 {
		t.Fatalf("foreign input must not produce artifacts, status=%d saved=%d", rec.Code, len(files.saved))
	}
}
