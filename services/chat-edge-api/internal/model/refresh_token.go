package model

import (
	"time"

	"github.com/google/uuid"
)

// RefreshToken maps to ``identity.refresh_tokens``.
//
// ``TokenHash`` is the SHA-256 hex digest of the raw token the
// client carries in their HttpOnly cookie. Storing the hash means
// a database dump alone cannot replay any session.
//
// ``FamilyID`` is the rotation anchor: a single login mints a
// family with seed token T1; refresh revokes T1 and issues T2 under
// the same family; "logout everywhere" revokes the whole family.
// Stolen-token reuse detection = "any token in the family is used
// after revocation" → revoke the whole family. See
// AuthService.Refresh for the replay detection branch.
type RefreshToken struct {
	ID        uuid.UUID
	UserID    uuid.UUID
	TokenHash string
	FamilyID  uuid.UUID
	ExpiresAt time.Time
	RevokedAt *time.Time
	CreatedAt time.Time
}

// Active returns true iff the token can still be used: it has not
// been revoked AND has not expired.
func (t *RefreshToken) Active(now time.Time) bool {
	if t.RevokedAt != nil {
		return false
	}
	return now.Before(t.ExpiresAt)
}
