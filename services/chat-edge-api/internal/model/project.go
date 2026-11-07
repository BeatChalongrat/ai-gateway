package model

import (
	"time"

	"github.com/google/uuid"
)

// Project maps to “chat.projects“ (migration 029).
//
// A project is a folder-like container that ALSO carries shared context:
// per-project instructions plus an optional knowledge-base binding. Every
// conversation assigned to the project (conversations.project_id) inherits
// that context at prompt-compile time — instructions render in Layer 10
// after the user's personal custom instructions; the KB feeds RAG when the
// conversation has no explicit KB of its own.
type Project struct {
	ID              uuid.UUID
	TenantID        uuid.UUID
	UserID          uuid.UUID
	Name            string
	Instructions    string
	KnowledgeBaseID *uuid.UUID
	CreatedAt       time.Time
	UpdatedAt       time.Time

	// ConversationCount is populated by ListByUser (LEFT JOIN + COUNT of
	// non-deleted conversations). Zero outside the list path.
	ConversationCount int
}
