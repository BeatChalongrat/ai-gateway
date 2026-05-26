package model

import (
	"time"

	"github.com/google/uuid"
)

// Message maps to ``chat.messages_masked``.
//
// CRITICAL: ``ContentMasked`` is the ONLY content field on this
// struct because the database table only stores masked content.
// Raw user prompts (pre-mask) and restored AI replies (post-restore)
// are transient — they cross the wire to the LLM via Gateway, get
// streamed back to the browser, and then disappear. The chat-edge-
// api stores the masked snapshot from Gateway's response. The
// ``SecureTraceID`` points at the Gateway's secure-trace row for
// admin audits.
type Message struct {
	ID                uuid.UUID
	ConversationID    uuid.UUID
	TenantID          uuid.UUID
	UserID            uuid.UUID
	Role              string // "user" | "assistant" | "system"
	ContentMasked     string
	Status            string // "pending" | "streaming" | "completed" | "cancelled" | "error"
	ModelID           *string
	TokenUsageInput   *int
	TokenUsageOutput  *int
	SecureTraceID     *uuid.UUID
	CreatedAt         time.Time
}
