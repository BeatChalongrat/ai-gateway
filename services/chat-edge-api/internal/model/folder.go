package model

import (
	"time"

	"github.com/google/uuid"
)

// Folder maps to “chat.folders“ (migration 022).
//
// A folder is a flat, user-defined bucket for organising conversations in
// the sidebar. tenant_id + user_id scope ownership; position drives the
// user-controlled ordering. Conversations point back via
// chat.conversations.folder_id (nullable — most conversations are unfiled).
type Folder struct {
	ID        uuid.UUID
	TenantID  uuid.UUID
	UserID    uuid.UUID
	Name      string
	Position  int
	CreatedAt time.Time
	UpdatedAt time.Time

	// ConversationCount is populated by ListByUser (LEFT JOIN + COUNT of
	// non-deleted conversations). Zero for a freshly created/renamed folder
	// returned outside the list path.
	ConversationCount int
}
