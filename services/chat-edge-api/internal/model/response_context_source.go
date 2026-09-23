package model

// ResponseContextSource maps to chat.response_context_sources
// (migration 017 — S7).
//
// One row per (assistant message, source) pair. The display_label
// is the load-bearing field for the UI; metadata carries
// per-source-type detail (memory_type for memories, conversation
// title for past-conversation hits, filename for files).

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// Source-type enum. Mirrored from the migration's column comment;
// exported as consts so the service + handler layers don't
// sprinkle string literals.
const (
	SourceTypeSavedMemory       = "saved_memory"
	SourceTypeConversationState = "conversation_state"
	SourceTypePastConversation  = "past_conversation"
	SourceTypeFileContext       = "file_context"
	SourceTypeCompactedHistory  = "compacted_history"
	// SourceTypeMemorySaved (FIX130 A3b) — save_memory succeeded during
	// this answer; source_id is the memory, metadata.status says whether it
	// is active or a pending suggestion. No schema change: source_type is a
	// free VARCHAR(30) (migration 017).
	SourceTypeMemorySaved = "memory_saved"
)

type ResponseContextSource struct {
	ID uuid.UUID `json:"id"`

	ResponseMessageID uuid.UUID `json:"response_message_id"`
	TenantID          uuid.UUID `json:"-"`
	UserID            uuid.UUID `json:"-"`
	ConversationID    uuid.UUID `json:"conversation_id"`

	SourceType   string     `json:"source_type"`
	SourceID     *uuid.UUID `json:"source_id,omitempty"`
	DisplayLabel string     `json:"display_label"`

	// Metadata is raw JSON so callers can persist arbitrary nested
	// shapes (memory_type + category for memories,
	// conversation_title + message_snippet for past conversations,
	// filename + file_type for files). nil when not set.
	Metadata json.RawMessage `json:"metadata,omitempty"`

	CreatedAt time.Time `json:"created_at"`
}
