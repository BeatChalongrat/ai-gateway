// ImportService — desktop agent-conversation backup mirror (migration 026).
//
// The SRAN Desktop client runs agent-mode turns through the gateway and
// persists them ONLY in local SQLite; these entry points let it push a
// one-way, idempotent backup here without touching any generation path.
//
// Idempotency contract (proven in import_service_test.go): replaying the
// exact same request produces created=false / imported=0 and no duplicate
// rows — client_id is the natural key, enforced by the partial unique
// indexes from migration 026.
package service

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ErrImportInvalid marks a validation failure the handler maps to 422.
var ErrImportInvalid = errors.New("import: invalid input")

// ErrImportConversationGone marks an import against a conversation the user
// deleted (on any client). The handler maps it to 410 Gone so the desktop
// propagates the delete locally instead of resurrecting the transcript
// (FIX 9 Part C — a user-intended delete must stay deleted).
var ErrImportConversationGone = errors.New("import: conversation deleted")

const (
	// importMaxBatch caps one message-import request.
	importMaxBatch = 200
	// importMaxContentBytes caps a single message's content (128 KB).
	importMaxContentBytes = 128 * 1024
	// importMaxFilesPerMessage mirrors the send path's files-per-message
	// cap (FileService Knobs.MaxFilesPerMessage default).
	importMaxFilesPerMessage = 5
)

type ImportService struct {
	pool          *pgxpool.Pool
	conversations *repository.ConversationRepo
	messages      *repository.MessageRepo
	files         *repository.FileRepo
	ownership     *ConversationService
}

func NewImportService(
	pool *pgxpool.Pool,
	conversations *repository.ConversationRepo,
	messages *repository.MessageRepo,
	files *repository.FileRepo,
	ownership *ConversationService,
) *ImportService {
	return &ImportService{
		pool:          pool,
		conversations: conversations,
		messages:      messages,
		files:         files,
		ownership:     ownership,
	}
}

// ImportConversationInput is what the handler passes after parsing JSON.
type ImportConversationInput struct {
	TenantID  uuid.UUID
	UserID    uuid.UUID
	ClientID  string
	Title     string
	ModelID   string
	CreatedAt *time.Time
}

// ImportConversation creates-or-gets a conversation by the desktop's local
// id. Returns (conversation, created). A deleted mirror is NOT resurrected
// (FIX 9 Part C): the server transcript is authoritative and a delete on any
// client is intentional — the call fails with ErrImportConversationGone
// (410) and the desktop deletes its local copy in response.
func (s *ImportService) ImportConversation(
	ctx context.Context, in ImportConversationInput,
) (*model.Conversation, bool, error) {
	clientID := strings.TrimSpace(in.ClientID)
	if clientID == "" {
		return nil, false, fmt.Errorf("%w: client_id is required", ErrImportInvalid)
	}

	if existing, err := s.getExisting(ctx, in.TenantID, in.UserID, clientID); err != nil {
		return nil, false, err
	} else if existing != nil {
		return existing, false, nil
	}

	title := strings.TrimSpace(in.Title)
	if title == "" {
		title = "New conversation"
	}
	if n := len([]rune(title)); n > 200 {
		title = string([]rune(title)[:200])
	}
	// model_id is NOT hard-validated against the live catalog — an old local
	// conversation may reference a retired model; store as-is. The column is
	// NOT NULL, so an absent model falls back to the "auto" pseudo-model.
	modelID := strings.TrimSpace(in.ModelID)
	if modelID == "" {
		modelID = "auto"
	}

	c := &model.Conversation{
		TenantID: in.TenantID,
		UserID:   in.UserID,
		ModelID:  modelID,
		Title:    title,
	}
	created, err := s.conversations.CreateImported(ctx, c, clientID, in.CreatedAt)
	if err != nil {
		return nil, false, err
	}
	if created {
		return c, true, nil
	}
	// Lost an insert race — the winner's row exists now.
	existing, err := s.getExisting(ctx, in.TenantID, in.UserID, clientID)
	if err != nil {
		return nil, false, err
	}
	if existing == nil {
		return nil, false, fmt.Errorf("import conversation: conflict but no row for client_id")
	}
	return existing, false, nil
}

// getExisting resolves the mirror for a client id. Returns (nil, nil) when
// no row exists, and ErrImportConversationGone when the mirror was deleted —
// deletes are user-intended and must never be undone by a sync push
// (previously this resurrected the row, which let the desktop re-create a
// conversation the user had deleted on the web).
func (s *ImportService) getExisting(
	ctx context.Context, tenantID, userID uuid.UUID, clientID string,
) (*model.Conversation, error) {
	existing, err := s.conversations.GetByClientID(ctx, tenantID, userID, clientID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, nil
		}
		return nil, err
	}
	if existing.IsDeleted {
		return nil, ErrImportConversationGone
	}
	return existing, nil
}

// ImportMessageInput is one message from the import batch.
type ImportMessageInput struct {
	ClientID  string
	Role      string
	Content   string
	ModelID   string
	CreatedAt time.Time
	// FileIDs are SERVER file ids (previously uploaded to this
	// conversation) to link to this message (FIX 10) — the desktop's
	// equivalent of the send path's ``file_ids``, so a desktop
	// attachment shows under its message bubble on web. Idempotent:
	// already-linked files are left untouched.
	FileIDs []uuid.UUID
}

// ImportMessages appends historical messages to an owned conversation in
// ONE transaction, deduping by (conversation_id, client_id). Returns
// (imported, skipped) where skipped = conflict-deduped rows. Never
// generates anything; supplied timestamps are preserved.
func (s *ImportService) ImportMessages(
	ctx context.Context,
	tenantID, userID, convID uuid.UUID,
	items []ImportMessageInput,
) (imported, skipped int, err error) {
	// Ownership first — same non-leaking 404 semantics as every other route.
	if _, err := s.ownership.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return 0, 0, err
	}

	if len(items) == 0 {
		return 0, 0, fmt.Errorf("%w: messages must be a non-empty array", ErrImportInvalid)
	}
	if len(items) > importMaxBatch {
		return 0, 0, fmt.Errorf("%w: at most %d messages per request", ErrImportInvalid, importMaxBatch)
	}
	for i := range items {
		// Only user/assistant ever reach the DB — a "tool"/"system" role is
		// rejected here, well before the messages_masked role CHECK.
		if items[i].Role != "user" && items[i].Role != "assistant" {
			return 0, 0, fmt.Errorf("%w: message %d: role must be user or assistant", ErrImportInvalid, i)
		}
		if strings.TrimSpace(items[i].ClientID) == "" {
			return 0, 0, fmt.Errorf("%w: message %d: client_id is required", ErrImportInvalid, i)
		}
		if len(items[i].Content) > importMaxContentBytes {
			return 0, 0, fmt.Errorf("%w: message %d: content exceeds %d bytes", ErrImportInvalid, i, importMaxContentBytes)
		}
		if items[i].CreatedAt.IsZero() {
			return 0, 0, fmt.Errorf("%w: message %d: created_at is required", ErrImportInvalid, i)
		}
		if len(items[i].FileIDs) > importMaxFilesPerMessage {
			return 0, 0, fmt.Errorf("%w: message %d: at most %d file_ids per message",
				ErrImportInvalid, i, importMaxFilesPerMessage)
		}
	}

	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return 0, 0, fmt.Errorf("import messages: begin tx: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	msgs := s.messages.WithTx(tx)
	convs := s.conversations.WithTx(tx)
	filesTx := s.files.WithTx(tx)
	for i := range items {
		var modelID *string
		if m := strings.TrimSpace(items[i].ModelID); m != "" {
			modelID = &m
		}
		msgID, inserted, err := msgs.CreateImported(ctx, &model.Message{
			ConversationID: convID,
			TenantID:       tenantID,
			UserID:         userID,
			Role:           items[i].Role,
			ContentMasked:  items[i].Content,
			ModelID:        modelID,
		}, strings.TrimSpace(items[i].ClientID), items[i].CreatedAt)
		if err != nil {
			return 0, 0, err
		}
		if inserted {
			imported++
		} else {
			skipped++
		}
		// Link uploaded attachments to the message (FIX 10) — same repo
		// path the send handler uses. Tenant+conversation scoped, and it
		// only claims rows with message_id IS NULL, so ids the caller
		// doesn't own (or already-linked files on a replay) are no-ops.
		if len(items[i].FileIDs) > 0 {
			if _, err := filesTx.AttachToMessage(
				ctx, tenantID, convID, msgID, items[i].FileIDs); err != nil {
				return 0, 0, err
			}
		}
	}
	// message_count is app-maintained; bump by rows ACTUALLY inserted —
	// conflict-skipped replays must not inflate the count.
	if imported > 0 {
		if err := convs.IncrementMessageCount(ctx, convID, imported); err != nil {
			return 0, 0, err
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return 0, 0, fmt.Errorf("import messages: commit: %w", err)
	}
	return imported, skipped, nil
}
