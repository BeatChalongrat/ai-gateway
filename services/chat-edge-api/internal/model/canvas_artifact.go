package model

import (
	"time"

	"github.com/google/uuid"
)

// CanvasArtifact maps to “chat.canvas_artifacts“ (migration 030).
//
// A canvas artifact is a versioned inline artifact (html / react /
// mermaid / svg / markdown) rendered in a conversation's canvas panel.
// Every row is an IMMUTABLE version: rows sharing (ConversationID,
// ArtifactKey) form one artifact's lineage and the latest version is
// MAX(Version). Reverting appends a new version whose content is copied
// from an older row — history is never mutated or deleted (same linear-
// lineage decision as the Phase-4 workspace rollback).
type CanvasArtifact struct {
	ID             uuid.UUID
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	// MessageID optionally links the assistant message that produced this
	// version. Nil when unknown or after the message was pruned (the FK
	// is ON DELETE SET NULL).
	MessageID   *uuid.UUID
	ArtifactKey string
	Type        string
	// Title is optional display metadata (nil when the client sent none).
	Title     *string
	Content   string
	Version   int
	CreatedAt time.Time
}
