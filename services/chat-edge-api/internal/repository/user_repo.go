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

// ErrNotFound is returned by lookup methods when the row doesn't
// exist. The auth layer maps this to "invalid credentials" — never
// echo the specific reason to the client.
var ErrNotFound = errors.New("repository: not found")

// ErrDuplicate is returned by inserts that hit a unique constraint.
// Callers map it to the right HTTP status (409 for register email).
var ErrDuplicate = errors.New("repository: duplicate")

// ErrForbidden marks ownership-check failures. Repos that take a
// tenant scope return this when the caller's tenant doesn't match
// the row's tenant — never leak the actual rows in that case.
var ErrForbidden = errors.New("repository: forbidden")

type UserRepo struct {
	db DB
}

func NewUserRepo(db *pgxpool.Pool) *UserRepo {
	return &UserRepo{db: db}
}

// WithTx returns a repo bound to the given transaction. The
// transactional repo shares no state with the pool-bound one, so
// the caller is responsible for committing / rolling back ``tx``.
func (r *UserRepo) WithTx(tx pgx.Tx) *UserRepo {
	return &UserRepo{db: tx}
}

// CreateUser inserts a new row into ``identity.users`` and returns
// the persisted struct with ``id`` / timestamps populated by the DB.
func (r *UserRepo) CreateUser(ctx context.Context, u *model.User) (*model.User, error) {
	const q = `
		INSERT INTO identity.users
			(tenant_id, email, name, password_hash, role, is_active)
		VALUES ($1, LOWER($2), $3, $4, $5, $6)
		RETURNING id, created_at, updated_at
	`
	err := r.db.QueryRow(ctx, q,
		u.TenantID, u.Email, u.Name, u.PasswordHash, u.Role, u.IsActive,
	).Scan(&u.ID, &u.CreatedAt, &u.UpdatedAt)
	if err != nil {
		if isUniqueViolation(err) {
			return nil, ErrDuplicate
		}
		return nil, fmt.Errorf("create user: %w", err)
	}
	u.Email = lower(u.Email)
	return u, nil
}

// GetUserByEmail looks up a user by case-insensitive email match.
func (r *UserRepo) GetUserByEmail(ctx context.Context, email string) (*model.User, error) {
	const q = `
		SELECT id, tenant_id, email, name, password_hash, role,
		       is_active, created_at, updated_at
		FROM identity.users
		WHERE email = LOWER($1)
	`
	u := &model.User{}
	err := r.db.QueryRow(ctx, q, email).Scan(
		&u.ID, &u.TenantID, &u.Email, &u.Name, &u.PasswordHash,
		&u.Role, &u.IsActive, &u.CreatedAt, &u.UpdatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get user by email: %w", err)
	}
	return u, nil
}

// GetUserByID looks up a user by primary key.
func (r *UserRepo) GetUserByID(ctx context.Context, id uuid.UUID) (*model.User, error) {
	const q = `
		SELECT id, tenant_id, email, name, password_hash, role,
		       is_active, created_at, updated_at
		FROM identity.users
		WHERE id = $1
	`
	u := &model.User{}
	err := r.db.QueryRow(ctx, q, id).Scan(
		&u.ID, &u.TenantID, &u.Email, &u.Name, &u.PasswordHash,
		&u.Role, &u.IsActive, &u.CreatedAt, &u.UpdatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get user by id: %w", err)
	}
	return u, nil
}
