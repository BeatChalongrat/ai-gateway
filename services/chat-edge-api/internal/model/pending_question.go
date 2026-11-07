package model

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// FIX74 — a parked turn: the model asked the user a question on the web
// path and the turn is waiting, in the database, for the answer.
//
// Status lifecycle: pending → answered | skipped (the user chose) |
// cancelled (Stop / POST /cancel) | superseded (the user sent a new
// message instead) | expired (nobody answered within the TTL). Only
// ``pending`` renders a card; nothing here is ever answered by the server.
const (
	PendingQuestionPending    = "pending"
	PendingQuestionAnswered   = "answered"
	PendingQuestionSkipped    = "skipped"
	PendingQuestionCancelled  = "cancelled"
	PendingQuestionSuperseded = "superseded"
	PendingQuestionExpired    = "expired"
)

// MessageStatusWaitingUser is the assistant row's status while its turn
// is parked. Distinct from "streaming" on purpose: the interrupted-turn
// sweep (service/interrupted_sweep.go, FIX131) that settles a "streaming"
// row left behind by a restart must never touch a row that is deliberately
// waiting.
const MessageStatusWaitingUser = "waiting_user"

type PendingQuestion struct {
	ID             uuid.UUID
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	MessageID      uuid.UUID
	ToolCallID     string
	// Questions is the validated card payload, as the client renders it.
	Questions json.RawMessage
	// State is the paused turn (service.parkedTurn), opaque here.
	State     json.RawMessage
	Status    string
	CreatedAt time.Time
	ExpiresAt time.Time
	ClosedAt  *time.Time
}

func (q *PendingQuestion) IsPending(now time.Time) bool {
	return q != nil && q.Status == PendingQuestionPending && now.Before(q.ExpiresAt)
}
