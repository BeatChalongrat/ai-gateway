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
}
