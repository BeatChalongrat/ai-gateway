package model

import (
	"time"

	"github.com/google/uuid"
)

// InvitationToken maps to ``identity.invitation_tokens``.
//
// Phase 1: invitations are the ONLY way to join an existing tenant.
// A self-registering user gets an auto-created personal tenant; an
// admin generates an invitation to onboard teammates into a shared
// tenant. The admin-side creation endpoint lands in a later session;
// this model and the consumer-side ``UseInvitation`` repo method
// are shipped now so the register flow can honour the
// ``invitation_token`` field.
type InvitationToken struct {
	ID        uuid.UUID
	TenantID  uuid.UUID
	Token     string
	CreatedBy uuid.UUID
	UsedBy    *uuid.UUID
	ExpiresAt time.Time
	UsedAt    *time.Time
	CreatedAt time.Time
}

// Usable returns true iff the invitation can still be consumed:
// not previously used AND not expired.
func (i *InvitationToken) Usable(now time.Time) bool {
	if i.UsedAt != nil {
		return false
	}
	return now.Before(i.ExpiresAt)
}
