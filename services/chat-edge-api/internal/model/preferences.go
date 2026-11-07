package model

import (
	"time"

	"github.com/google/uuid"
)

// UserPreferences maps to “chat.user_preferences“ — ChatGPT-style
// "custom instructions" that prepend to every conversation's system
// prompt.
type UserPreferences struct {
	ID                        uuid.UUID
	TenantID                  uuid.UUID
	UserID                    uuid.UUID
	CustomInstructionsAbout   string
	CustomInstructionsRespond string
	UpdatedAt                 time.Time
}
