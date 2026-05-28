// FeedbackService — thumbs-up / thumbs-down upsert for assistant
// messages.
//
// Phase 1 has no comment field (any free-text input would be a
// new PII channel and is deferred until DLP-on-save lands). The
// caller can flip their vote at any time — the upsert in
// FeedbackRepo handles toggle vs first-vote in one statement.
package service

import (
	"context"
	"errors"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// Typed errors.
var (
	ErrFeedbackBadRating       = errors.New("feedback: rating must be thumbs_up or thumbs_down")
	ErrFeedbackTargetNotFound  = errors.New("feedback: target message not found")
	ErrFeedbackNotAssistantMsg = errors.New("feedback: feedback only allowed on assistant messages")
)

type FeedbackService struct {
	feedback      *repository.FeedbackRepo
	messages      *repository.MessageRepo
	conversations *repository.ConversationRepo
}

// NewFeedbackService wires the repos. The conversations repo lets
// Create verify the caller actually owns the conversation the
// target message belongs to — without it a same-tenant attacker
// could thumbs-vote arbitrary messages in other users' threads
// (the feedback table's (message_id, user_id) UNIQUE constraint
// keeps it from overwriting, but still leaks message existence).
func NewFeedbackService(
	feedback *repository.FeedbackRepo,
	messages *repository.MessageRepo,
	conversations *repository.ConversationRepo,
) *FeedbackService {
	return &FeedbackService{
		feedback:      feedback,
		messages:      messages,
		conversations: conversations,
	}
}

// CreateInput is the typed payload from the handler.
type FeedbackCreateInput struct {
	TenantID  uuid.UUID
	UserID    uuid.UUID
	MessageID uuid.UUID
	Rating    string // must be ``thumbs_up`` or ``thumbs_down``
}

// Create upserts the feedback row after verifying the target
// message exists within the caller's tenant + has assistant role.
func (s *FeedbackService) Create(ctx context.Context, in FeedbackCreateInput) (*model.Feedback, error) {
	if in.Rating != "thumbs_up" && in.Rating != "thumbs_down" {
		return nil, ErrFeedbackBadRating
	}

	target, err := s.messages.GetByID(ctx, in.TenantID, in.MessageID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrFeedbackTargetNotFound
		}
		return nil, err
	}
	if target.Role != "assistant" {
		return nil, ErrFeedbackNotAssistantMsg
	}
	// Walk the message → conversation → user_id chain so a same-
	// tenant caller can't thumbs-vote messages in other users'
	// threads. Returns ErrFeedbackTargetNotFound on any mismatch
	// so the existence of the target message stays hidden.
	conv, err := s.conversations.GetByID(ctx, in.TenantID, target.ConversationID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrFeedbackTargetNotFound
		}
		return nil, err
	}
	if conv.UserID != in.UserID {
		return nil, ErrFeedbackTargetNotFound
	}

	return s.feedback.Upsert(ctx, &model.Feedback{
		MessageID: in.MessageID,
		TenantID:  in.TenantID,
		UserID:    in.UserID,
		Rating:    in.Rating,
	})
}
