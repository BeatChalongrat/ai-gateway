package repository

import (
	"context"
	"errors"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

var ErrFileRevisionConflict = errors.New("file has a newer revision; reload before saving")

// Lock the expected parent, then check its children on a fresh READ COMMITTED
// statement. Two editor saves cannot silently branch or overwrite one another.
func (r *FileRepo) CreateRevision(ctx context.Context, parent uuid.UUID, row *model.ConversationFile) (*model.ConversationFile, error) {
	beginner, ok := r.db.(interface {
		Begin(context.Context) (pgx.Tx, error)
	})
	if !ok {
		return nil, errors.New("file revision transaction unavailable")
	}
	tx, err := beginner.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	var version int
	err = tx.QueryRow(ctx, `SELECT version FROM chat.conversation_files WHERE id=$1 AND tenant_id=$2 AND user_id=$3 AND conversation_id=$4 FOR UPDATE`, parent, row.TenantID, row.UserID, row.ConversationID).Scan(&version)
	if err != nil {
		return nil, err
	}
	var exists bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM chat.conversation_files WHERE parent_file_id=$1)`, parent).Scan(&exists)
	if err != nil {
		return nil, err
	}
	if exists {
		return nil, ErrFileRevisionConflict
	}
	row.ParentFileID = &parent
	row.Version = version + 1
	result, err := r.WithTx(tx).Create(ctx, row)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return result, nil
}
