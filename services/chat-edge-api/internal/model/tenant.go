package model

import (
	"time"

	"github.com/google/uuid"
)

// Tenant maps to ``identity.tenants``.
//
// ``IsPersonal`` distinguishes auto-created single-user tenants
// from real org tenants (multi-user, invite-flow). The auth flow
// creates a personal tenant on first registration; an admin can
// promote it later.
type Tenant struct {
	ID         uuid.UUID
	Name       string
	Slug       string
	IsPersonal bool
	CreatedAt  time.Time
	UpdatedAt  time.Time
}
