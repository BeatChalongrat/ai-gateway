package service

// FIX125 — one conversation, one turn at a time, and nothing written into a
// conversation after it was deleted.
//
// M5 (busy guard). Two tabs sending into one conversation used to run two
// turns side by side: two user rows, two assistant rows, two streams whose
// histories each missed the other's question, and activeStreams (keyed by
// conversation) overwritten by whichever registered last, so Stop reached
// only one of them. A conversation now holds at most one in-flight turn;
// a second send, regenerate, edit or question-answer while one runs is
// refused with ErrConversationBusy (HTTP 409 CONVERSATION_BUSY) BEFORE it
// writes anything.
//
// Single-replica assumption: the guard is an in-process sync.Map, like
// activeStreams and the resume broker (stream_broker.go). chat-edge runs as
// one replica; a second replica would need the guard in Redis or Postgres.
//
// M2 (deleted conversation). The finish callbacks run after the stream
// ends — possibly after the person deleted the conversation. They check
// conversationGone first and then write nothing: no assistant content, no
// terminal, no reasoning, no sources, no finalizeConversation (so no
// message_count bump and no title / state / preference extraction calls to
// the gateway). The content writes also carry a SQL guard
// (repository.liveConversationGuard) for the window between check and
// write.

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// ErrConversationBusy — the conversation already has a turn in flight.
// The handler answers 409 CONVERSATION_BUSY; nothing was written.
var ErrConversationBusy = errors.New("message: conversation already has a turn in progress")

// turnStaleAfter bounds how long a claim can hold a conversation. A claim
// is released by the turn's finish (or by its own error paths); this is
// only the backstop for a claim whose owner vanished without either, so a
// leak cannot lock a conversation until the next restart. Far longer than
// any real turn (the image path's own timeout is 180 s).
const turnStaleAfter = 30 * time.Minute

// turnClaim is one in-flight turn's hold on its conversation.
type turnClaim struct {
	key   string // the send's idempotency key (FIX121), "" when none
	since time.Time
	once  sync.Once
}

// claimTurn takes the conversation for one turn. The returned release is
// idempotent and only ever frees THIS claim.
//
// key is the send's idempotency key. A second copy of the SAME request
// (same client-chosen X-Request-Id) is not "busy": it is FIX121's repeat,
// and gets ErrSendDuplicate so the handler answers it through the replay
// path exactly as before the guard existed.
func (s *MessageService) claimTurn(convID uuid.UUID, key string) (func(), error) {
	c := &turnClaim{key: key, since: time.Now()}
	for {
		v, loaded := s.turns.LoadOrStore(convID, c)
		if !loaded {
			return func() {
				c.once.Do(func() { s.turns.CompareAndDelete(convID, c) })
			}, nil
		}
		cur := v.(*turnClaim)
		if time.Since(cur.since) > turnStaleAfter {
			if s.turns.CompareAndSwap(convID, cur, c) {
				slog.Warn("conversation turn claim was stale; taken over",
					"conversation_id", convID.String(), "held_for", time.Since(cur.since).String())
				return func() {
					c.once.Do(func() { s.turns.CompareAndDelete(convID, c) })
				}, nil
			}
			continue
		}
		if key != "" && cur.key == key {
			return nil, ErrSendDuplicate
		}
		return nil, ErrConversationBusy
	}
}

// TurnInFlight reports whether the conversation holds a turn claim right
// now (tests and diagnostics).
func (s *MessageService) TurnInFlight(convID uuid.UUID) bool {
	_, ok := s.turns.Load(convID)
	return ok
}

// conversationGone reports whether the conversation was deleted (or purged)
// since the turn began. A read error answers false: the finish then writes
// as it always did, and the SQL guard on the content write still holds.
func (s *MessageService) conversationGone(ctx context.Context, conv *model.Conversation) bool {
	if s.conversations == nil || conv == nil {
		return false
	}
	gone, err := s.conversations.IsDeleted(ctx, conv.ID)
	if err != nil {
		slog.Warn("finish: could not read conversation state", "conversation_id", conv.ID.String(), "err", err.Error())
		return false
	}
	if gone {
		slog.Info("finish_skipped_conversation_deleted", "conversation_id", conv.ID.String())
	}
	return gone
}
