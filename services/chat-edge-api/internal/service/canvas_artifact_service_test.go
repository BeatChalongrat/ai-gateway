package service

// CanvasArtifactService unit tests — validation rules + the ownership
// gate, exercised against in-memory stubs (no DB). The repo/DB behavior
// (versioning, scoping, cascade) is covered by the integration tests in
// internal/repository/canvas_artifact_repo_test.go.

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// stubArtifactStore records CreateVersion calls and serves one canned
// version for GetVersion.
type stubArtifactStore struct {
	created     []model.CanvasArtifact
	getVersion  *model.CanvasArtifact
	getVersionE error
}

func (s *stubArtifactStore) CreateVersion(
	_ context.Context, tenantID, userID, convID uuid.UUID,
	messageID *uuid.UUID, key, typ, title, content string,
) (*model.CanvasArtifact, error) {
	a := model.CanvasArtifact{
		ID: uuid.New(), TenantID: tenantID, UserID: userID, ConversationID: convID,
		MessageID: messageID, ArtifactKey: key, Type: typ, Content: content,
		Version: len(s.created) + 1,
	}
	if title != "" {
		a.Title = &title
	}
	s.created = append(s.created, a)
	return &a, nil
}

func (s *stubArtifactStore) ListLatest(context.Context, uuid.UUID, uuid.UUID) ([]model.CanvasArtifact, error) {
	return nil, nil
}

func (s *stubArtifactStore) ListVersions(context.Context, uuid.UUID, uuid.UUID, string) ([]model.CanvasArtifact, error) {
	return nil, nil
}

func (s *stubArtifactStore) GetVersion(context.Context, uuid.UUID, uuid.UUID, string, int) (*model.CanvasArtifact, error) {
	return s.getVersion, s.getVersionE
}

func (s *stubArtifactStore) GetLatest(context.Context, uuid.UUID, uuid.UUID, string) (*model.CanvasArtifact, error) {
	return nil, nil
}

// stubAccess grants or denies every conversation.
type stubAccess struct{ err error }

func (s *stubAccess) AssertConversationAccess(context.Context, uuid.UUID, uuid.UUID, uuid.UUID) error {
	return s.err
}

func TestCanvasArtifactService_SaveValidation(t *testing.T) {
	ctx := context.Background()
	tenantID, userID, convID := uuid.New(), uuid.New(), uuid.New()

	cases := []struct {
		name    string
		key     string
		typ     string
		content string
		want    error
	}{
		{"empty key", "", "html", "<p/>", ErrArtifactKeyInvalid},
		{"key too long", strings.Repeat("k", 201), "html", "<p/>", ErrArtifactKeyInvalid},
		{"bad type", "a.html", "pdf", "<p/>", ErrArtifactTypeInvalid},
		{"empty content", "a.html", "html", "", ErrArtifactTooLarge},
		{"oversized content", "a.html", "html", strings.Repeat("x", 524289), ErrArtifactTooLarge},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			store := &stubArtifactStore{}
			svc := NewCanvasArtifactService(store, &stubAccess{})
			_, err := svc.Save(ctx, tenantID, userID, convID, nil, tc.key, tc.typ, "", tc.content)
			if !errors.Is(err, tc.want) {
				t.Fatalf("want %v, got %v", tc.want, err)
			}
			if len(store.created) != 0 {
				t.Fatal("invalid save must never reach the repo")
			}
		})
	}

	// Boundary values pass: max-length key, max-size content, every type.
	for _, typ := range []string{"html", "react", "mermaid", "svg", "markdown"} {
		store := &stubArtifactStore{}
		svc := NewCanvasArtifactService(store, &stubAccess{})
		if _, err := svc.Save(ctx, tenantID, userID, convID, nil,
			strings.Repeat("k", 200), typ, "t", strings.Repeat("x", 524288)); err != nil {
			t.Fatalf("boundary save (%s) should pass: %v", typ, err)
		}
	}
}

func TestCanvasArtifactService_SaveTruncatesLongTitle(t *testing.T) {
	store := &stubArtifactStore{}
	svc := NewCanvasArtifactService(store, &stubAccess{})
	// 300 Thai runes — truncation must cut at 255 RUNES, never mid-char.
	long := strings.Repeat("ก", 300)
	a, err := svc.Save(context.Background(), uuid.New(), uuid.New(), uuid.New(), nil, "t.md", "markdown", long, "x")
	if err != nil {
		t.Fatalf("save: %v", err)
	}
	if a.Title == nil || len([]rune(*a.Title)) != 255 {
		t.Fatalf("title not truncated to 255 runes: %+v", a.Title)
	}
}

func TestCanvasArtifactService_OwnershipGateBlocksEverything(t *testing.T) {
	ctx := context.Background()
	tenantID, userID, convID := uuid.New(), uuid.New(), uuid.New()
	store := &stubArtifactStore{}
	svc := NewCanvasArtifactService(store, &stubAccess{err: ErrMessageConversationNotFound})

	if _, err := svc.Save(ctx, tenantID, userID, convID, nil, "a.html", "html", "", "<p/>"); !errors.Is(err, ErrMessageConversationNotFound) {
		t.Fatalf("Save should propagate the access error, got %v", err)
	}
	if _, err := svc.ListLatest(ctx, tenantID, userID, convID); !errors.Is(err, ErrMessageConversationNotFound) {
		t.Fatalf("ListLatest should propagate the access error, got %v", err)
	}
	if _, err := svc.ListVersions(ctx, tenantID, userID, convID, "a.html"); !errors.Is(err, ErrMessageConversationNotFound) {
		t.Fatalf("ListVersions should propagate the access error, got %v", err)
	}
	if _, err := svc.Revert(ctx, tenantID, userID, convID, "a.html", 1); !errors.Is(err, ErrMessageConversationNotFound) {
		t.Fatalf("Revert should propagate the access error, got %v", err)
	}
	if len(store.created) != 0 {
		t.Fatal("denied caller must never reach the repo")
	}
}

func TestCanvasArtifactService_RevertCopiesSourceVersion(t *testing.T) {
	title := "เวอร์ชันแรก"
	src := &model.CanvasArtifact{
		Type: "mermaid", Title: &title, Content: "graph TD; A-->B", Version: 1,
	}
	store := &stubArtifactStore{getVersion: src}
	svc := NewCanvasArtifactService(store, &stubAccess{})

	got, err := svc.Revert(context.Background(), uuid.New(), uuid.New(), uuid.New(), "flow.mmd", 1)
	if err != nil {
		t.Fatalf("revert: %v", err)
	}
	if got.Type != "mermaid" || got.Content != "graph TD; A-->B" ||
		got.Title == nil || *got.Title != title {
		t.Fatalf("revert did not copy source fields: %+v", got)
	}
	if got.MessageID != nil {
		t.Fatal("revert must not carry over the source message link")
	}
}
