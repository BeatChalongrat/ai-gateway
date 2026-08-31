package model

// DataExportRequest maps to chat.data_export_requests
// (migration 018 — S9). One row per export the user generated;
// the file lives on local storage at FilePath until ExpiresAt.

import (
	"time"

	"github.com/google/uuid"
)

const (
	ExportTypeMemories      = "memories"
	ExportTypeConversations = "conversations"
	ExportTypeFullAccount   = "full_account"

	ExportStatusPending    = "pending"
	ExportStatusProcessing = "processing"
	ExportStatusCompleted  = "completed"
	ExportStatusFailed     = "failed"
	ExportStatusExpired    = "expired"
)

type DataExportRequest struct {
	ID       uuid.UUID `json:"id"`
	TenantID uuid.UUID `json:"-"`
	UserID   uuid.UUID `json:"-"`

	ExportType string `json:"export_type"`
	Status     string `json:"status"`

	FilePath      string `json:"-"`
	FileSizeBytes int64  `json:"file_size_bytes"`

	ExpiresAt   *time.Time `json:"expires_at,omitempty"`
	RequestedAt time.Time  `json:"requested_at"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
}
