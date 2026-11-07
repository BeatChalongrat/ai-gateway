package service

// Phase 8 Task 3 — model-driven canvas updates.
//
// Two things must hold no matter what the model sends:
//   * it can only touch a lineage that ALREADY exists in THIS
//     conversation (no inventing keys, no cross-conversation writes —
//     the service's ownership assert is the gate), and
//   * it cannot change an artifact's TYPE, because the type selects the
//     renderer and therefore the sandbox posture on the client.

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// fakeCanvasUpdater stands in for *CanvasArtifactService.
type fakeCanvasUpdater struct {
	latest    *model.CanvasArtifact
	latestErr error
	getCalls  int

	saved     []savedArtifact
	saveErr   error
	nextVer   int
	accessErr error
}

type savedArtifact struct {
	tenant, user, conv uuid.UUID
	messageID          *uuid.UUID
	key, typ, title    string
	content            string
}

func (f *fakeCanvasUpdater) GetLatest(
	_ context.Context, _, _, _ uuid.UUID, _ string,
) (*model.CanvasArtifact, error) {
	f.getCalls++
	if f.accessErr != nil {
		return nil, f.accessErr
	}
	if f.latestErr != nil {
		return nil, f.latestErr
	}
	return f.latest, nil
}

func (f *fakeCanvasUpdater) Save(
	_ context.Context, tenantID, userID, convID uuid.UUID,
	messageID *uuid.UUID, key, typ, title, content string,
) (*model.CanvasArtifact, error) {
	if f.saveErr != nil {
		return nil, f.saveErr
	}
	f.saved = append(f.saved, savedArtifact{
		tenant: tenantID, user: userID, conv: convID, messageID: messageID,
		key: key, typ: typ, title: title, content: content,
	})
	f.nextVer++
	return &model.CanvasArtifact{
		ArtifactKey: key, Type: typ, Content: content, Version: f.nextVer + 1,
	}, nil
}

func canvasExecutor(f *fakeCanvasUpdater) *ToolExecutor {
	e := &ToolExecutor{}
	e.SetCanvasArtifacts(f)
	return e
}

func htmlArtifact(key string) *model.CanvasArtifact {
	title := "Dashboard"
	return &model.CanvasArtifact{
		ArtifactKey: key, Type: "html", Title: &title,
		Content: "<button>red</button>", Version: 1,
	}
}

func TestUpdateCanvasArtifact_AppendsANewVersionToTheSameLineage(t *testing.T) {
	f := &fakeCanvasUpdater{latest: htmlArtifact("msg-1:abc")}
	e := canvasExecutor(f)
	ec := baseExecCtx()

	args, _ := json.Marshal(updateCanvasArtifactArgs{
		ArtifactKey: "msg-1:abc",
		Content:     "<button style='color:green'>ok</button>",
	})
	out := e.execUpdateCanvasArtifact(context.Background(), string(args), ec)

	if len(f.saved) != 1 {
		t.Fatalf("expected exactly one save, got %d", len(f.saved))
	}
	s := f.saved[0]
	if s.key != "msg-1:abc" {
		t.Errorf("saved under key %q — the update must stay on the same lineage", s.key)
	}
	if s.content != "<button style='color:green'>ok</button>" {
		t.Errorf("content not forwarded verbatim: %q", s.content)
	}
	if s.tenant != ec.TenantID || s.user != ec.UserID || s.conv != ec.ConversationID {
		t.Error("the save must carry the CALLER's scope, never anything from the arguments")
	}
	if s.messageID == nil || *s.messageID != ec.MessageID {
		t.Error("the new version should link the assistant message that produced it")
	}
	if !strings.Contains(out, "version") {
		t.Errorf("the model should be told the new version number, got %q", out)
	}
}

func TestUpdateCanvasArtifact_InheritsTypeAndTitle(t *testing.T) {
	f := &fakeCanvasUpdater{latest: htmlArtifact("k")}
	e := canvasExecutor(f)

	// The model tries to smuggle a different type in — the arguments
	// have no type field at all, and the saved row must keep "html".
	args := `{"artifact_key":"k","content":"<p>x</p>","type":"react"}`
	e.execUpdateCanvasArtifact(context.Background(), args, baseExecCtx())

	if f.saved[0].typ != "html" {
		t.Fatalf("type = %q — a model must never be able to switch the renderer", f.saved[0].typ)
	}
	if f.saved[0].title != "Dashboard" {
		t.Errorf("title should be inherited when not supplied, got %q", f.saved[0].title)
	}
}

func TestUpdateCanvasArtifact_AcceptsAnExplicitNewTitle(t *testing.T) {
	f := &fakeCanvasUpdater{latest: htmlArtifact("k")}
	e := canvasExecutor(f)

	e.execUpdateCanvasArtifact(context.Background(),
		`{"artifact_key":"k","content":"<p>x</p>","title":"Sales dashboard"}`, baseExecCtx())

	if f.saved[0].title != "Sales dashboard" {
		t.Fatalf("title = %q, want the supplied one", f.saved[0].title)
	}
}

func TestUpdateCanvasArtifact_RefusesUnknownOrForeignKeys(t *testing.T) {
	// Same shape for "doesn't exist" and "belongs to another
	// conversation/user": the service returns not-found for both, and the
	// tool must write nothing either way.
	for name, err := range map[string]error{
		"unknown key":     ErrArtifactNotFound,
		"foreign convo":   ErrMessageConversationNotFound,
		"invalid key len": ErrArtifactKeyInvalid,
	} {
		t.Run(name, func(t *testing.T) {
			f := &fakeCanvasUpdater{latestErr: err}
			e := canvasExecutor(f)

			out := e.execUpdateCanvasArtifact(context.Background(),
				`{"artifact_key":"whatever","content":"<p>x</p>"}`, baseExecCtx())

			if len(f.saved) != 0 {
				t.Fatal("nothing may be written when the lineage doesn't resolve")
			}
			if !strings.Contains(out, "couldn't find") {
				t.Errorf("the model needs a usable explanation, got %q", out)
			}
		})
	}
}

func TestUpdateCanvasArtifact_RejectsEmptyArguments(t *testing.T) {
	f := &fakeCanvasUpdater{latest: htmlArtifact("k")}
	e := canvasExecutor(f)

	for _, args := range []string{
		`{"artifact_key":"","content":"<p>x</p>"}`,
		`{"artifact_key":"k","content":"   "}`,
		`not json`,
	} {
		e.execUpdateCanvasArtifact(context.Background(), args, baseExecCtx())
	}
	if len(f.saved) != 0 {
		t.Fatalf("bad arguments must not reach the store, got %d saves", len(f.saved))
	}
	if f.getCalls != 0 {
		t.Fatalf("bad arguments should short-circuit before the lookup, got %d", f.getCalls)
	}
}

func TestUpdateCanvasArtifact_SurfacesTheSizeLimit(t *testing.T) {
	f := &fakeCanvasUpdater{latest: htmlArtifact("k"), saveErr: ErrArtifactTooLarge}
	e := canvasExecutor(f)

	out := e.execUpdateCanvasArtifact(context.Background(),
		`{"artifact_key":"k","content":"<p>x</p>"}`, baseExecCtx())

	if !strings.Contains(out, "too large") {
		t.Fatalf("the model should learn the content was too large, got %q", out)
	}
}

func TestUpdateCanvasArtifact_HiddenWhenNotWired(t *testing.T) {
	e := &ToolExecutor{}
	if e.HasCanvasArtifacts() {
		t.Fatal("the tool must be hidden when the canvas service isn't wired")
	}
	for _, d := range e.ToolDefs(&model.Conversation{MemoryEnabled: true}) {
		if d.Function.Name == "update_canvas_artifact" {
			t.Fatal("update_canvas_artifact was advertised without a backing service")
		}
	}

	e.SetCanvasArtifacts(&fakeCanvasUpdater{})
	found := false
	for _, d := range e.ToolDefs(&model.Conversation{MemoryEnabled: true}) {
		if d.Function.Name == "update_canvas_artifact" {
			found = true
		}
	}
	if !found {
		t.Fatal("the tool should be advertised once wired")
	}
}

// ─── system-prompt block ────────────────────────────────────────────

func TestCanvasSystemBlock_ListsKeysAndInlinesTheNewestSource(t *testing.T) {
	title := "Dashboard"
	artifacts := []model.CanvasArtifact{
		{ArtifactKey: "msg-2:new", Type: "react", Title: &title,
			Content: "export default () => <div/>", Version: 3},
		{ArtifactKey: "msg-1:old", Type: "html", Content: "<p>old</p>", Version: 1},
	}
	got := renderCanvasSystemBlock(artifacts)

	for _, want := range []string{
		"## Canvas Artifacts", "msg-2:new", "msg-1:old",
		"update_canvas_artifact", "version: 3", "Dashboard",
		"export default () => <div/>",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("block is missing %q:\n%s", want, got)
		}
	}
	// Only the NEWEST source is inlined — a long conversation must not
	// blow the context budget.
	if strings.Contains(got, "<p>old</p>") {
		t.Error("only the newest artifact's source should be inlined")
	}
}

func TestCanvasSystemBlock_SkipsAnOversizedSource(t *testing.T) {
	huge := strings.Repeat("x", maxCanvasPromptSource+1)
	got := renderCanvasSystemBlock([]model.CanvasArtifact{
		{ArtifactKey: "k", Type: "html", Content: huge, Version: 1},
	})
	if strings.Contains(got, huge) {
		t.Fatal("an oversized artifact must not be inlined")
	}
	if !strings.Contains(got, "too large") {
		t.Errorf("the model should be told why the source is missing:\n%s", got)
	}
}

func TestCanvasSystemBlock_CapsTheList(t *testing.T) {
	var many []model.CanvasArtifact
	for i := 0; i < maxCanvasPromptArtifacts+4; i++ {
		many = append(many, model.CanvasArtifact{
			ArtifactKey: "k" + string(rune('a'+i)), Type: "html",
			Content: "<p>x</p>", Version: 1,
		})
	}
	got := renderCanvasSystemBlock(many)
	if n := strings.Count(got, "artifact_key:"); n != maxCanvasPromptArtifacts {
		t.Fatalf("listed %d artifacts, want the %d newest", n, maxCanvasPromptArtifacts)
	}
}

func TestCanvasContext_EmptyWithoutArtifactsOrLookup(t *testing.T) {
	s := &MessageService{}
	if got := s.canvasContext(context.Background(), uuid.New(), uuid.New()); got != "" {
		t.Errorf("no lookup wired ⇒ no block, got:\n%s", got)
	}

	s.SetCanvasLookup(stubCanvasLookup{})
	if got := s.canvasContext(context.Background(), uuid.New(), uuid.New()); got != "" {
		t.Errorf("no artifacts ⇒ no block (an empty list would invite invented keys), got:\n%s", got)
	}

	s.SetCanvasLookup(stubCanvasLookup{err: errors.New("db down")})
	if got := s.canvasContext(context.Background(), uuid.New(), uuid.New()); got != "" {
		t.Errorf("a failed lookup must degrade to no block, got:\n%s", got)
	}
}

type stubCanvasLookup struct {
	rows []model.CanvasArtifact
	err  error
}

func (s stubCanvasLookup) ListLatest(
	_ context.Context, _, _ uuid.UUID,
) ([]model.CanvasArtifact, error) {
	return s.rows, s.err
}
