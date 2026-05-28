package model

import (
	"time"

	"github.com/google/uuid"
)

// Conversation maps to “chat.conversations“.
//
// “Title“ is the DLP-sanitized conversation label. The title
// service hits Gateway with the first user message; Gateway returns
// either the raw value (when no PII detected) or the pseudonymized
// form. Either way, the chat-edge-api persists that result and
// never sees / stores raw PII in this field.
type Conversation struct {
	ID           uuid.UUID
	TenantID     uuid.UUID
	UserID       uuid.UUID
	Title        string
	ModelID      string
	MessageCount int
	IsDeleted    bool
	DeletedAt    *time.Time
	CreatedAt    time.Time
	UpdatedAt    time.Time
	// KnowledgeBaseID is the per-conversation RAG binding. Nullable
	// because most conversations have no KB attached. When set, the
	// message flow injects retrieved chunks into the system prompt.
	KnowledgeBaseID *uuid.UUID

	// MemoryEnabled toggles the cross-conversation memory layer for
	// THIS conversation (S1 — migration 014). Default true; set to
	// false for "temporary chats" the user doesn't want feeding into
	// their long-term memory.
	//
	// When false the message flow:
	//   - skips injecting the <saved_user_memory> system-prompt block
	//   - omits the save_memory / list_memories tools from the
	//     gateway request's tool list
	MemoryEnabled bool

	// T4 — Thinking Mode (migration 019). ReasoningEnabled is the
	// master toggle; when true the message-build path adds the
	// ``reasoning`` block to the Gateway request and the SSE pump
	// forwards reasoning_summary_delta + analysis_status frames to
	// the client. Default false so existing conversations are
	// byte-identical to their pre-T4 behaviour.
	//
	// ReasoningMode mirrors the public ReasoningConfig.mode values
	// (off / auto / low / medium / high). Persisted even when
	// ReasoningEnabled is false so the previously-chosen depth
	// restores when the user re-enables.
	//
	// ShowReasoningSummary gates wire emission of the reasoning
	// channel. When false the Gateway still asks the model to
	// think (better answers) but the chat-edge SSE pump drops the
	// reasoning frames before they reach the browser.
	ReasoningEnabled     bool
	ReasoningMode        string
	ShowReasoningSummary bool
}
