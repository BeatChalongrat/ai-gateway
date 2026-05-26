// ConversationService — CRUD over chat.conversations with tenant
// ownership checks at every entry point.
//
// Handlers translate the typed errors returned by this service into
// HTTP status codes; the service has no knowledge of the wire layer.
package service

import (
	"context"
	"errors"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// Typed errors. Handlers map these to HTTP statuses.
var (
	ErrConversationNotFound = errors.New("conversation: not found")
	ErrConversationDenied   = errors.New("conversation: access denied")
	ErrConversationBadModel = errors.New("conversation: model not allowed")
)

// ModelChecker is the small contract the service needs from the
// config: "is this model id in our allow-list?" Keeps the service
// decoupled from the concrete Config type for testing.
type ModelChecker interface {
	ModelAllowed(id string) bool
}

type ConversationService struct {
	conversations *repository.ConversationRepo
	models        ModelChecker
}

func NewConversationService(c *repository.ConversationRepo, models ModelChecker) *ConversationService {
	return &ConversationService{conversations: c, models: models}
}

// CreateInput is what the handler passes after parsing JSON.
type CreateInput struct {
	UserID   uuid.UUID
	TenantID uuid.UUID
	ModelID  string
}

func (s *ConversationService) Create(ctx context.Context, in CreateInput) (*model.Conversation, error) {
	if strings.TrimSpace(in.ModelID) == "" {
		return nil, errors.New("model_id is required")
	}
	if !s.models.ModelAllowed(in.ModelID) {
		return nil, ErrConversationBadModel
	}
	return s.conversations.Create(ctx, &model.Conversation{
		TenantID: in.TenantID,
		UserID:   in.UserID,
		ModelID:  in.ModelID,
	})
}

// Get returns a conversation enforcing tenant scope. The repo
// already gates on tenant_id + is_deleted=false — we surface its
// ErrNotFound as ErrConversationNotFound for typed error mapping.
func (s *ConversationService) Get(ctx context.Context, tenantID, convID uuid.UUID) (*model.Conversation, error) {
	c, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrConversationNotFound
		}
		return nil, err
	}
	return c, nil
}

// List returns the user's conversations with cursor pagination.
// ``limit`` is clamped to [1, 100]; default 50. Up from the
// previous 20-default because the new sidebar's infinite-scroll
// experience benefits from larger first pages.
func (s *ConversationService) List(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	limit int,
	cursor uuid.UUID,
) ([]model.Conversation, uuid.UUID, error) {
	if limit <= 0 {
		limit = 50
	}
	if limit > 100 {
		limit = 100
	}
	return s.conversations.ListByUser(ctx, tenantID, userID, limit, cursor)
}

// Search runs a title ILIKE query on the user's conversations.
// Empty / short queries pass through to List for a sane default.
func (s *ConversationService) Search(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	query string,
	limit int,
) ([]model.Conversation, error) {
	q := strings.TrimSpace(query)
	if q == "" {
		// Empty search → behave like list. Caller decides whether
		// that's the right UX; the front-end short-circuits the
		// search path when the input is empty so this is mostly a
		// safety net.
		convs, _, err := s.List(ctx, tenantID, userID, limit, uuid.Nil)
		return convs, err
	}
	if limit <= 0 {
		limit = 50
	}
	if limit > 100 {
		limit = 100
	}
	// Escape SQL LIKE meta-characters so a literal `_` or `%` in
	// the search text doesn't act as a wildcard.
	q = strings.ReplaceAll(q, "\\", "\\\\")
	q = strings.ReplaceAll(q, "%", "\\%")
	q = strings.ReplaceAll(q, "_", "\\_")
	return s.conversations.SearchByUser(ctx, tenantID, userID, q, limit)
}

// Rename updates the title. Validates non-empty + length cap to
// match the DB column expectations (TEXT is unbounded but the UI
// renders a sidebar list so we keep titles compact).
func (s *ConversationService) Rename(ctx context.Context, tenantID, convID uuid.UUID, title string) error {
	title = strings.TrimSpace(title)
	if title == "" {
		return errors.New("title is required")
	}
	if n := len([]rune(title)); n > 200 {
		// Trim to 200 runes (not bytes — Thai is multi-byte) so we
		// don't truncate mid-codepoint.
		title = string([]rune(title)[:200])
	}
	if err := s.conversations.UpdateTitle(ctx, tenantID, convID, title); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}

// SetModel reassigns the conversation's default model. Used by
// PATCH /conversations/{id} when the user picks a different model
// mid-conversation. Validates that the new id is in the allow-list
// so an admin-removed model can't be re-pinned to a conversation.
func (s *ConversationService) SetModel(ctx context.Context, tenantID, convID uuid.UUID, modelID string) error {
	modelID = strings.TrimSpace(modelID)
	if modelID == "" {
		return errors.New("model_id is required")
	}
	if !s.models.ModelAllowed(modelID) {
		return ErrConversationBadModel
	}
	if err := s.conversations.UpdateModel(ctx, tenantID, convID, modelID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}

// SetKnowledgeBase binds or unbinds the conversation's RAG source.
// kbID == uuid.Nil clears the binding. Caller is expected to have
// already verified the KB belongs to the same tenant (the handler
// does this via a GetKB lookup before invoking the service).
func (s *ConversationService) SetKnowledgeBase(
	ctx context.Context, tenantID, convID uuid.UUID, kbID uuid.UUID,
) error {
	if err := s.conversations.UpdateKnowledgeBase(ctx, tenantID, convID, kbID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}

// Delete soft-deletes the conversation. Messages remain in
// chat.messages_masked for audit; only the sidebar listing hides.
func (s *ConversationService) Delete(ctx context.Context, tenantID, convID uuid.UUID) error {
	if err := s.conversations.SoftDelete(ctx, tenantID, convID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}
