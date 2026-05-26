package model

import (
	"time"

	"github.com/google/uuid"
)

// SharedConversation maps to ``chat.shared_conversations`` — a
// public-read share link to a conversation.
type SharedConversation struct {
	ID             uuid.UUID
	ConversationID uuid.UUID
	TenantID       uuid.UUID
	ShareToken     string
	ExpiresAt      *time.Time
	CreatedAt      time.Time
}
