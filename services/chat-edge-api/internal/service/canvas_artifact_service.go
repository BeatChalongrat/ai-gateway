package service

// CanvasArtifactService — Phase 6/C3 canvas artifacts. Validation +
// ownership assertions over CanvasArtifactRepo, mirroring
// ProjectService's shape. Every operation first asserts the caller owns
// the conversation via MessageService.AssertConversationAccess (the one
// canonical ownership check — no parallel implementation here), so the
// repo layer only needs (tenant_id, conversation_id) scoping.

import (
	"context"
	"errors"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

const (
	// maxArtifactContentBytes caps a single version at 512 KiB — generous
	// for html/react/svg source, small enough that a lineage of versions
	// can't balloon a conversation row-set.
	maxArtifactContentBytes = 524288
	maxArtifactKeyLen       = 200
	// maxArtifactTitleLen — titles LONGER than this are TRUNCATED (on a
	// rune boundary), not rejected: the title is display metadata only
	// and failing a whole save over it would be hostile.
	maxArtifactTitleLen = 255
)

var (
	ErrArtifactNotFound    = errors.New("canvas artifact not found")
	ErrArtifactTypeInvalid = errors.New("artifact type must be one of html, react, mermaid, svg, markdown")
	ErrArtifactTooLarge    = errors.New("artifact content must be 1-524288 bytes")
	ErrArtifactKeyInvalid  = errors.New("artifact key must be 1-200 characters")
)

// validArtifactTypes is the closed set the canvas renderer understands.
var validArtifactTypes = map[string]bool{
	"html":     true,
	"react":    true,
	"mermaid":  true,
	"svg":      true,
	"markdown": true,
}

// conversationAccessAsserter is the one method this service needs from
// MessageService — satisfied by *MessageService.
type conversationAccessAsserter interface {
	AssertConversationAccess(ctx context.Context, tenantID, userID, convID uuid.UUID) error
}

// canvasArtifactStore is the repo contract — satisfied by
// *repository.CanvasArtifactRepo. Interface-shaped (unlike
// ProjectService's concrete repo field) so the validation rules are
// unit-testable without a database.
type canvasArtifactStore interface {
	CreateVersion(ctx context.Context, tenantID, userID, convID uuid.UUID, messageID *uuid.UUID, key, typ, title, content string) (*model.CanvasArtifact, error)
	ListLatest(ctx context.Context, tenantID, convID uuid.UUID) ([]model.CanvasArtifact, error)
	ListVersions(ctx context.Context, tenantID, convID uuid.UUID, key string) ([]model.CanvasArtifact, error)
	GetVersion(ctx context.Context, tenantID, convID uuid.UUID, key string, version int) (*model.CanvasArtifact, error)
	GetLatest(ctx context.Context, tenantID, convID uuid.UUID, key string) (*model.CanvasArtifact, error)
}

type CanvasArtifactService struct {
	artifacts canvasArtifactStore
	messages  conversationAccessAsserter
}

func NewCanvasArtifactService(
	artifacts canvasArtifactStore, messages conversationAccessAsserter,
) *CanvasArtifactService {
	return &CanvasArtifactService{artifacts: artifacts, messages: messages}
}

// truncateArtifactTitle clips the title to maxArtifactTitleLen runes
// (rune-safe so Thai text never gets cut mid-character).
func truncateArtifactTitle(title string) string {
	r := []rune(title)
	if len(r) > maxArtifactTitleLen {
		return string(r[:maxArtifactTitleLen])
	}
	return title
}

// Save validates and appends the next version of (convID, key).
// Ownership failures surface as ErrMessageConversationNotFound — the
// handler maps every not-found to 404 so existence isn't probeable.
func (s *CanvasArtifactService) Save(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
	messageID *uuid.UUID, key, typ, title, content string,
) (*model.CanvasArtifact, error) {
	if err := s.messages.AssertConversationAccess(ctx, tenantID, userID, convID); err != nil {
		return nil, err
	}
	if key == "" || len(key) > maxArtifactKeyLen {
		return nil, ErrArtifactKeyInvalid
	}
	if !validArtifactTypes[typ] {
		return nil, ErrArtifactTypeInvalid
	}
	if content == "" || len(content) > maxArtifactContentBytes {
		return nil, ErrArtifactTooLarge
	}
	return s.artifacts.CreateVersion(
		ctx, tenantID, userID, convID, messageID, key, typ, truncateArtifactTitle(title), content,
	)
}

// ListLatest returns the newest version of every artifact in the
// conversation, newest first.
func (s *CanvasArtifactService) ListLatest(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) ([]model.CanvasArtifact, error) {
	if err := s.messages.AssertConversationAccess(ctx, tenantID, userID, convID); err != nil {
		return nil, err
	}
	return s.artifacts.ListLatest(ctx, tenantID, convID)
}

// ListVersions returns every version for one artifact_key, version DESC.
func (s *CanvasArtifactService) ListVersions(
	ctx context.Context, tenantID, userID, convID uuid.UUID, key string,
) ([]model.CanvasArtifact, error) {
	if err := s.messages.AssertConversationAccess(ctx, tenantID, userID, convID); err != nil {
		return nil, err
	}
	if key == "" || len(key) > maxArtifactKeyLen {
		return nil, ErrArtifactKeyInvalid
	}
	versions, err := s.artifacts.ListVersions(ctx, tenantID, convID, key)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrArtifactNotFound
		}
		return nil, err
	}
	return versions, nil
}

// GetLatest returns the newest version of ONE artifact. Added in Phase 8
// for the model-driven update tool: the tool inherits the existing
// type/title rather than letting the model restate (and therefore
// change) them, and an unknown key must fail before anything is written.
func (s *CanvasArtifactService) GetLatest(
	ctx context.Context, tenantID, userID, convID uuid.UUID, key string,
) (*model.CanvasArtifact, error) {
	if err := s.messages.AssertConversationAccess(ctx, tenantID, userID, convID); err != nil {
		return nil, err
	}
	if key == "" || len(key) > maxArtifactKeyLen {
		return nil, ErrArtifactKeyInvalid
	}
	a, err := s.artifacts.GetLatest(ctx, tenantID, convID, key)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrArtifactNotFound
		}
		return nil, err
	}
	return a, nil
}

// Revert appends a NEW version whose type/title/content are copied from
// an older one — linear lineage, history is never mutated or deleted
// (same decision as the Phase-4 workspace rollback). Returns the new
// artifact version.
func (s *CanvasArtifactService) Revert(
	ctx context.Context, tenantID, userID, convID uuid.UUID, key string, version int,
) (*model.CanvasArtifact, error) {
	if err := s.messages.AssertConversationAccess(ctx, tenantID, userID, convID); err != nil {
		return nil, err
	}
	if key == "" || len(key) > maxArtifactKeyLen {
		return nil, ErrArtifactKeyInvalid
	}
	src, err := s.artifacts.GetVersion(ctx, tenantID, convID, key, version)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrArtifactNotFound
		}
		return nil, err
	}
	title := ""
	if src.Title != nil {
		title = *src.Title
	}
	// The reverted version's message link is deliberately NOT copied —
	// the new version was produced by the revert action, not by the
	// original assistant message.
	return s.artifacts.CreateVersion(
		ctx, tenantID, userID, convID, nil, key, src.Type, title, src.Content,
	)
}
