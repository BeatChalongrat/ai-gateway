package model

import (
	"time"

	"github.com/google/uuid"
)

// Conversation maps to ``chat.conversations``.
//
// ``Title`` is the DLP-sanitized conversation label. The title
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
}
