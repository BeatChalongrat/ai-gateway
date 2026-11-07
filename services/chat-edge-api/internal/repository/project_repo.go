package repository

// ProjectRepo — chat.projects (migration 029). Same ownership model as
// FolderRepo: every query filters (tenant_id, user_id) so a leaked id
// from another user/tenant can never read or mutate a row.

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type ProjectRepo struct {
	db DB
}

func NewProjectRepo(db *pgxpool.Pool) *ProjectRepo {
	return &ProjectRepo{db: db}
}

const projectColumns = `id, tenant_id, user_id, name, instructions, knowledge_base_id, created_at, updated_at`

func scanProject(row pgx.Row, p *model.Project) error {
	return row.Scan(
		&p.ID, &p.TenantID, &p.UserID, &p.Name, &p.Instructions,
		&p.KnowledgeBaseID, &p.CreatedAt, &p.UpdatedAt,
	)
}

// Create inserts a project owned by (tenantID, userID).
func (r *ProjectRepo) Create(
	ctx context.Context, tenantID, userID uuid.UUID, name, instructions string,
) (*model.Project, error) {
	q := `
		INSERT INTO chat.projects (tenant_id, user_id, name, instructions)
		VALUES ($1, $2, $3, $4)
		RETURNING ` + projectColumns
	p := &model.Project{}
	if err := scanProject(r.db.QueryRow(ctx, q, tenantID, userID, name, instructions), p); err != nil {
		return nil, fmt.Errorf("create project: %w", err)
	}
	return p, nil
}

// ListByUser returns the owner's projects with live conversation counts,
// newest first.
func (r *ProjectRepo) ListByUser(
	ctx context.Context, tenantID, userID uuid.UUID,
) ([]model.Project, error) {
	const q = `
		SELECT p.id, p.tenant_id, p.user_id, p.name, p.instructions,
		       p.knowledge_base_id, p.created_at, p.updated_at,
		       COUNT(c.id) FILTER (WHERE c.is_deleted = false) AS conversation_count
		FROM chat.projects p
		LEFT JOIN chat.conversations c ON c.project_id = p.id
		WHERE p.tenant_id = $1 AND p.user_id = $2
		GROUP BY p.id
		ORDER BY p.created_at DESC
	`
	rows, err := r.db.Query(ctx, q, tenantID, userID)
	if err != nil {
		return nil, fmt.Errorf("list projects: %w", err)
	}
	defer rows.Close()
	var out []model.Project
	for rows.Next() {
		var p model.Project
		if err := rows.Scan(
			&p.ID, &p.TenantID, &p.UserID, &p.Name, &p.Instructions,
			&p.KnowledgeBaseID, &p.CreatedAt, &p.UpdatedAt, &p.ConversationCount,
		); err != nil {
			return nil, fmt.Errorf("scan project: %w", err)
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// GetByID returns the project only when the (tenant, user) scope matches.
func (r *ProjectRepo) GetByID(
	ctx context.Context, tenantID, userID, projectID uuid.UUID,
) (*model.Project, error) {
	q := `SELECT ` + projectColumns + `
		FROM chat.projects
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	p := &model.Project{}
	if err := scanProject(r.db.QueryRow(ctx, q, projectID, tenantID, userID), p); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get project: %w", err)
	}
	return p, nil
}

// Update patches name and/or instructions (nil = keep current value).
func (r *ProjectRepo) Update(
	ctx context.Context, tenantID, userID, projectID uuid.UUID,
	name, instructions *string,
) (*model.Project, error) {
	q := `
		UPDATE chat.projects
		SET name         = COALESCE($4, name),
		    instructions = COALESCE($5, instructions),
		    updated_at   = NOW()
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3
		RETURNING ` + projectColumns
	p := &model.Project{}
	if err := scanProject(r.db.QueryRow(ctx, q, projectID, tenantID, userID, name, instructions), p); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("update project: %w", err)
	}
	return p, nil
}

// SetKnowledgeBase attaches (or detaches, kbID = nil) the project's KB.
// The EXISTS guard enforces the KB's scope: same tenant AND owned by the
// project's user (FIX132 B — a knowledge base is private to its creator), so
// neither another tenant's nor a colleague's KB id can be attached.
func (r *ProjectRepo) SetKnowledgeBase(
	ctx context.Context, tenantID, userID, projectID uuid.UUID, kbID *uuid.UUID,
) (*model.Project, error) {
	q := `
		UPDATE chat.projects
		SET knowledge_base_id = $4, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3
		  AND ($4::uuid IS NULL OR EXISTS (
		        SELECT 1 FROM chat.knowledge_bases kb
		        WHERE kb.id = $4 AND kb.tenant_id = $2 AND kb.user_id = $3))
		RETURNING ` + projectColumns
	var arg any
	if kbID != nil {
		arg = *kbID
	}
	p := &model.Project{}
	if err := scanProject(r.db.QueryRow(ctx, q, projectID, tenantID, userID, arg), p); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("set project knowledge base: %w", err)
	}
	return p, nil
}

// Delete removes the project. conversations.project_id has ON DELETE SET
// NULL, so member chats are un-assigned, never lost.
func (r *ProjectRepo) Delete(
	ctx context.Context, tenantID, userID, projectID uuid.UUID,
) error {
	const q = `DELETE FROM chat.projects WHERE id = $1 AND tenant_id = $2 AND user_id = $3`
	tag, err := r.db.Exec(ctx, q, projectID, tenantID, userID)
	if err != nil {
		return fmt.Errorf("delete project: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// GetForConversation resolves the project context a conversation inherits
// (instructions + KB), joined through conversations.project_id in a single
// tenant-scoped round-trip. Returns ErrNotFound when the conversation has
// no project. Used by the prompt-compile path — user scope is enforced
// upstream by the conversation ownership check in prepareSend.
func (r *ProjectRepo) GetForConversation(
	ctx context.Context, tenantID, convID uuid.UUID,
) (*model.Project, error) {
	const q = `
		SELECT p.id, p.tenant_id, p.user_id, p.name, p.instructions,
		       p.knowledge_base_id, p.created_at, p.updated_at
		FROM chat.projects p
		JOIN chat.conversations c ON c.project_id = p.id
		WHERE c.id = $1 AND c.tenant_id = $2 AND p.tenant_id = $2
	`
	p := &model.Project{}
	if err := scanProject(r.db.QueryRow(ctx, q, convID, tenantID), p); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get project for conversation: %w", err)
	}
	return p, nil
}

// MoveConversationToProject assigns (or clears, projectID = nil) a
// conversation's project membership. Mirrors MoveToFolder exactly.
func (r *ProjectRepo) MoveConversationToProject(
	ctx context.Context, tenantID, convID uuid.UUID, projectID *uuid.UUID,
) error {
	const q = `
		UPDATE chat.conversations
		SET project_id = $3, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	var arg any
	if projectID != nil {
		arg = *projectID
	}
	tag, err := r.db.Exec(ctx, q, convID, tenantID, arg)
	if err != nil {
		return fmt.Errorf("move conversation to project: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}
