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

type TenantRepo struct {
	db DB
}

func NewTenantRepo(db *pgxpool.Pool) *TenantRepo {
	return &TenantRepo{db: db}
}

func (r *TenantRepo) WithTx(tx pgx.Tx) *TenantRepo {
	return &TenantRepo{db: tx}
}

func (r *TenantRepo) CreateTenant(ctx context.Context, t *model.Tenant) (*model.Tenant, error) {
	const q = `
		INSERT INTO identity.tenants (name, slug, is_personal)
		VALUES ($1, $2, $3)
		RETURNING id, created_at, updated_at
	`
	err := r.db.QueryRow(ctx, q, t.Name, t.Slug, t.IsPersonal).Scan(
		&t.ID, &t.CreatedAt, &t.UpdatedAt,
	)
	if err != nil {
		if isUniqueViolation(err) {
			return nil, ErrDuplicate
		}
		return nil, fmt.Errorf("create tenant: %w", err)
	}
	return t, nil
}

func (r *TenantRepo) GetTenantByID(ctx context.Context, id uuid.UUID) (*model.Tenant, error) {
	const q = `
		SELECT id, name, slug, is_personal, created_at, updated_at
		FROM identity.tenants
		WHERE id = $1
	`
	t := &model.Tenant{}
	err := r.db.QueryRow(ctx, q, id).Scan(
		&t.ID, &t.Name, &t.Slug, &t.IsPersonal, &t.CreatedAt, &t.UpdatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get tenant by id: %w", err)
	}
	return t, nil
}

func (r *TenantRepo) SlugExists(ctx context.Context, slug string) (bool, error) {
	const q = `SELECT EXISTS(SELECT 1 FROM identity.tenants WHERE slug = $1)`
	var exists bool
	if err := r.db.QueryRow(ctx, q, slug).Scan(&exists); err != nil {
		return false, fmt.Errorf("slug exists: %w", err)
	}
	return exists, nil
}
