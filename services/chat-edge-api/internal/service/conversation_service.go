// ConversationService — CRUD over chat.conversations with tenant
// ownership checks at every entry point.
//
// Handlers translate the typed errors returned by this service into
// HTTP status codes; the service has no knowledge of the wire layer.
package service

import (
	"context"
	"errors"
	"log/slog"
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
// config: "is this model id in our allow-list?" plus an enumeration
// of allowed IDs (used by the Auto-model resolver). Keeps the
// service decoupled from the concrete Config type for testing.
type ModelChecker interface {
	ModelAllowed(id string) bool
	ModelIDs() []string
}

type ConversationService struct {
	conversations *repository.ConversationRepo
	models        ModelChecker
	// deletionAudit, when wired (SetDeletionAudit), records the user's
	// soft-delete as a durable audit entry — the recorded INTENT the
	// nightly hard-erasure later completes. Nil-safe: unset skips.
	deletionAudit *repository.DeletionAuditRepo
}

func NewConversationService(c *repository.ConversationRepo, models ModelChecker) *ConversationService {
	return &ConversationService{conversations: c, models: models}
}

// SetDeletionAudit wires the deletion-audit sink (main.go).
func (s *ConversationService) SetDeletionAudit(a *repository.DeletionAuditRepo) {
	s.deletionAudit = a
}

// CreateInput is what the handler passes after parsing JSON.
type CreateInput struct {
	UserID   uuid.UUID
	TenantID uuid.UUID
	ModelID  string
	// IsTemporary — FIX127 B1: the user started the chat as temporary.
	IsTemporary bool
}

func (s *ConversationService) Create(ctx context.Context, in CreateInput) (*model.Conversation, error) {
	if strings.TrimSpace(in.ModelID) == "" {
		return nil, errors.New("model_id is required")
	}
	if !s.models.ModelAllowed(in.ModelID) {
		return nil, ErrConversationBadModel
	}
	return s.conversations.Create(ctx, &model.Conversation{
		TenantID:    in.TenantID,
		UserID:      in.UserID,
		ModelID:     in.ModelID,
		IsTemporary: in.IsTemporary,
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

// AssertOwnership is the canonical "does this user own this
// conversation" check, reused by sibling services that need to
// gate file/workspace/feedback access through the conversation
// chain. Returns the conversation on success so callers don't
// have to round-trip a second GetByID.
//
// Returns ErrConversationNotFound on every failure mode (wrong
// tenant, wrong user, soft-deleted, missing) so the upstream HTTP
// layer can map a single error to 404 without leaking existence.
func (s *ConversationService) AssertOwnership(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) (*model.Conversation, error) {
	c, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrConversationNotFound
		}
		return nil, err
	}
	if c.UserID != userID {
		return nil, ErrConversationNotFound
	}
	return c, nil
}

// List returns the user's conversations with cursor pagination.
// “limit“ is clamped to [1, 100]; default 50. Up from the
// previous 20-default because the new sidebar's infinite-scroll
// experience benefits from larger first pages.
func (s *ConversationService) List(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	limit int,
	cursor uuid.UUID,
	archived bool,
) ([]model.Conversation, uuid.UUID, error) {
	if limit <= 0 {
		limit = 50
	}
	if limit > 100 {
		limit = 100
	}
	return s.conversations.ListByUser(ctx, tenantID, userID, limit, cursor, archived)
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
		convs, _, err := s.List(ctx, tenantID, userID, limit, uuid.Nil, false)
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
//
// User-scope gate: AssertOwnership runs before the UPDATE so a
// same-tenant-different-user caller can't rename someone else's
// conversation.
func (s *ConversationService) Rename(
	ctx context.Context, tenantID, userID, convID uuid.UUID, title string,
) error {
	if _, err := s.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return err
	}
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
func (s *ConversationService) SetModel(
	ctx context.Context, tenantID, userID, convID uuid.UUID, modelID string,
) error {
	if _, err := s.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return err
	}
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
	ctx context.Context, tenantID, userID, convID uuid.UUID, kbID uuid.UUID,
) error {
	if _, err := s.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return err
	}
	if err := s.conversations.UpdateKnowledgeBase(ctx, tenantID, convID, kbID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}

// SetMemoryEnabled toggles the per-conversation memory layer
// (S1 — temporary chat). User-scope gate runs first so a same-tenant
// caller can't flip another user's conversation.
func (s *ConversationService) SetMemoryEnabled(
	ctx context.Context, tenantID, userID, convID uuid.UUID, enabled bool,
) error {
	if _, err := s.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return err
	}
	if err := s.conversations.UpdateMemoryEnabled(ctx, tenantID, convID, enabled); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}

// SetPinned flips the sidebar pin flag (migration 021). User-scope gate
// first so a same-tenant caller can't pin another user's conversation.
func (s *ConversationService) SetPinned(
	ctx context.Context, tenantID, userID, convID uuid.UUID, pinned bool,
) error {
	if _, err := s.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return err
	}
	if err := s.conversations.UpdatePinned(ctx, tenantID, convID, pinned); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}

// SetArchived files a conversation away from the sidebar (or brings it
// back). User-scope gate first so a same-tenant caller can't archive
// someone else's conversation. Archiving is NOT deleting: the chat stays
// openable, searchable and sendable — only the list hides it.
// ValidRoutingTiers is the closed set the Auto router understands
// (FIX35 Part C). "" means "use the tenant / deployment default".
var ValidRoutingTiers = map[string]bool{"": true, "low": true, "medium": true, "high": true, "xhigh": true, "max": true}

// SetRoutingTier pins the Auto router's cost-tier ceiling for one
// conversation (FIX35 Part C). Only meaningful on an "auto" conversation;
// stored regardless so the choice survives a later switch back to Auto.
func (s *ConversationService) SetRoutingTier(
	ctx context.Context, tenantID, userID, convID uuid.UUID, tier string,
) error {
	if !ValidRoutingTiers[tier] {
		return ErrConversationInvalidRoutingTier
	}
	if _, err := s.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return err
	}
	if err := s.conversations.UpdateRoutingTier(ctx, tenantID, convID, tier); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}

// ErrConversationInvalidRoutingTier — routing_tier outside ValidRoutingTiers.
var ErrConversationInvalidRoutingTier = errors.New("invalid routing_tier")

func (s *ConversationService) SetArchived(
	ctx context.Context, tenantID, userID, convID uuid.UUID, archived bool,
) error {
	if _, err := s.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return err
	}
	if err := s.conversations.UpdateArchived(ctx, tenantID, convID, archived); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}

// SetReasoning persists the per-conversation thinking-mode settings
// (T4 — migration 019). All three params are pointer-typed so the
// handler can patch any subset. User-scope gate first so a
// same-tenant caller can't flip another user's conversation.
func (s *ConversationService) SetReasoning(
	ctx context.Context,
	tenantID, userID, convID uuid.UUID,
	enabled *bool, mode *string, showSummary *bool,
) error {
	if _, err := s.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return err
	}
	if err := s.conversations.UpdateReasoningSettings(
		ctx, tenantID, convID, enabled, mode, showSummary,
	); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}

// Delete soft-deletes the conversation: the user-visible removal is
// immediate. Back-end erasure completes when the nightly transcript
// sweep hard-deletes the row deleted_conversation_purge_days later
// (TranscriptRetentionService) — until then the soft-deleted row is
// the erasure queue entry. The audit entry written here records the
// user's request; the sweep's entry records the completion.
func (s *ConversationService) Delete(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) error {
	if _, err := s.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return err
	}
	if err := s.conversations.SoftDelete(ctx, tenantID, convID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	if s.deletionAudit != nil {
		uid := userID
		cid := convID
		if err := s.deletionAudit.Insert(ctx, &model.DeletionAuditEntry{
			TenantID:   tenantID,
			UserID:     &uid,
			Actor:      "user:" + userID.String(),
			Action:     model.AuditActionConversationSoftDeleted,
			Scope:      "conversation",
			ResourceID: &cid,
			Rule:       model.AuditRuleUserRequest,
		}); err != nil {
			slog.Warn("conversation delete: audit write failed",
				"conversation", convID.String(), "err", err)
		}
	}
	return nil
}
