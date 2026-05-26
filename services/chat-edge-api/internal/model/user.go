package model

import (
	"time"

	"github.com/google/uuid"
)

// User maps to ``identity.users``.
//
// ``PasswordHash`` is the argon2id PHC string from auth.HashPassword
// — never stored or transmitted plaintext. Repositories scan into
// this struct; handlers do NOT serialise PasswordHash to the wire.
type User struct {
	ID           uuid.UUID
	TenantID     uuid.UUID
	Email        string
	Name         string
	PasswordHash string
	Role         string // "user" | "admin" | "super_admin"
	IsActive     bool
	CreatedAt    time.Time
	UpdatedAt    time.Time
}
