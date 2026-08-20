package model

import (
	"time"

	"github.com/google/uuid"
)

// SharedConversation maps to “chat.shared_conversations“ — a
// public-read share link to a conversation.
type SharedConversation struct {
	ID             uuid.UUID
	ConversationID uuid.UUID
	TenantID       uuid.UUID
	ShareToken     string
	ExpiresAt      *time.Time
	CreatedAt      time.Time
	// T3 — the message ids frozen at share time (migration 033). The
	// public view renders exactly these rows (minus any later hard-
	// deleted ones — deletion wins). nil only on a legacy row the 033
	// backfill missed; Resolve lazily snapshots those on first read.
	SnapshotMessageIDs []uuid.UUID
}
