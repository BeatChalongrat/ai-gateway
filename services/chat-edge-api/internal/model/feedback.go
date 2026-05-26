package model

import (
	"time"

	"github.com/google/uuid"
)

// Feedback maps to ``chat.feedback``.
//
// Phase 1: thumbs-up / thumbs-down only. The ``Comment`` field is
// deliberately absent — free-text feedback would require its own
// DLP sanitization pass (user might type their email or phone into
// a complaint), which is deferred to Phase 2.
type Feedback struct {
	ID        uuid.UUID
	MessageID uuid.UUID
	TenantID  uuid.UUID
	UserID    uuid.UUID
	Rating    string // "thumbs_up" | "thumbs_down"
	CreatedAt time.Time
}
