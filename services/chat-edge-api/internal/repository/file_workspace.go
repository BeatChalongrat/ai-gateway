package repository

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

var ErrDraftConflict = errors.New("draft changed on another client; recover your local draft before replacing it")

type FileDraft struct {
	SHA       string          `json:"sha256"`
	Revision  int64           `json:"revision"`
	Body      json.RawMessage `json:"body"`
	UpdatedAt time.Time       `json:"updated_at"`
}
type FileWork struct {
	ID           uuid.UUID       `json:"id"`
	FileID       uuid.UUID       `json:"file_id"`
	TenantID     uuid.UUID       `json:"-"`
	UserID       uuid.UUID       `json:"-"`
	Request      json.RawMessage `json:"-"`
	Status       string          `json:"status"`
	Stage        string          `json:"stage"`
	Checkpoint   json.RawMessage `json:"-"`
	Candidate    []byte          `json:"-"`
	Error        string          `json:"error,omitempty"`
	ResultFileID *uuid.UUID      `json:"result_file_id,omitempty"`
	CreatedAt    time.Time       `json:"created_at"`
	UpdatedAt    time.Time       `json:"updated_at"`
	ExpiresAt    time.Time       `json:"expires_at"`
}

const workColumns = `w.id,w.file_id,w.tenant_id,w.user_id,w.request,w.status,w.stage,w.checkpoint,w.candidate,w.error,w.result_file_id,w.created_at,w.updated_at,w.expires_at`

func scanWork(row pgx.Row) (*FileWork, error) {
	w := &FileWork{}
	err := row.Scan(&w.ID, &w.FileID, &w.TenantID, &w.UserID, &w.Request, &w.Status, &w.Stage, &w.Checkpoint, &w.Candidate, &w.Error, &w.ResultFileID, &w.CreatedAt, &w.UpdatedAt, &w.ExpiresAt)
	return w, err
}

func (r *FileRepo) GetDraft(ctx context.Context, tenant, user, file uuid.UUID) (*FileDraft, error) {
	d := &FileDraft{}
	err := r.db.QueryRow(ctx, `SELECT source_sha,revision,body,updated_at FROM chat.file_drafts WHERE file_id=$1 AND tenant_id=$2 AND user_id=$3`, file, tenant, user).Scan(&d.SHA, &d.Revision, &d.Body, &d.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return d, err
}
func (r *FileRepo) PutDraft(ctx context.Context, tenant, user, file uuid.UUID, d FileDraft) (*FileDraft, error) {
	var err error
	if d.Revision == 0 {
		err = r.db.QueryRow(ctx, `INSERT INTO chat.file_drafts(file_id,tenant_id,user_id,source_sha,body) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING RETURNING revision,updated_at`, file, tenant, user, d.SHA, d.Body).Scan(&d.Revision, &d.UpdatedAt)
	} else {
		err = r.db.QueryRow(ctx, `UPDATE chat.file_drafts SET source_sha=$4,body=$5,revision=revision+1,updated_at=now() WHERE file_id=$1 AND tenant_id=$2 AND user_id=$3 AND revision=$6 RETURNING revision,updated_at`, file, tenant, user, d.SHA, d.Body, d.Revision).Scan(&d.Revision, &d.UpdatedAt)
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrDraftConflict
	}
	return &d, err
}
func (r *FileRepo) DeleteDraft(ctx context.Context, tenant, user, file uuid.UUID, revision int64) error {
	tag, err := r.db.Exec(ctx, `DELETE FROM chat.file_drafts WHERE file_id=$1 AND tenant_id=$2 AND user_id=$3 AND revision=$4`, file, tenant, user, revision)
	if err == nil && tag.RowsAffected() == 0 {
		if d, e := r.GetDraft(ctx, tenant, user, file); e != nil {
			return e
		} else if d != nil {
			return ErrDraftConflict
		}
	}
	return err
}
func (r *FileRepo) CreateWork(ctx context.Context, tenant, user, file uuid.UUID, request json.RawMessage) (*FileWork, error) {
	// Expired candidates are never promoted; reclaim their bounded DB payloads.
	_, err := r.db.Exec(ctx, `DELETE FROM chat.file_work WHERE tenant_id=$1 AND user_id=$2 AND expires_at<now()`, tenant, user)
	if err != nil {
		return nil, err
	}
	return scanWork(r.db.QueryRow(ctx, `INSERT INTO chat.file_work AS w(file_id,tenant_id,user_id,request) VALUES($1,$2,$3,$4) RETURNING `+workColumns, file, tenant, user, request))
}
func (r *FileRepo) GetWork(ctx context.Context, tenant, user, id uuid.UUID) (*FileWork, error) {
	return scanWork(r.db.QueryRow(ctx, `SELECT `+workColumns+` FROM chat.file_work w JOIN chat.conversation_files f ON f.id=w.file_id JOIN chat.conversations c ON c.id=f.conversation_id WHERE w.id=$1 AND w.tenant_id=$2 AND w.user_id=$3 AND f.tenant_id=$2 AND f.user_id=$3 AND NOT c.is_deleted AND w.expires_at>now()`, id, tenant, user))
}
func (r *FileRepo) LatestWork(ctx context.Context, tenant, user, file uuid.UUID) (*FileWork, error) {
	w, err := scanWork(r.db.QueryRow(ctx, `SELECT `+workColumns+` FROM chat.file_work w WHERE w.file_id=$1 AND w.tenant_id=$2 AND w.user_id=$3 AND w.expires_at>now() ORDER BY created_at DESC LIMIT 1`, file, tenant, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return w, err
}
func (r *FileRepo) SaveWork(ctx context.Context, w *FileWork, expected string) error {
	tag, err := r.db.Exec(ctx, `UPDATE chat.file_work SET status=$4,stage=$5,checkpoint=$6,candidate=$7,error=$8,result_file_id=$9,updated_at=now() WHERE id=$1 AND tenant_id=$2 AND user_id=$3 AND status=$10 AND expires_at>now()`, w.ID, w.TenantID, w.UserID, w.Status, w.Stage, w.Checkpoint, w.Candidate, w.Error, w.ResultFileID, expected)
	if err == nil && tag.RowsAffected() != 1 {
		return context.Canceled
	}
	return err
}
func (r *FileRepo) WorkTransition(ctx context.Context, tenant, user, id uuid.UUID, from, to string) error {
	tag, err := r.db.Exec(ctx, `UPDATE chat.file_work SET status=$5,updated_at=now(),error='' WHERE id=$1 AND tenant_id=$2 AND user_id=$3 AND status=$4 AND expires_at>now()`, id, tenant, user, from, to)
	if err == nil && tag.RowsAffected() != 1 {
		return ErrFileRevisionConflict
	}
	return err
}
func (r *FileRepo) FileLibrary(ctx context.Context, tenant, user uuid.UUID, query, kind string, offset int) ([]model.ConversationFile, error) {
	rows, err := r.db.Query(ctx, `SELECT `+fileColumns+` FROM chat.conversation_files WHERE tenant_id=$1 AND user_id=$2 AND conversation_id IN (SELECT id FROM chat.conversations WHERE NOT is_deleted AND tenant_id=$1 AND user_id=$2) AND ($3='' OR original_filename ILIKE '%'||$3||'%') AND ($4='' OR lower(original_filename) LIKE '%.'||$4) AND NOT EXISTS(SELECT 1 FROM chat.conversation_files child WHERE child.parent_file_id=chat.conversation_files.id) ORDER BY created_at DESC,id DESC LIMIT 41 OFFSET $5`, tenant, user, query, kind, offset)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []model.ConversationFile{}
	for rows.Next() {
		var f model.ConversationFile
		if err = scanFile(rows, &f); err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}
func (r *FileRepo) WorkRevision(ctx context.Context, w *FileWork) (*model.ConversationFile, error) {
	var f model.ConversationFile
	err := scanFile(r.db.QueryRow(ctx, `SELECT `+fileColumns+` FROM chat.conversation_files WHERE parent_file_id=$1 AND tenant_id=$2 AND user_id=$3 AND change_log LIKE $4 LIMIT 1`, w.FileID, w.TenantID, w.UserID, "%user_accept:"+w.ID.String()+"%"), &f)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return &f, err
}
