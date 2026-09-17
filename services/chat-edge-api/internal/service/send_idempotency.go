package service

// FIX121 — a send that arrives twice is one turn.
//
// The run id (FIX119) is the one id a turn carries through every layer, and
// a client may supply it as X-Request-Id. Until now supplying it changed
// nothing about the WRITE: the same POST twice ran two full turns — two user
// rows, two assistant rows with the same run_id, two provider calls, two
// charges. No model provider de-duplicates for us (the providers' request
// ids are for finding a request afterwards, not for refusing a repeat), so
// the server that writes the row is the only place this can be decided.
//
// A SUPPLIED id is therefore the idempotency key of the send, in the shape
// everybody already knows from payment APIs:
//
//   - first time: the turn runs, and the user row records the key;
//   - same key, same content: no second turn — the caller is given the first
//     one (the handler replays it; see MessageHandler.replaySend);
//   - same key, different content: refused (ErrIdempotencyKeyReused) — the
//     key names one request, and this is not that request;
//   - same key while the first request has not written its answer row yet:
//     ErrSendInProgress.
//
// An id the middleware minted is unique by construction and is not a key:
// such a send behaves exactly as before, down to the row (its user row still
// has run_id NULL).

import (
	"context"
	"errors"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

var (
	// ErrIdempotencyKeyReused — the key is known and names a different
	// request. The handler answers 409.
	ErrIdempotencyKeyReused = errors.New("message: idempotency key reused with different content")
	// ErrSendInProgress — the key is known but its turn has no answer row
	// yet (two copies of one request raced). The handler answers 409; the
	// caller retries and gets the replay.
	ErrSendInProgress = errors.New("message: a request with this idempotency key is in progress")
	// ErrSendDuplicate — prepareSend lost the race on the user-row insert
	// (the unique index of migration 047 refused it). The handler resolves
	// it through FindSendReplay like any other repeat.
	ErrSendDuplicate = errors.New("message: duplicate send for this idempotency key")
)

// SendReplay is the turn a repeated send names.
type SendReplay struct {
	UserMessage      *model.Message
	AssistantMessage *model.Message
}

// FindSendReplay looks up the turn a supplied idempotency key already
// produced in this conversation. (nil, nil) means "never seen — run the
// turn". Ownership is the caller's: a conversation that is not the caller's
// answers (nil, nil) and the normal send path then refuses it with its usual
// non-leaking 404.
func (s *MessageService) FindSendReplay(
	ctx context.Context, tenantID, userID, convID uuid.UUID, key, content string,
) (*SendReplay, error) {
	key = strings.TrimSpace(key)
	if key == "" {
		return nil, nil
	}
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, nil
		}
		return nil, err
	}
	if conv.UserID != userID {
		return nil, nil
	}
	userMsg, err := s.messages.FindByRun(ctx, tenantID, convID, key, "user")
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, nil
		}
		return nil, err
	}
	if userMsg.ContentMasked != content {
		return nil, ErrIdempotencyKeyReused
	}
	asstMsg, err := s.messages.FindByRun(ctx, tenantID, convID, key, "assistant")
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrSendInProgress
		}
		return nil, err
	}
	return &SendReplay{UserMessage: userMsg, AssistantMessage: asstMsg}, nil
}
