package model

import (
	"time"

	"github.com/google/uuid"
)

// ConversationFile maps to “chat.conversation_files“.
//
// The struct's lifetime tracks the file's journey:
//
//  1. upload handler creates the row with extraction_status="pending"
//  2. file_service runs the extractor → status becomes "completed" /
//     "failed" / "unsupported" and ExtractedText is populated
//  3. message handler patches MessageID once the user presses send
//
// MessageID stays nil for "abandoned" uploads (user removed the
// pending card before sending). A future cleanup job can sweep
// rows where MessageID IS NULL and created_at < NOW() - 24h.
type ConversationFile struct {
	ID               uuid.UUID
	ConversationID   uuid.UUID
	MessageID        *uuid.UUID
	TenantID         uuid.UUID
	// UserID denormalises the owning conversation's user_id onto the
	// file row (migration 013). Populated on every Create path so
	// ownership checks + the upcoming memory system's "this user's
	// artifacts" queries don't have to join conversations.
	UserID           uuid.UUID
	Filename         string // sanitized, used on disk
	OriginalFilename string // what the user typed
	ContentType      string
	FileSizeBytes    int64
	StoragePath      string // absolute path on the local filesystem
	ExtractedText    *string
	ExtractionStatus string // pending | processing | completed | failed | unsupported
	CreatedAt        time.Time
	UpdatedAt        time.Time

	// Phase 4 — Excel Smart Workspace fields (migration 012).
	//
	// InspectionJSON caches excel_inspector.py's output verbatim so
	// the message-service can fold the structure into the system
	// prompt on every send without re-shelling into Python. Nil for
	// non-Excel files.
	//
	// IsGenerated flags files produced by an AI edit plan (vs.
	// direct user uploads). The frontend renders these as result
	// cards with version + change-log context.
	//
	// ParentFileID + Version walk the lineage so the version-history
	// endpoint can list every iteration of a workbook the user has
	// asked the AI to edit. Version starts at 1 for originals.
	//
	// ChangeLog stores the joined output of excel_executor.py's
	// per-action change messages so the result card can render
	// "Created sheet X, wrote N cells, …" without re-parsing the
	// plan or the executor stdout.
	InspectionJSON *string
	IsGenerated    bool
	ParentFileID   *uuid.UUID
	Version        int
	ChangeLog      *string

	