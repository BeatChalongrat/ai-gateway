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

// FileRepo persists conversation file attachments. The blob itself
// lives on disk; this repo only owns the metadata row.
type FileRepo struct {
	db DB
}

func NewFileRepo(db *pgxpool.Pool) *FileRepo {
	return &FileRepo{db: db}
}

func (r *FileRepo) WithTx(tx pgx.Tx) *FileRepo {
	return &FileRepo{db: tx}
}

// fileColumns is the single source of truth for SELECT projections.
// Every read path uses it so the Excel-workspace fields added in
// migration 012 are always populated and we don't have stale-row
// pitfalls where the model has fields the SQL never filled in.
const fileColumns = `
	id, conversation_id, message_id, tenant_id, user_id, filename,
	original_filename, content_type, file_size_bytes,
	storage_path, extracted_text, extraction_status,
	created_at, updated_at,
	inspection_json, is_generated, parent_file_id, version, change_log
`

func scanFile(row pgx.Row, f *model.ConversationFile) error {
	return row.Scan(
		&f.ID, &f.ConversationID, &f.MessageID, &f.TenantID, &f.UserID, &f.Filename,
		&f.OriginalFilename, &f.ContentType, &f.FileSizeBytes,
		&f.StoragePath, &f.ExtractedText, &f.ExtractionStatus,
		&f.CreatedAt, &f.UpdatedAt,
		&f.InspectionJSON, &f.IsGenerated, &f.ParentFileID, &f.Version, &f.ChangeLog,
	)
}

// Create inserts a new file row. Caller pre-populates filename,
// original_filename, content_type, file_size_bytes, storage_path.
// extraction_status defaults to "pending" via the DB.
//
// Phase-4 fields (is_generated, parent_file_id, version, change_log)
// are honoured when set so the executor result path can write a
// "generated" row in a single insert; legacy direct-upload callers
// can leave them zero-valued and the DB defaults pick up
// (is_generated=false, version=1, parent_file_id NULL).
func (r *FileRepo) Create(ctx context.Context, f *model.ConversationFile) (*model.ConversationFile, error) {
	const q = `
		INSERT INTO chat.conversation_files
			(conversation_id, tenant_id, user_id, filename, original_filename,
			 content_type, file_size_bytes, storage_path, extraction_status,
			 is_generated, parent_file_id, version, change_log, inspection_json)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
		RETURNING id, created_at, updated_at
	`
	if f.ExtractionStatus == "" {
		f.ExtractionStatus = "pending"
	}
	if f.Version == 0 {
		f.Version = 1
	}
	err := r.db.QueryRow(ctx, q,
		f.ConversationID, f.TenantID, f.UserID, f.Filename, f.OriginalFilename,
		f.ContentType, f.FileSizeBytes, f.StoragePath, f.ExtractionStatus,
		f.IsGenerated, f.ParentFileID, f.Version, f.ChangeLog, f.InspectionJSON,
	).Scan(&f.ID, &f.CreatedAt, &f.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("create file: %w", err)
	}
	return f, nil
}

// GetByID returns a single file row, tenant-scoped so an attacker
// can't read another tenant's upload metadata by guessing IDs.
func (r *FileRepo) GetByID(ctx context.Context, tenantID, fileID uuid.UUID) (*model.ConversationFile, error) {
	q := `SELECT ` + fileColumns + ` FROM chat.conversation_files WHERE id = $1 AND tenant_id = $2`
	f := &model.ConversationFile{}
	if err := scanFile(r.db.QueryRow(ctx, q, fileID, tenantID), f); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get file: %w", err)
	}
	return f, nil
}

// ListByConversation returns every non-orphaned file row for a
// conversation in upload order. Used by the file-list endpoint and
// by the message handler when assembling AI context.
func (r *FileRepo) ListByConversation(
	ctx context.Context, tenantID, convID uuid.UUID,
) ([]model.ConversationFile, error) {
	q := `SELECT ` + fileColumns + ` FROM chat.conversation_files
	      WHERE conversation_id = $1 AND tenant_id = $2
	      ORDER BY created_at ASC`
	rows, err := r.db.Query(ctx, q, convID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list files: %w", err)
	}
	defer rows.Close()
	out := make([]model.ConversationFile, 0)
	for rows.Next() {
		var f model.ConversationFile
		if err := scanFile(rows, &f); err != nil {
			return nil, fmt.Errorf("list files scan: %w", err)
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// ListByMessage returns the files attached to one message. Used by
// the message-list endpoint when shaping the wire response.
func (r *FileRepo) ListByMessage(
	ctx context.Context, tenantID, messageID uuid.UUID,
) ([]model.ConversationFile, error) {
	q := `SELECT ` + fileColumns + ` FROM chat.conversation_files
	      WHERE message_id = $1 AND tenant_id = $2
	      ORDER BY created_at ASC`
	rows, err := r.db.Query(ctx, q, messageID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list files by message: %w", err)
	}
	defer rows.Close()
	out := make([]model.ConversationFile, 0)
	for rows.Next() {
		var f model.ConversationFile
		if err := scanFile(rows, &f); err != nil {
			return nil, fmt.Errorf("list files by msg scan: %w", err)
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// ListByMessageIDs is a batched variant: one query for many message
// ids. Used by the message-list handler so we don't run N round-
// trips when shaping the response.
func (r *FileRepo) ListByMessageIDs(
	ctx context.Context, tenantID uuid.UUID, messageIDs []uuid.UUID,
) (map[uuid.UUID][]model.ConversationFile, error) {
	out := make(map[uuid.UUID][]model.ConversationFile)
	if len(messageIDs) == 0 {
		return out, nil
	}
	q := `SELECT ` + fileColumns + ` FROM chat.conversation_files
	      WHERE message_id = ANY($1) AND tenant_id = $2
	      ORDER BY created_at ASC`
	rows, err := r.db.Query(ctx, q, messageIDs, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list files by message ids: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var f model.ConversationFile
		if err := scanFile(rows, &f); err != nil {
			return nil, fmt.Errorf("list files by msg ids scan: %w", err)
		}
		if f.MessageID != nil {
			out[*f.MessageID] = append(out[*f.MessageID], f)
		}
	}
	return out, rows.Err()
}

// UpdateExtraction writes the post-extraction status + content.
// extracted_text may be nil (failed / unsupported cases).
func (r *FileRepo) UpdateExtraction(
	ctx context.Context, fileID uuid.UUID, status string, text *string,
) error {
	const q = `
		UPDATE chat.conversation_files
		SET extracted_text = $2, extraction_status = $3, updated_at = NOW()
		WHERE id = $1
	`
	if _, err := r.db.Exec(ctx, q, fileID, text, status); err != nil {
		return fmt.Errorf("update extraction: %w", err)
	}
	return nil
}

// UpdateInspectionJSON caches the inspection_json payload produced
// by excel_inspector.py. Idempotent: re-running the inspector on
// the same file overwrites the previous JSON.
func (r *FileRepo) UpdateInspectionJSON(
	ctx context.Context, fileID uuid.UUID, inspectionJSON string,
) error {
	const q = `
		UPDATE chat.conversation_files
		SET inspection_json = $2, updated_at = NOW()
		WHERE id = $1
	`
	if _, err := r.db.Exec(ctx, q, fileID, inspectionJSON); err != nil {
		return fmt.Errorf("update inspection json: %w", err)
	}
	return nil
}

// ListVersions walks the parent_file_id chain rooted at fileID and
// returns every version (the root + all descendants) in version
// order. Used by the version-history endpoint so the user can see
// every iteration the AI has produced.
func (r *FileRepo) ListVersions(
	ctx context.Context, tenantID, fileID uuid.UUID,
) ([]model.ConversationFile, error) {
	// Recursive CTE: start with the requested id, walk up to the
	// root, then walk back down through every child so we surface
	// every sibling version regardless of which one the caller
	// passed in.
	q := `
		WITH RECURSIVE root AS (
			SELECT id, parent_file_id
			FROM chat.conversation_files
			WHERE id = $1 AND tenant_id = $2
		),
		chain_up AS (
			SELECT cf.id, cf.parent_file_id
			FROM chat.conversation_files cf
			JOIN root r ON r.id = cf.id
			UNION ALL
			SELECT cf.id, cf.parent_file_id
			FROM chat.conversation_files cf
			JOIN chain_up c ON cf.id = c.parent_file_id
		),
		ancestor_root AS (
			SELECT id FROM chain_up WHERE parent_file_id IS NULL LIMIT 1
		),
		descendants AS (
			SELECT cf.*
			FROM chat.conversation_files cf
			JOIN ancestor_root ar ON cf.id = ar.id
			UNION ALL
			SELECT cf.*
			FROM chat.conversation_files cf
			JOIN descendants d ON cf.parent_file_id = d.id
		)
		SELECT ` + fileColumns + `
		FROM descendants
		WHERE tenant_id = $2
		ORDER BY version ASC, created_at ASC
	`
	rows, err := r.db.Query(ctx, q, fileID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list versions: %w", err)
	}
	defer rows.Close()
	out := make([]model.ConversationFile, 0)
	for rows.Next() {
		var f model.ConversationFile
		if err := scanFile(rows, &f); err != nil {
			return nil, fmt.Errorf("list versions scan: %w", err)
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// GeneratedLeaf is one row from ListGeneratedLeaves: the latest
// version of a generated-file chain plus a denormalized count
// of versions in that chain and the immediate parent's display
// name. Used to populate the workspace panel's file list.
type GeneratedLeaf struct {
	File           model.ConversationFile
	VersionCount   int
	ParentFilename string
}

// ListGeneratedLeaves returns the latest generated file from
// every lineage in the conversation. "Latest" = a generated file
// that has no generated child pointing at it (no descendant
// version exists). VersionCount is sized by walking
// parent_file_id back up while the rows stay is_generated=true.
// Used by GET /conversations/{convID}/generated-files.
func (r *FileRepo) ListGeneratedLeaves(
	ctx context.Context, tenantID, convID uuid.UUID,
) ([]GeneratedLeaf, error) {
	// Recursive CTE per leaf: walk parent_file_id while the
	// ancestor is also is_generated=true. The chain count is
	// the size of that walk; the user-uploaded source (if any)
	// is the first ancestor where is_generated=false.
	q := `
		WITH RECURSIVE leaves AS (
			SELECT cf.*
			FROM chat.conversation_files cf
			WHERE cf.conversation_id = $1
			  AND cf.tenant_id = $2
			  AND cf.is_generated = true
			  AND NOT EXISTS (
			    SELECT 1 FROM chat.conversation_files c2
			    WHERE c2.parent_file_id = cf.id
			      AND c2.tenant_id = $2
			  )
		),
		chain AS (
			SELECT l.id AS leaf_id, l.id, l.parent_file_id, l.is_generated, 1 AS depth
			FROM leaves l
			UNION ALL
			SELECT c.leaf_id, p.id, p.parent_file_id, p.is_generated, c.depth + 1
			FROM chain c
			JOIN chat.conversation_files p
			  ON p.id = c.parent_file_id
			 AND p.tenant_id = $2
			WHERE c.is_generated = true
		),
		counts AS (
			-- Cast to int: COUNT() returns bigint which pgx won't
			-- auto-coerce into Go's int. ::int is safe — chain
			-- depth is bounded by realistic version counts.
			-- Counts ALL rows in the chain (including the
			-- non-generated source upload) so the workspace's
			-- "N versions" badge matches the version timeline's
			-- row count exactly. ListVersions returns the same
			-- full chain.
			SELECT leaf_id, COUNT(*)::int AS version_count
			FROM chain
			GROUP BY leaf_id
		)
		SELECT
		  cf.id, cf.conversation_id, cf.message_id, cf.tenant_id, cf.user_id, cf.filename,
		  cf.original_filename, cf.content_type, cf.file_size_bytes,
		  cf.storage_path, cf.extracted_text, cf.extraction_status,
		  cf.created_at, cf.updated_at,
		  cf.inspection_json, cf.is_generated, cf.parent_file_id, cf.version, cf.change_log,
		  counts.version_count,
		  parent.original_filename AS parent_filename
		FROM leaves cf
		JOIN counts ON counts.leaf_id = cf.id
		LEFT JOIN chat.conversation_files parent
		  ON parent.id = cf.parent_file_id
		 AND parent.tenant_id = $2
		ORDER BY cf.created_at DESC
	`
	rows, err := r.db.Query(ctx, q, convID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list generated leaves: %w", err)
	}
	defer rows.Close()
	out := make([]GeneratedLeaf, 0)
	for rows.Next() {
		var leaf GeneratedLeaf
		var parentName *string
		if err := rows.Scan(
			&leaf.File.ID, &leaf.File.ConversationID, &leaf.File.MessageID,
			&leaf.File.TenantID, &leaf.File.UserID, &leaf.File.Filename, &leaf.File.OriginalFilename,
			&leaf.File.ContentType, &leaf.File.FileSizeBytes, &leaf.File.StoragePath,
			&leaf.File.ExtractedText, &leaf.File.ExtractionStatus,
			&leaf.File.CreatedAt, &leaf.File.UpdatedAt,
			&leaf.File.InspectionJSON, &leaf.File.IsGenerated, &leaf.File.ParentFileID,
			&leaf.File.Version, &leaf.File.ChangeLog,
			&leaf.VersionCount, &parentName,
		); err != nil {
			return nil, fmt.Errorf("list generated leaves scan: %w", err)
		}
		if parentName != nil {
			leaf.ParentFilename = *parentName
		}
		out = append(out, leaf)
	}
	return out, rows.Err()
}

// AttachToMessage links a batch of files to the newly-created
// message row. Tenant + conversation scoped so a caller can't
// re-attach another conversation's files. Returns the number of
// rows actually linked so the handler can detect missing ids.
func (r *FileRepo) AttachToMessage(
	ctx context.Context,
	tenantID, convID, messageID uuid.UUID,
	fileIDs []uuid.UUID,
) (int64, error) {
	if len(fileIDs) == 0 {
		return 0, nil
	}
	const q = `
		UPDATE chat.conversation_files
		SET message_id = $1, updated_at = NOW()
		WHERE id = ANY($2)
		  AND tenant_id = $3
		  AND conversation_id = $4
		  AND message_id IS NULL
	`
	tag, err := r.db.Exec(ctx, q, messageID, fileIDs, tenantID, convID)
	if err != nil {
		return 0, fmt.Errorf("attach files: %w", err)
	}
	return tag.RowsAffected(), nil
}

// Delete removes the row. The handler is responsible for unlinking
// the on-disk blob — the repo can't safely touch the filesystem
// from a transaction. Returns ErrNotFound if no row matched (so
// re-deletes don't silently succeed).
func (r *FileRepo) Delete(ctx context.Context, tenantID, fileID uuid.UUID) error {
	const q = `
		DELETE FROM chat.conversation_files
		WHERE id = $1 AND tenant_id = $2
	`
	tag, err := r.db.Exec(ctx, q, fileID, tenantID)
	if err != nil {
		return fmt.Errorf("delete file: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// SumSizeForConversation returns the total bytes used by all files
// in one conversation. Used by the upload handler to enforce the
// per-conversation cap (100MB by default).
func (r *FileRepo) SumSizeForConversation(
	ctx context.Context, tenantID, convID uuid.UUID,
) (int64, error) {
	const q = `
		SELECT COALESCE(SUM(file_size_bytes), 0)
		FROM chat.conversation_files
		WHERE conversation_id = $1 AND tenant_id = $2
	`
	var total int64
	if err := r.db.QueryRow(ctx, q, convID, tenantID).Scan(&total); err != nil {
		return 0, fmt.Errorf("sum file size: %w", err)
	}
	return total, nil
}
