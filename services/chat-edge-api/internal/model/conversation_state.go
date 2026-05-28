package model

// ConversationState maps to chat.conversation_states (migration 015).
//
// One row per conversation; conversation_id is PK. Tenant + user
// are denormalised onto the row so the read path doesn't have to
// JOIN back to chat.conversations just to verify ownership — which
// matters since the auto-extraction trigger may run from a
// background goroutine where the JOIN cost compounds.

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

type ConversationState struct {
	ConversationID uuid.UUID `json:"conversation_id"`
	TenantID       uuid.UUID `json:"-"`
	UserID         uuid.UUID `json:"-"`

	// CurrentGoal is the one-sentence intent of the conversation.
	// NULL until the first extraction pass populates it; the system-
	// prompt injector treats a NULL goal as "no state yet, skip
	// injection".
	CurrentGoal *string `json:"current_goal"`

	// JSONB columns. Stored as RawMessage so the service layer can
	// pass them through to the system-prompt builder without an
	// intermediate type — the schema inside each blob is free-form
	// (key_context / progress / technical_details for summary;
	// {decision: "..."} / {task: "..."} / {name, status, version}
	// for the array fields).
	StructuredSummary    json.RawMessage `json:"structured_summary"`
	ConfirmedDecisions   json.RawMessage `json:"confirmed_decisions"`
	ActiveArtifacts      json.RawMessage `json:"active_artifacts"`
	OpenTasks            json.RawMessage `json:"open_tasks"`
	RejectedApproaches   json.RawMessage `json:"rejected_approaches"`
	UnresolvedQuestions  json.RawMessage `json:"unresolved_questions"`

	// Compaction tracking. SummarizedUpToMessageID pins the latest
	// message that was folded into the summary so the next
	// extraction pass only processes messages AFTER that point.
	// MessageCountAtSummary lets the auto-trigger decide whether
	// enough new messages have accumulated to re-extract.
	SummarizedUpToMessageID *uuid.UUID `json:"summarized_up_to_message_id"`
	MessageCountAtSummary   int        `json:"message_count_at_summary"`
	SummaryTokenCount       int        `json:"summary_token_count"`

	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}
