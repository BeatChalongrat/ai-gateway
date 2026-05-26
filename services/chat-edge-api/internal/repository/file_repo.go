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

// Create inserts a new file row. Caller pre-populates filename,
// original_filename, content_type, file_size_bytes, storage_path.
// extraction_status defaults to "pending" via the DB.
func (r *FileRepo) Create(ctx context.Context, f *model.ConversationFile) (*model.ConversationFile, error) {
	const q = `
		INSERT INTO chat.conversation_files
			(conversation_id, tenant_id, filename, original_filename,
			 content_type, file_size_bytes, storage_path, extraction_status)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		RETURNING id, created_at, updated_at
	`
	if f.ExtractionStatus == "" {
		f.ExtractionStatus = "pending"
	}
	err := r.db.QueryRow(ctx, q,
		f.ConversationID, f.TenantID, f.Filename, f.OriginalFilename,
		f.ContentType, f.FileSizeBytes, f.StoragePath, f.ExtractionStatus,
	).Scan(&f.ID, &f.CreatedAt, &f.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("create file: %w", err)
	}
	return f, nil
}

// GetByID returns a single file row, tenant-scoped so an attacker
// can't read another tenant's upload metadata by guessing IDs.
func (r *FileRepo) GetByID(ctx context.Context, tenantID, fileID uuid.UUID) (*model.ConversationFile, error) {
	const q = `
		SELECT id, conversation_id, message_id, tenant_id, filename,
		       original_filename, content_type, file_size_bytes,
		       storage_path, extracted_text, extraction_status,
		       created_at, updated_at
		FROM chat.conversation_files
		WHERE id = $1 AND tenant_id = $2
	`
	f := &model.ConversationFile{}
	err := r.db.QueryRow(ctx, q, fileID, tenantID).Scan(
		&f.ID, &f.ConversationID, &f.MessageID, &f.TenantID, &f.Filename,
		&f.OriginalFilename, &f.ContentType, &f.FileSizeBytes,
		&f.StoragePath, &f.ExtractedText, &f.ExtractionStatus,
		&f.CreatedAt, &f.UpdatedAt,
	)
	if err != nil {
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
	const q = `
		SELECT id, conversation_id, message_id, tenant_id, filename,
		       original_filename, content_type, file_size_bytes,
		       storage_path, extracted_text, extraction_status,
		       created_at, updated_at
		FROM chat.conversation_files
		WHERE conversation_id = $1 AND tenant_id = $2
		ORDER BY created_at ASC
	`
	rows, err := r.db.Query(ctx, q, convID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list files: %w", err)
	}
	defer rows.Close()
	out := make([]model.ConversationFile, 0)
	for rows.Next() {
		var f model.ConversationFile
		if err := rows.Scan(
			&f.ID, &f.ConversationID, &f.MessageID, &f.TenantID, &f.Filename,
			&f.OriginalFilename, &f.ContentType, &f.FileSizeBytes,
			&f.StoragePath, &f.ExtractedText, &f.ExtractionStatus,
			&f.CreatedAt, &f.UpdatedAt,
		); err != nil {
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
	const q = `
		SELECT id, conversation_id, message_id, tenant_id, filename,
		       original_filename, content_type, file_size_bytes,
		       storage_path, extracted_text, extraction_status,
		       created_at, updated_at
		FROM chat.conversation_files
		WHERE message_id = $1 AND tenant_id = $2
		ORDER BY created_at ASC
	`
	rows, err := r.db.Query(ctx, q, messageID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list files by message: %w", err)
	}
	defer rows.Close()
	out := make([]model.ConversationFile, 0)
	for rows.Next() {
		var f model.ConversationFile
		if err := rows.Scan(
			&f.ID, &f.ConversationID, &f.MessageID, &f.TenantID, &f.Filename,
			&f.OriginalFilename, &f.ContentType, &f.FileSizeBytes,
			&f.StoragePath, &f.ExtractedText, &f.ExtractionStatus,
			&f.CreatedAt, &f.UpdatedAt,
		); err != nil {
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
	const q = `
		SELECT id, conversation_id, message_id, tenant_id, filename,
		       original_filename, content_type, file_size_bytes,
		       storage_path, extracted_text, extraction_status,
		       created_at, updated_at
		FROM chat.conversation_files
		WHERE message_id = ANY($1) AND tenant_id = $2
		ORDER BY created_at ASC
	`
	rows, err := r.db.Query(ctx, q, messageIDs, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list files by message ids: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var f model.ConversationFile
		if err := rows.Scan(
			&f.ID, &f.ConversationID, &f.MessageID, &f.TenantID, &f.Filename,
			&f.OriginalFilename, &f.ContentType, &f.FileSizeBytes,
			&f.StoragePath, &f.ExtractedText, &f.ExtractionStatus,
			&f.CreatedAt, &f.UpdatedAt,
		); err != nil {
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
