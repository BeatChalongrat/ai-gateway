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

// RefreshTokenRepo handles refresh-token rotation and revocation.
// Kept separate from ``UserRepo`` so the rotation + replay-detection
// SQL lives in one place where security audits can reason about it.
type RefreshTokenRepo struct {
	db DB
}

func NewRefreshTokenRepo(db *pgxpool.Pool) *RefreshTokenRepo {
	return &RefreshTokenRepo{db: db}
}

func (r *RefreshTokenRepo) WithTx(tx pgx.Tx) *RefreshTokenRepo {
	return &RefreshTokenRepo{db: tx}
}

// CreateRefreshToken inserts a new row. The caller is expected to
// have already revoked the predecessor in the same family (refresh
// rotation invariant: at most one active token per family).
func (r *RefreshTokenRepo) CreateRefreshToken(ctx context.Context, t *model.RefreshToken) error {
	const q = `
		INSERT INTO identity.refresh_tokens
			(user_id, token_hash, family_id, expires_at)
		VALUES ($1, $2, $3, $4)
		RETURNING id, created_at
	`
	err := r.db.QueryRow(ctx, q,
		t.UserID, t.TokenHash, t.FamilyID, t.ExpiresAt,
	).Scan(&t.ID, &t.CreatedAt)
	if err != nil {
		return fmt.Errorf("create refresh token: %w", err)
	}
	return nil
}

// GetRefreshTokenByHash returns the row matching the SHA-256 hex
// of the raw token. Returns ``ErrNotFound`` when no row exists.
//
// Note: this method does NOT check ``revoked_at`` or ``expires_at``
// — that gating lives in the AuthService.Refresh flow so the same
// row can be inspected for replay detection (a revoked token being
// re-presented is the trigger that revokes the whole family).
func (r *RefreshTokenRepo) GetRefreshTokenByHash(ctx context.Context, hash string) (*model.RefreshToken, error) {
	const q = `
		SELECT id, user_id, token_hash, family_id, expires_at, revoked_at, created_at
		FROM identity.refresh_tokens
		WHERE token_hash = $1
	`
	t := &model.RefreshToken{}
	err := r.db.QueryRow(ctx, q, hash).Scan(
		&t.ID, &t.UserID, &t.TokenHash, &t.FamilyID,
		&t.ExpiresAt, &t.RevokedAt, &t.CreatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get refresh token: %w", err)
	}
	return t, nil
}

// RevokeRefreshToken sets ``revoked_at = NOW()`` on a single row.
// Idempotent — re-revoking an already-revoked token is a no-op.
func (r *RefreshTokenRepo) RevokeRefreshToken(ctx context.Context, id uuid.UUID) error {
	const q = `
		UPDATE identity.refresh_tokens
		SET revoked_at = NOW()
		WHERE id = $1 AND revoked_at IS NULL
	`
	if _, err := r.db.Exec(ctx, q, id); err != nil {
		return fmt.Errorf("revoke refresh token: %w", err)
	}
	return nil
}

// RevokeRefreshTokenFamily revokes EVERY token in the family.
// Used by:
//
//   - Logout: revoke the whole family so all devices are signed out.
//   - Replay detection: a revoked token being re-used by an attacker
//     means the family is compromised — burn it.
func (r *RefreshTokenRepo) RevokeRefreshTokenFamily(ctx context.Context, familyID uuid.UUID) error {
	const q = `
		UPDATE identity.refresh_tokens
		SET revoked_at = NOW()
		WHERE family_id = $1 AND revoked_at IS NULL
	`
	if _, err := r.db.Exec(ctx, q, familyID); err != nil {
		return fmt.Errorf("revoke refresh token family: %w", err)
	}
	return nil
}
