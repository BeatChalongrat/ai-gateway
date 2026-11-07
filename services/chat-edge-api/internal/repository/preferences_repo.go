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

// PreferencesRepo owns chat.user_preferences. Exactly one row per
// (tenant_id, user_id); upsert semantics on writes.
type PreferencesRepo struct {
	db DB
}

func NewPreferencesRepo(db *pgxpool.Pool) *PreferencesRepo {
	return &PreferencesRepo{db: db}
}

// Get returns the user's preferences. ErrNotFound when no row
// exists — callers treat that as "default empty preferences" so the
// GET endpoint can short-circuit to {"":"", "":""}.
func (r *PreferencesRepo) Get(ctx context.Context, tenantID, userID uuid.UUID) (*model.UserPreferences, error) {
	const q = `
		SELECT id, tenant_id, user_id, custom_instructions_about,
		       custom_instructions_respond, updated_at
		FROM chat.user_preferences
		WHERE tenant_id = $1 AND user_id = $2
	`
	p := &model.UserPreferences{}
	err := r.db.QueryRow(ctx, q, tenantID, userID).Scan(
		&p.ID, &p.TenantID, &p.UserID, &p.CustomInstructionsAbout,
		&p.CustomInstructionsRespond, &p.UpdatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get preferences: %w", err)
	}
	return p, nil
}

// Upsert writes the two instruction fields. Either field may be the
// empty string. Returns the post-write row so the handler can echo
// the canonical state back to the caller.
func (r *PreferencesRepo) Upsert(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	about, respond string,
) (*model.UserPreferences, error) {
	const q = `
		INSERT INTO chat.user_preferences
			(tenant_id, user_id, custom_instructions_about, custom_instructions_respond)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (tenant_id, user_id) DO UPDATE
		SET custom_instructions_about   = EXCLUDED.custom_instructions_about,
		    custom_instructions_respond = EXCLUDED.custom_instructions_respond,
		    updated_at                  = NOW()
		RETURNING id, tenant_id, user_id, custom_instructions_about,
		          custom_instructions_respond, updated_at
	`
	p := &model.UserPreferences{}
	err := r.db.QueryRow(ctx, q, tenantID, userID, about, respond).Scan(
		&p.ID, &p.TenantID, &p.UserID, &p.CustomInstructionsAbout,
		&p.CustomInstructionsRespond, &p.UpdatedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("upsert preferences: %w", err)
	}
	return p, nil
}
