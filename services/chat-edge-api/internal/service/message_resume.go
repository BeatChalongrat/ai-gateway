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

// ErrResumeTooOld — the client asked to continue from a position the
// ledger no longer holds (trimmed by the cap or the TTL). The handler
// maps this to 409 RESUME_TOO_OLD so the client reloads the message from
// the API. It must NEVER be reported as a completed stream: the user
// would be shown a truncated answer as if it were the whole one.
var ErrResumeTooOld = errors.New("resume: position older than the ledger")

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
	Message *model.Message
	// Frames (FIX41) is the preferred path: the run's recorded wire
	// frames, replayed verbatim with their original seq, then the live
	// tail. Non-nil whenever the broker still holds the run.
	//
	// When it is set, Snapshot/Events are unused — deriving frames a
	// second time from StreamUpdate would renumber them, and two writers
	// numbering the same run independently is exactly the gap-and-
	// duplicate problem this replaces.
	Frames   *FrameAttachment
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
	return s.ResumeAttachFrom(ctx, tenantID, userID, convID, msgID, 0)
}

// ResumeAttachFrom is ResumeAttach with a cursor: only frames with
// seq > startingAfter are replayed (FIX41 / STREAM_CONTRACT §4).
// startingAfter == 0 replays the whole run, which is the pre-FIX41
// behaviour and what a client that sends no cursor gets.
func (s *MessageService) ResumeAttachFrom(
	ctx context.Context, tenantID, userID, convID, msgID uuid.UUID, startingAfter int64,
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
		// FIX41 — the ledger first. It is the only source that can say
		// what order the client missed things in.
		fa, ferr, ok := s.broker.SubscribeFrames(msgID, startingAfter)
		if ok && ferr != nil {
			return nil, ErrResumeTooOld
		}
		if ok {
			return &ResumeAttachment{
				Message: msg,
				Frames:  fa,
				Cancel:  fa.Cancel,
				Lagged:  fa.Lagged,
			}, nil
		}
		// Entry exists but has recorded nothing yet (the run registered
		// microseconds ago), or predates the ledger: fall back to the
		// folded snapshot rather than refusing the client.
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
