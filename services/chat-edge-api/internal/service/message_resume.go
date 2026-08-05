package service

// Phase 5 — stream resume, service half. ResumeAttach is what the
// GET /conversations/{id}/messages/{messageID}/stream handler calls to
// reattach a client to an in-flight generation.
//
// Resolution order:
//  1. Ownership: caller must own the conversation AND the message must
//     belong to it (assistant role only) — reattach can never cross a
//     user or tenant boundary.
//  2. Broker hit → live attach: snapshot prefix + live event channel.
//  3. Broker miss + row already terminal → full replay from the DB.
//  4. Broker miss + row still "streaming":
//     a. younger than resumeOrphanGrace → ErrResumeUnavailable (the
//        generation may not have registered yet; client retries).
//     b. older → the generation was orphaned (process restart mid-
//        stream). Settle the row to status=error (guarded UPDATE) and
//        replay what was persisted with an interrupted terminal so
//        clients stop waiting forever.

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ErrResumeUnavailable — the row says "streaming" but no generation is
// registered and the row is too young to declare orphaned. The handler
// maps this to 409 so the client retries with backoff.
var ErrResumeUnavailable = errors.New("resume: generation not attachable yet")

// ErrGenerationInterrupted is the terminal error surfaced when a
// streaming row was orphaned by a restart (or persisted as error).
var ErrGenerationInterrupted = errors.New("generation interrupted")

// resumeOrphanGrace: a streaming row younger than this with no broker
// entry is treated as "not registered yet", not orphaned. The broker
// entry is registered in the same request that inserts the row, so in
// practice the gap is milliseconds; the grace only guards clock skew
// and slow inserts.
const resumeOrphanGrace = 10 * time.Second

// ResumeAttachment is everything the resume handler needs to serve the
// SSE response. Events == nil ⇒ the generation already ended and
// Snapshot.Terminal carries the terminal state (full replay).
type ResumeAttachment struct {
	Message  *model.Message
	Snapshot StreamSnapshot
	Events   <-chan StreamUpdate
	// Cancel unsubscribes from the broker (idempotent, never nil).
	// MUST be called when the caller stops consuming Events.
	Cancel func()
	// Lagged reports (after Events closes) whether the subscriber was
	// dropped for falling behind — the handler tells the client to
	// reattach fresh instead of emitting a bogus message_complete.
	Lagged func() bool
}

func (s *MessageService) ResumeAttach(
	ctx context.Context, tenantID, userID, convID, msgID uuid.UUID,
) (*ResumeAttachment, error) {
	if err := s.AssertConversationAccess(ctx, tenantID, userID, convID); err != nil {
		return nil, err
	}
	msg, err := s.messages.GetByID(ctx, tenantID, msgID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageNotFound
		}
		return nil, err
	}
	if msg.ConversationID != convID {
		return nil, ErrMessageNotFound
	}
	if msg.Role != "assistant" {
		return nil, ErrMessageNotAssistant
	}

	if s.broker != nil {
		if snap, events, cancelSub, lagged, ok := s.broker.Subscribe(msgID); ok {
			return &ResumeAttachment{
				Message:  msg,
				Snapshot: snap,
				Events:   events,
				Cancel:   cancelSub,
				Lagged:   lagged,
			}, nil
		}
	}

	noop := func() {}
	notLagged := func() bool { return false }
	switch msg.Status {
	case "streaming", "pending":
		if time.Since(msg.CreatedAt) < resumeOrphanGrace {
			return nil, ErrResumeUnavailable
		}
		// Orphaned by a restart — settle the row so clients stop
		// waiting on it. Guarded UPDATE: only flips streaming/pending.
		_ = s.messages.MarkInterrupted(ctx, msgID)
		msg.Status = "error"
		return &ResumeAttachment{
			Message: msg,
			Snapshot: StreamSnapshot{
				Content:  msg.ContentMasked,
				Terminal: &StreamUpdate{Err: ErrGenerationInterrupted},
			},
			Cancel: noop,
			Lagged: notLagged,
		}, nil
	default:
		// Terminal row — full replay so a reattach that raced
		// completion resolves instantly instead of hanging.
		snap := StreamSnapshot{Content: msg.ContentMasked}
		if msg.ReasoningContent != nil && *msg.ReasoningContent != "" {
			snap.Reasoning = *msg.ReasoningContent
			snap.AnalysisStatus = "completed"
		}
		if msg.Status == "error" {
			snap.Terminal = &StreamUpdate{Err: ErrGenerationInterrupted}
		} else {
			term := &StreamUpdate{Done: true}
			if msg.TokenUsageInput != nil || msg.TokenUsageOutput != nil {
				u := &GatewayChatUsage{}
				if msg.TokenUsageInput != nil {
					u.PromptTokens = *msg.TokenUsageInput
				}
				if msg.TokenUsageOutput != nil {
					u.CompletionTokens = *msg.TokenUsageOutput
				}
				term.Usage = u
			}
			snap.Terminal = term
		}
		return &ResumeAttachment{
			Message:  msg,
			Snapshot: snap,
			Cancel:   noop,
			Lagged:   notLagged,
		}, nil
	}
}
