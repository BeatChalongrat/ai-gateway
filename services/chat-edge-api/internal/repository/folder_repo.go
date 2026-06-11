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

// FolderRepo — CRUD over chat.folders plus the membership count join.
// Mirrors ConversationRepo: a DB-querier field so the same methods run on
// either *pgxpool.Pool or pgx.Tx via WithTx, every query is (tenant_id,
// user_id)-scoped, and a 0-row UPDATE/DELETE surfaces ErrNotFound.
type FolderRepo struct {
	db DB
}

func NewFolderRepo(db *pgxpool.Pool) *FolderRepo {
	return &FolderRepo{db: db}
}

func (r *FolderRepo) WithTx(tx pgx.Tx) *FolderRepo {
	return &FolderRepo{db: tx}
}

// ListByUser returns the user's folders ordered by (position, created_at),
// each carrying a live conversation_count of its NON-deleted conversations.
// The LEFT JOIN keeps empty folders in the result (count 0).
func (r *FolderRepo) ListByUser(ctx context.Context, tenantID, userID uuid.UUID) ([]model.Folder, error) {
	const q = `
		SELECT f.id, f.tenant_id, f.user_id, f.name, f.position,
		       f.created_at, f.updated_at,
		       COUNT(c.id) FILTER (WHERE c.is_deleted = false) AS conversation_count
		FROM chat.folders f
		LEFT JOIN chat.conversations c ON c.folder_id = f.id
		WHERE f.tenant_id = $1 AND f.user_id = $2
		GROUP BY f.id
		ORDER BY f.position, f.created_at
	`
	rows, err := r.db.Query(ctx, q, tenantID, userID)
	if err != nil {
		return nil, fmt.Errorf("list folders: %w", err)
	}
	defer rows.Close()
	out := make([]model.Folder, 0)
	for rows.Next() {
		var f model.Folder
		if err := rows.Scan(
			&f.ID, &f.TenantID, &f.UserID, &f.Name, &f.Position,
			&f.CreatedAt, &f.UpdatedAt, &f.ConversationCount,
		); err != nil {
			return nil, fmt.Errorf("list folders scan: %w", err)
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// GetByID returns a single folder scoped to the owner. Used to validate a
// move target belongs to the caller before reassigning a conversation.
func (r *FolderRepo) GetByID(ctx context.Context, tenantID, userID, folderID uuid.UUID) (*model.Folder, error) {
	const q = `
		SELECT id, tenant_id, user_id, name, position, created_at, updated_at
		FROM chat.folders
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3
	`
	f := &model.Folder{}
	err := r.db.QueryRow(ctx, q, folderID, tenantID, userID).Scan(
		&f.ID, &f.TenantID, &f.UserID, &f.Name, &f.Position, &f.CreatedAt, &f.UpdatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get folder by id: %w", err)
	}
	return f, nil
}

// Create inserts a folder at the end of the user's ordering. The position
// subquery (MAX+1) runs in the same statement so concurrent creates can't
// collide on a stale read.
func (r *FolderRepo) Create(ctx context.Context, tenantID, userID uuid.UUID, name string) (*model.Folder, error) {
	const q = `
		INSERT INTO chat.folders (tenant_id, user_id, name, position)
		VALUES ($1, $2, $3,
		        (SELECT COALESCE(MAX(position), 0) + 1
		           FROM chat.folders WHERE tenant_id = $1 AND user_id = $2))
		RETURNING id, tenant_id, user_id, name, position, created_at, updated_at
	`
	f := &model.Folder{}
	err := r.db.QueryRow(ctx, q, tenantID, userID, name).Scan(
		&f.ID, &f.TenantID, &f.UserID, &f.Name, &f.Position, &f.CreatedAt, &f.UpdatedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("create folder: %w", err)
	}
	return f, nil
}

// Update renames a folder. Owner-scoped via the WHERE clause; a 0-row
// result (wrong owner or missing) is ErrNotFound.
func (r *FolderRepo) Update(ctx context.Context, tenantID, userID, folderID uuid.UUID, name string) (*model.Folder, error) {
	const q = `
		UPDATE chat.folders
		SET name = $4, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3
		RETURNING id, tenant_id, user_id, name, position, created_at, updated_at
	`
	f := &model.Folder{}
	err := r.db.QueryRow(ctx, q, folderID, tenantID, userID, name).Scan(
		&f.ID, &f.TenantID, &f.UserID, &f.Name, &f.Position, &f.CreatedAt, &f.UpdatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("update folder: %w", err)
	}
	return f, nil
}

// Delete removes a folder. The conversations.folder_id FK is
// ON DELETE SET NULL, so the folder's chats become unfiled rather than
// being deleted. Owner-scoped; 0 rows → ErrNotFound.
func (r *FolderRepo) Delete(ctx context.Context, tenantID, userID, folderID uuid.UUID) error {
	const q = `DELETE FROM chat.folders WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	tag, err := r.db.Exec(ctx, q, folderID, tenantID, userID)
	if err != nil {
		return fmt.Errorf("delete folder: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// Reorder rewrites every folder's position to its index in folderIDs
// (1-based). Done in a single statement via ``unnest ... WITH ORDINALITY``
// so it is atomic without an explicit transaction; only the caller's own
// folders are touched (a foreign id in the list simply matches nothing).
func (r *FolderRepo) Reorder(ctx context.Context, tenantID, userID uuid.UUID, folderIDs []uuid.UUID) error {
	if len(folderIDs) == 0 {
		return nil
	}
	const q = `
		UPDATE chat.folders AS f
		SET position = o.ord, updated_at = NOW()
		FROM unnest($1::uuid[]) WITH ORDINALITY AS o(id, ord)
		WHERE f.id = o.id AND f.tenant_id = $2 AND f.user_id = $3
	`
	if _, err := r.db.Exec(ctx, q, folderIDs, tenantID, userID); err != nil {
		return fmt.Errorf("reorder folders: %w", err)
	}
	return nil
}
