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
	"regexp"
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
	// ImportMaxBodyBytes caps one import REQUEST (32 MiB) — a transport
	// ceiling, enforced by the handler before the body is decoded.
	//
	// FIX121 — there is no ceiling on a message's content. Until now one
	// message above 128 KiB (about 43,000 Thai characters: three bytes each)
	// failed the whole batch with 422, so a long desktop turn — question AND
	// answer — never reached the server while the person saw a normal reply.
	// An import mirrors a turn that has already happened; refusing to keep
	// it protects nothing. The web send path never had a content ceiling
	// either, so the same message sent from the page was always kept.
	ImportMaxBodyBytes = 32 << 20
	// importMaxFilesPerMessage is the send path's files-per-message cap
	// (FIX126 F1 — one named constant, DefaultMaxFilesPerMessage).
	importMaxFilesPerMessage = DefaultMaxFilesPerMessage
)

// importErrorCodeRe bounds an imported error_code to the shape every
// catalogue code has (letters, digits, underscore; short).
var importErrorCodeRe = regexp.MustCompile(`^[A-Za-z0-9_]{1,64}$`)

// importRunIDRe bounds an imported run id to the same shape the request
// id middleware accepts (middleware.SanitizeRequestID).
var importRunIDRe = regexp.MustCompile(`^[A-Za-z0-9._-]{8,64}$`)

type ImportService struct {
	pool          *pgxpool.Pool
	conversations *repository.ConversationRepo
	messages      *repository.MessageRepo
	files         *repository.FileRepo
	ownership     *ConversationService
	// afterImport (FIX130 A1) — called once an import added messages, so a
	// desktop chat feeds the memory extractor the way a web turn does
	// (MessageService.AfterImport). nil = nothing (tests, legacy wiring).
	afterImport func(ctx context.Context, tenantID, convID uuid.UUID)
}

// SetAfterImport wires the FIX130 post-import hook (main.go).
func (s *ImportService) SetAfterImport(fn func(ctx context.Context, tenantID, convID uuid.UUID)) {
	s.afterImport = fn
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
	// FIX118 — how the turn ended on the client that produced it. Status
	// is completed | error | cancelled ("" = completed, the pre-FIX118
	// wire). TerminalCause is one of the closed set (see
	// service.TerminalCauses) or ""; ErrorCode is the catalogue code of the
	// failure or "". Only assistant rows carry them; a user row's are
	// ignored. Without these a turn that failed on the desktop was
	// imported as a success.
	Status        string
	TerminalCause string
	ErrorCode     string
	// FIX119 — the desktop's run id for the turn (the same id its frames
	// carried and it sent the gateway as X-Request-Id) and why the model
	// stopped (closed set). Assistant rows only.
	RunID      string
	StopReason string
	// FIX120 — the requested model when a fallback model answered
	// (assistant rows only; stored only when it differs from ModelID).
	FallbackFrom string
	// FIX121 — the per-model attempt log of a failed desktop turn, as the
	// gateway's error frame carried it. Assistant rows that ended in error
	// only; stored as the bounded copy NormalizeAttempts makes.
	Attempts any
	// FIX124 — the client id of the assistant row this one replaces (a
	// desktop Regenerate / Retry). That row is retired as status=regenerated
	// in the same transaction, as the web's Regenerate does; without it the
	// old answer stayed an active second answer in every later context.
	// Assistant rows only; an id that names no row is ignored.
	ReplacesClientID string
	// Invalid is a reason the HANDLER already found this item unusable
	// (an unparsable file id); it is rejected like any other bad item.
	Invalid string
}

// ImportRejection names one item of a batch that was not stored, and why.
type ImportRejection struct {
	Index    int    `json:"index"`
	ClientID string `json:"client_id,omitempty"`
	Reason   string `json:"reason"`
}

// importItemProblem returns why an item cannot be stored, or "".
func importItemProblem(it *ImportMessageInput) string {
	if it.Invalid != "" {
		return it.Invalid
	}
	// Only user/assistant ever reach the DB — a "tool"/"system" role is
	// rejected here, well before the messages_masked role CHECK.
	if it.Role != "user" && it.Role != "assistant" {
		return "role must be user or assistant"
	}
	if strings.TrimSpace(it.ClientID) == "" {
		return "client_id is required"
	}
	if it.CreatedAt.IsZero() {
		return "created_at is required (RFC3339)"
	}
	if len(it.FileIDs) > importMaxFilesPerMessage {
		return fmt.Sprintf("at most %d file_ids per message", importMaxFilesPerMessage)
	}
	// FIX118 — the ending must be one the row can hold.
	switch it.Status {
	case "", "completed", "error", "cancelled":
	default:
		return "status must be completed, error or cancelled"
	}
	if c := it.TerminalCause; c != "" && !IsTerminalCause(c) {
		return "terminal_cause is not one of " + strings.Join(TerminalCauses, "|")
	}
	if e := it.ErrorCode; e != "" && !importErrorCodeRe.MatchString(e) {
		return "error_code must be a short identifier"
	}
	if sr := it.StopReason; sr != "" && !IsStopReason(sr) {
		return "stop_reason is not one of " + strings.Join(StopReasons, "|")
	}
	if rid := it.RunID; rid != "" && !importRunIDRe.MatchString(rid) {
		return "run_id must be a short identifier"
	}
	return ""
}

// ImportMessages appends historical messages to an owned conversation in
// ONE transaction, deduping by (conversation_id, client_id). Returns
// (imported, skipped, rejected) where skipped = conflict-deduped rows and
// rejected = items that could not be stored, each with its reason. Never
// generates anything; supplied timestamps are preserved.
//
// FIX121 — one bad item no longer fails its neighbours. The old pre-flight
// loop returned on the first problem, before the transaction opened, so a
// batch was all or nothing and the caller learned only "422". Now every
// storable item is stored and every other one is named in rejected; the
// request is refused as a whole (ErrImportInvalid) only when it is malformed
// as a whole: empty, over the batch size, or with no storable item at all.
func (s *ImportService) ImportMessages(
	ctx context.Context,
	tenantID, userID, convID uuid.UUID,
	items []ImportMessageInput,
) (imported, skipped int, rejected []ImportRejection, err error) {
	// Ownership first — same non-leaking 404 semantics as every other route.
	if _, err := s.ownership.AssertOwnership(ctx, tenantID, userID, convID); err != nil {
		return 0, 0, nil, err
	}

	if len(items) == 0 {
		return 0, 0, nil, fmt.Errorf("%w: messages must be a non-empty array", ErrImportInvalid)
	}
	if len(items) > importMaxBatch {
		return 0, 0, nil, fmt.Errorf("%w: at most %d messages per request", ErrImportInvalid, importMaxBatch)
	}
	storable := make([]bool, len(items))
	anyStorable := false
	for i := range items {
		if why := importItemProblem(&items[i]); why != "" {
			rejected = append(rejected, ImportRejection{Index: i, ClientID: strings.TrimSpace(items[i].ClientID), Reason: why})
			continue
		}
		storable[i] = true
		anyStorable = true
	}
	if !anyStorable {
		return 0, 0, nil, fmt.Errorf("%w: message %d: %s", ErrImportInvalid, rejected[0].Index, rejected[0].Reason)
	}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return 0, 0, nil, fmt.Errorf("import messages: begin tx: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	msgs := s.messages.WithTx(tx)
	convs := s.conversations.WithTx(tx)
	filesTx := s.files.WithTx(tx)
	userRowInserted := false
	for i := range items {
		if !storable[i] {
			continue
		}
		var modelID *string
		if m := strings.TrimSpace(items[i].ModelID); m != "" {
			modelID = &m
		}
		row := &model.Message{
			ConversationID: convID,
			TenantID:       tenantID,
			UserID:         userID,
			Role:           items[i].Role,
			ContentMasked:  items[i].Content,
			ModelID:        modelID,
			Status:         "completed",
		}
		if items[i].Role == "assistant" {
			// FIX118 — the client says how its turn ended; the row keeps
			// it. A user row is always completed and never carries a cause.
			cause := ""
			if items[i].Status != "" {
				row.Status = items[i].Status
				cause = items[i].TerminalCause
				if cause == "" {
					cause = CauseForStatus(row.Status)
				}
				switch row.Status {
				case "completed":
					cause = TerminalCauseCompleted
				case "cancelled":
					cause = TerminalCauseStoppedByUser
				}
			}
			// FIX119 Part C3 — an item that says nothing about its ending
			// (the pre-FIX118 desktop wire, e48) keeps terminal_cause NULL:
			// what was not said is unknown, never "completed". FIX118 wrote
			// `completed` here; a new desktop says status explicitly.
			if cause != "" {
				row.TerminalCause = &cause
			}
			if code := items[i].ErrorCode; code != "" && row.Status == "error" {
				row.ErrorCode = &code
			}
			if sr := items[i].StopReason; sr != "" && row.Status == "completed" {
				row.StopReason = &sr
			}
			if rid := items[i].RunID; rid != "" {
				row.RunID = &rid
			}
			if from := strings.TrimSpace(items[i].FallbackFrom); from != "" && (row.ModelID == nil || from != *row.ModelID) {
				row.FallbackFrom = &from
			}
			// FIX121 — the attempt log belongs to a turn that failed.
			if row.Status == "error" {
				row.Attempts = NormalizeAttempts(items[i].Attempts)
			}
		}
		msgID, inserted, err := msgs.CreateImported(ctx, row, strings.TrimSpace(items[i].ClientID), items[i].CreatedAt)
		if err != nil {
			return 0, 0, nil, err
		}
		if inserted {
			imported++
			if items[i].Role == "user" {
				userRowInserted = true
			}
		} else {
			skipped++
		}
		if replaces := strings.TrimSpace(items[i].ReplacesClientID); replaces != "" && items[i].Role == "assistant" && replaces != strings.TrimSpace(items[i].ClientID) {
			if _, err := msgs.MarkRegeneratedByClientID(ctx, tenantID, convID, replaces); err != nil {
				return 0, 0, nil, err
			}
		}
		// Link uploaded attachments to the message (FIX 10) — same repo
		// path the send handler uses. Tenant+conversation scoped, and it
		// only claims rows with message_id IS NULL, so ids the caller
		// doesn't own (or already-linked files on a replay) are no-ops.
		if len(items[i].FileIDs) > 0 {
			if _, err := filesTx.AttachToMessage(
				ctx, tenantID, convID, msgID, items[i].FileIDs); err != nil {
				return 0, 0, nil, err
			}
		}
	}
	// message_count is app-maintained; bump by rows ACTUALLY inserted —
	// conflict-skipped replays must not inflate the count.
	if imported > 0 {
		if err := convs.IncrementMessageCount(ctx, convID, imported); err != nil {
			return 0, 0, nil, err
		}
	}
	// FIX124 — a desktop chat has a name from its first message too. The
	// import path never wrote one, so a desktop chat whose first answers
	// failed stayed "New conversation" (the owner's 66b0122a).
	if userRowInserted {
		writePlaceholderTitle(ctx, convs, msgs, tenantID, convID)
	}
	if err := tx.Commit(ctx); err != nil {
		return 0, 0, nil, fmt.Errorf("import messages: commit: %w", err)
	}
	if imported > 0 && s.afterImport != nil {
		s.afterImport(ctx, tenantID, convID)
	}
	return imported, skipped, rejected, nil
}
