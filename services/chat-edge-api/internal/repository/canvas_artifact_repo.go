package repository

// CanvasArtifactRepo — chat.canvas_artifacts (migration 030). Every row
// is an immutable version; rows sharing (conversation_id, artifact_key)
// form one artifact's lineage. Every query filters tenant_id AND
// conversation_id so a leaked artifact key or id from another tenant /
// conversation can never read or write a row. User scope is enforced one
// layer up (CanvasArtifactService asserts conversation ownership before
// every call), same split as WorkspaceService over the file repo.

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type CanvasArtifactRepo struct {
	db DB
}

func NewCanvasArtifactRepo(db *pgxpool.Pool) *CanvasArtifactRepo {
	return &CanvasArtifactRepo{db: db}
}

const canvasArtifactColumns = `id, tenant_id, user_id, conversation_id, message_id, artifact_key, type, title, content, version, created_at`

func scanCanvasArtifact(row pgx.Row, a *model.CanvasArtifact) error {
	return row.Scan(
		&a.ID, &a.TenantID, &a.UserID, &a.ConversationID, &a.MessageID,
		&a.ArtifactKey, &a.Type, &a.Title, &a.Content, &a.Version, &a.CreatedAt,
	)
}

// CreateVersion appends the next version for (convID, key). The version
// number is computed as COALESCE(MAX(version),0)+1 INSIDE the INSERT
// statement (single-statement read-and-write) so two concurrent saves
// race on the UNIQUE (conversation_id, artifact_key, version) constraint
// instead of silently sharing a number; on a unique violation we retry
// once with a freshly computed MAX. An empty title is stored as NULL.
func (r *CanvasArtifactRepo) CreateVersion(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
	messageID *uuid.UUID, key, typ, title, content string,
) (*model.CanvasArtifact, error) {
	q := `
		INSERT INTO chat.canvas_artifacts
			(tenant_id, user_id, conversation_id, message_id, artifact_key, type, title, content, version)
		SELECT $1, $2, $3, $4, $5, $6, NULLIF($7, ''), $8,
		       COALESCE(MAX(version), 0) + 1
		FROM chat.canvas_artifacts
		WHERE tenant_id = $1 AND conversation_id = $3 AND artifact_key = $5
		RETURNING ` + canvasArtifactColumns
	var arg any
	if messageID != nil {
		arg = *messageID
	}
	for attempt := 0; ; attempt++ {
		a := &model.CanvasArtifact{}
		err := scanCanvasArtifact(
			r.db.QueryRow(ctx, q, tenantID, userID, convID, arg, key, typ, title, content), a,
		)
		if err == nil {
			return a, nil
		}
		var pgErr *pgconn.PgError
		if attempt == 0 && errors.As(err, &pgErr) && pgErr.Code == "23505" {
			continue // lost the race — recompute MAX once and re-insert
		}
		return nil, fmt.Errorf("create canvas artifact version: %w", err)
	}
}

// ListLatest returns the newest version of every artifact in the
// conversation (one row per artifact_key), newest first.
func (r *CanvasArtifactRepo) ListLatest(
	ctx context.Context, tenantID, convID uuid.UUID,
) ([]model.CanvasArtifact, error) {
	q := `
		SELECT ` + canvasArtifactColumns + ` FROM (
			SELECT DISTINCT ON (artifact_key) ` + canvasArtifactColumns + `
			FROM chat.canvas_artifacts
			WHERE tenant_id = $1 AND conversation_id = $2
			ORDER BY artifact_key, version DESC
		) latest
		ORDER BY created_at DESC
	`
	rows, err := r.db.Query(ctx, q, tenantID, convID)
	if err != nil {
		return nil, fmt.Errorf("list latest canvas artifacts: %w", err)
	}
	defer rows.Close()
	return collectCanvasArtifacts(rows)
}

// ListVersions returns every version for one artifact_key, version DESC.
// Returns ErrNotFound when the key has no rows in this (tenant,
// conversation) scope — a key only exists through its versions.
func (r *CanvasArtifactRepo) ListVersions(
	ctx context.Context, tenantID, convID uuid.UUID, key string,
) ([]model.CanvasArtifact, error) {
	q := `
		SELECT ` + canvasArtifactColumns + `
		FROM chat.canvas_artifacts
		WHERE tenant_id = $1 AND conversation_id = $2 AND artifact_key = $3
		ORDER BY version DESC
	`
	rows, err := r.db.Query(ctx, q, tenantID, convID, key)
	if err != nil {
		return nil, fmt.Errorf("list canvas artifact versions: %w", err)
	}
	defer rows.Close()
	out, err := collectCanvasArtifacts(rows)
	if err != nil {
		return nil, err
	}
	if len(out) == 0 {
		return nil, ErrNotFound
	}
	return out, nil
}

// GetVersion returns one specific version of one artifact.
func (r *CanvasArtifactRepo) GetVersion(
	ctx context.Context, tenantID, convID uuid.UUID, key string, version int,
) (*model.CanvasArtifact, error) {
	q := `
		SELECT ` + canvasArtifactColumns + `
		FROM chat.canvas_artifacts
		WHERE tenant_id = $1 AND conversation_id = $2 AND artifact_key = $3 AND version = $4
	`
	a := &model.CanvasArtifact{}
	if err := scanCanvasArtifact(r.db.QueryRow(ctx, q, tenantID, convID, key, version), a); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get canvas artifact version: %w", err)
	}
	return a, nil
}

// GetLatest returns the newest version of one artifact.
func (r *CanvasArtifactRepo) GetLatest(
	ctx context.Context, tenantID, convID uuid.UUID, key string,
) (*model.CanvasArtifact, error) {
	q := `
		SELECT ` + canvasArtifactColumns + `
		FROM chat.canvas_artifacts
		WHERE tenant_id = $1 AND conversation_id = $2 AND artifact_key = $3
		ORDER BY version DESC
		LIMIT 1
	`
	a := &model.CanvasArtifact{}
	if err := scanCanvasArtifact(r.db.QueryRow(ctx, q, tenantID, convID, key), a); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get latest canvas artifact: %w", err)
	}
	return a, nil
}

func collectCanvasArtifacts(rows pgx.Rows) ([]model.CanvasArtifact, error) {
	var out []model.CanvasArtifact
	for rows.Next() {
		var a model.CanvasArtifact
		if err := rows.Scan(
			&a.ID, &a.TenantID, &a.UserID, &a.ConversationID, &a.MessageID,
			&a.ArtifactKey, &a.Type, &a.Title, &a.Content, &a.Version, &a.CreatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan canvas artifact: %w", err)
		}
		out = append(out, a)
	}
	return out, rows.Err()
}
