package model

import (
	"time"

	"github.com/google/uuid"
)

// ConversationFile maps to ``chat.conversation_files``.
//
// The struct's lifetime tracks the file's journey:
//
//	1. upload handler creates the row with extraction_status="pending"
//	2. file_service runs the extractor → status becomes "completed" /
//	   "failed" / "unsupported" and ExtractedText is populated
//	3. message handler patches MessageID once the user presses send
//
// MessageID stays nil for "abandoned" uploads (user removed the
// pending card before sending). A future cleanup job can sweep
// rows where MessageID IS NULL and created_at < NOW() - 24h.
type ConversationFile struct {
	ID                uuid.UUID
	ConversationID    uuid.UUID
	MessageID         *uuid.UUID
	TenantID          uuid.UUID
	Filename          string // sanitized, used on disk
	OriginalFilename  string // what the user typed
	ContentType       string
	FileSizeBytes     int64
	StoragePath       string // absolute path on the local filesystem
	ExtractedText     *string
	ExtractionStatus  string // pending | processing | completed | failed | unsupported
	CreatedAt         time.Time
	UpdatedAt         time.Time
}
