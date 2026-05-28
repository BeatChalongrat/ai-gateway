package model

import (
	"time"

	"github.com/google/uuid"
)

// Message maps to “chat.messages_masked“.
//
// CRITICAL: “ContentMasked“ is the ONLY content field on this
// struct because the database table only stores masked content.
// Raw user prompts (pre-mask) and restored AI replies (post-restore)
// are transient — they cross the wire to the LLM via Gateway, get
// streamed back to the browser, and then disappear. The chat-edge-
// api stores the masked snapshot from Gateway's response. The
// “SecureTraceID“ points at the Gateway's secure-trace row for
// admin audits.
type Message struct {
	ID               uuid.UUID
	ConversationID   uuid.UUID
	TenantID         uuid.UUID
	UserID           uuid.UUID
	Role             string // "user" | "assistant" | "system"
	ContentMasked    string
	Status           string // "pending" | "streaming" | "completed" | "cancelled" | "error"
	ModelID          *string
	TokenUsageInput  *int
	TokenUsageOutput *int
	SecureTraceID    *uuid.UUID
	CreatedAt        time.Time
	// Branching — see migration 009. ParentID is NULL for messages
	// that haven't been edited and aren't part of an edit's
	// downstream chain; when set, points at the fork-root user
	// message id and is shared by all siblings + descendants in
	// that fork group.
	ParentID    *uuid.UUID
	BranchIndex int
	IsActive    bool

	// T4 — Thinking Mode (migration 019). Reasoning / thinking
	// summary the model emitted for this assistant turn. Stored
	// masked; raw reasoning text is filtered by the Gateway's
	// outbound DLP scan on the reasoning channel before the
	// chat-edge ever sees it. Nullable — non-reasoning turns and
	// pre-T4 rows return ``nil``.
	ReasoningContent *string
}
