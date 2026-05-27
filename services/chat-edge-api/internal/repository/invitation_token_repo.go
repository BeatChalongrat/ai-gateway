package repository

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// InvitationTokenRepo handles the consumer side of invitations
// (lookup + mark-used). The admin-side creation endpoint lands in
// a later Phase 1 session.
type InvitationTokenRepo struct {
	db DB
}

func NewInvitationTokenRepo(db *pgxpool.Pool) *InvitationTokenRepo {
	return &InvitationTokenRepo{db: db}
}

func (r *InvitationTokenRepo) WithTx(tx pgx.Tx) *InvitationTokenRepo {
	return &InvitationTokenRepo{db: tx}
}

// GetInvitationByToken returns the invitation matching the raw
// token string. The token column has a UNIQUE constraint so this
// is a primary-key-style lookup.
func (r *InvitationTokenRepo) GetInvitationByToken(ctx context.Context, token string) (*model.InvitationToken, error) {
	const q = `
		SELECT id, tenant_id, token, created_by, used_by,
		       expires_at, used_at, created_at
		FROM identity.invitation_tokens
		WHERE token = $1
	`
	i := &model.InvitationToken{}
	err := r.db.QueryRow(ctx, q, token).Scan(
		&i.ID, &i.TenantID, &i.Token, &i.CreatedBy, &i.UsedBy,
		&i.ExpiresAt, &i.UsedAt, &i.CreatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get invitation: %w", err)
	}
	return i, nil
}

// UseInvitation marks the invitation as consumed by “usedBy“.
// The WHERE clause includes “used_at IS NULL“ so a concurrent
// double-redeem from two browser tabs is rejected — the second
// UPDATE affects zero rows and the helper returns a sentinel error.
func (r *InvitationTokenRepo) UseInvitation(ctx context.Context, id, usedBy uuid.UUID) error {
	const q = `
		UPDATE identity.invitation_tokens
		SET used_by = $2, used_at = NOW()
		WHERE id = $1 AND used_at IS NULL
	`
	tag, err := r.db.Exec(ctx, q, id, usedBy)
	if err != nil {
		return fmt.Errorf("use invitation: %w", err)
	}
	if tag.RowsAffected() == 0 {
		// Either the row doesn't exist or it was already used. Both
		// map to "invalid invitation" at the service layer.
		return ErrNotFound
	}
	return nil
}
