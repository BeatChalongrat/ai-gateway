package repository

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
)

type ConversationRepo struct {
	db DB
}

func NewConversationRepo(db *pgxpool.Pool) *ConversationRepo {
	return &ConversationRepo{db: db}
}

func (r *ConversationRepo) WithTx(tx pgx.Tx) *ConversationRepo {
	return &ConversationRepo{db: tx}
}

// Create inserts a new conversation. “message_count“ defaults to
// 0 via the column default; the title defaults to "New conversation"
// likewise. Callers should populate “tenant_id“, “user_id“,
// “model_id“ only; everything else is server-side.
func (r *ConversationRepo) Create(ctx context.Context, c *model.Conversation) (*model.Conversation, error) {
	const q = `
		INSERT INTO chat.conversations (tenant_id, user_id, model_id)
		VALUES ($1, $2, $3)
		RETURNING id, title, message_count, is_deleted, deleted_at, created_at, updated_at, knowledge_base_id, memory_enabled,
		       reasoning_enabled, reasoning_mode, show_reasoning_summary, locked_model, is_pinned, folder_id
	`
	err := r.db.QueryRow(ctx, q, c.TenantID, c.UserID, c.ModelID).Scan(
		&c.ID, &c.Title, &c.MessageCount, &c.IsDeleted,
		&c.DeletedAt, &c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
		&c.ReasoningEnabled, &c.ReasoningMode, &c.ShowReasoningSummary, &c.LockedModel, &c.IsPinned, &c.FolderID,
	)
	if err != nil {
		return nil, fmt.Errorf("create conversation: %w", err)
	}
	return c, nil
}

// CreateImported inserts a desktop-imported conversation keyed by the
// client's local id (migration 026). ``ON CONFLICT DO NOTHING`` against the
// partial unique index makes concurrent imports of the same client_id
// converge on a single row: returns true when this call inserted the row,
// false on conflict (the caller resolves the winner via GetByClientID).
// ``createdAt`` is the desktop's historical timestamp — preserved, never
// re-stamped; nil falls back to NOW().
func (r *ConversationRepo) CreateImported(
	ctx context.Context, c *model.Conversation, clientID string, createdAt *time.Time,
) (bool, error) {
	const q = `
		INSERT INTO chat.conversations
			(tenant_id, user_id, model_id, title, client_id, origin, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, 'desktop_agent', COALESCE($6, NOW()), COALESCE($6, NOW()))
		ON CONFLICT (tenant_id, user_id, client_id) WHERE client_id IS NOT NULL
		DO NOTHING
		RETURNING id, title, message_count, is_deleted, deleted_at, created_at, updated_at,
		          knowledge_base_id, memory_enabled, reasoning_enabled, reasoning_mode,
		          show_reasoning_summary, locked_model, is_pinned, folder_id
	`
	err := r.db.QueryRow(ctx, q, c.TenantID, c.UserID, c.ModelID, c.Title, clientID, createdAt).Scan(
		&c.ID, &c.Title, &c.MessageCount, &c.IsDeleted,
		&c.DeletedAt, &c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
		&c.ReasoningEnabled, &c.ReasoningMode, &c.ShowReasoningSummary, &c.LockedModel, &c.IsPinned, &c.FolderID,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return false, nil // conflict — a row with this client_id already exists
		}
		return false, fmt.Errorf("create imported conversation: %w", err)
	}
	return true, nil
}

// GetByClientID resolves an imported conversation by its desktop-side id.
// Deliberately does NOT filter is_deleted: the desktop mirror must be able
// to find (and resurrect) a web-deleted copy — see ImportService.
func (r *ConversationRepo) GetByClientID(
	ctx context.Context, tenantID, userID uuid.UUID, clientID string,
) (*model.Conversation, error) {
	const q = `
		SELECT id, tenant_id, user_id, title, model_id, message_count,
		       is_deleted, deleted_at, created_at, updated_at, knowledge_base_id, memory_enabled,
		       reasoning_enabled, reasoning_mode, show_reasoning_summary, locked_model, is_pinned, folder_id
		FROM chat.conversations
		WHERE tenant_id = $1 AND user_id = $2 AND client_id = $3
	`
	c := &model.Conversation{}
	err := r.db.QueryRow(ctx, q, tenantID, userID, clientID).Scan(
		&c.ID, &c.TenantID, &c.UserID, &c.Title, &c.ModelID,
		&c.MessageCount, &c.IsDeleted, &c.DeletedAt,
		&c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
		&c.ReasoningEnabled, &c.ReasoningMode, &c.ShowReasoningSummary, &c.LockedModel, &c.IsPinned, &c.FolderID,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get conversation by client id: %w", err)
	}
	return c, nil
}

// Restore reverses a soft-delete. No longer called by the desktop import
// path (FIX 9: deletes are authoritative and imports against a deleted
// conversation return 410 Gone instead of resurrecting); kept for potential
// ops/admin recovery use.
func (r *ConversationRepo) Restore(ctx context.Context, tenantID, convID uuid.UUID) error {
	const q = `
		UPDATE chat.conversations
		SET is_deleted = false, deleted_at = NULL, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2
	`
	if _, err := r.db.Exec(ctx, q, convID, tenantID); err != nil {
		return fmt.Errorf("restore conversation: %w", err)
	}
	return nil
}

// GetByID returns the conversation only when the tenant scope
// matches AND the row isn't soft-deleted. The combined predicate
// keeps the ownership and lifecycle gates in a single round-trip
// so handlers can't accidentally skip either check.
func (r *ConversationRepo) GetByID(ctx context.Context, tenantID, convID uuid.UUID) (*model.Conversation, error) {
	const q = `
		SELECT id, tenant_id, user_id, title, model_id, message_count,
		       is_deleted, deleted_at, created_at, updated_at, knowledge_base_id, memory_enabled,
		       reasoning_enabled, reasoning_mode, show_reasoning_summary, locked_model, is_pinned, folder_id
		FROM chat.conversations
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	c := &model.Conversation{}
	err := r.db.QueryRow(ctx, q, convID, tenantID).Scan(
		&c.ID, &c.TenantID, &c.UserID, &c.Title, &c.ModelID,
		&c.MessageCount, &c.IsDeleted, &c.DeletedAt,
		&c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
		&c.ReasoningEnabled, &c.ReasoningMode, &c.ShowReasoningSummary, &c.LockedModel, &c.IsPinned, &c.FolderID,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get conversation by id: %w", err)
	}
	return c, nil
}

// ListByUser returns the user's conversations sorted by
// “updated_at DESC“. Cursor-based pagination: the caller supplies
// the cursor row's ID; the query looks up that row's updated_at and
// returns the next “limit“ rows after it.
//
// Returns (rows, nextCursor, error). “nextCursor == uuid.Nil“ ⇒
// no more rows.
//
// Pagination predicate is a COMPOSITE “(updated_at, id)“ so rapid
// inserts that tie on microsecond-precision “updated_at“ don't get
// silently dropped. The ORDER BY mirrors the predicate to keep the
// scan deterministic and index-friendly.
func (r *ConversationRepo) ListByUser(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	limit int,
	cursor uuid.UUID,
) ([]model.Conversation, uuid.UUID, error) {
	// Resolve the cursor row's (updated_at, id) tuple so the WHERE
	// clause stays indexable. Empty cursor → start from the most-
	// recently-updated row.
	var cursorTs *string
	var cursorID *string
	var cursorPinned *bool
	if cursor != uuid.Nil {
		var ts string
		var pinned bool
		err := r.db.QueryRow(ctx,
			`SELECT updated_at::text, is_pinned FROM chat.conversations WHERE id = $1 AND tenant_id = $2`,
			cursor, tenantID,
		).Scan(&ts, &pinned)
		if err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				// Unknown cursor → start from top.
				cursorTs = nil
			} else {
				return nil, uuid.Nil, fmt.Errorf("cursor lookup: %w", err)
			}
		} else {
			cursorTs = &ts
			cid := cursor.String()
			cursorID = &cid
			cursorPinned = &pinned
		}
	}

	// limit+1 lets us tell the caller whether there's another page
	// without a second query. The (limit+1)th row becomes next_cursor.
	const q = `
		SELECT id, tenant_id, user_id, title, model_id, message_count,
		       is_deleted, deleted_at, created_at, updated_at, knowledge_base_id, memory_enabled,
		       reasoning_enabled, reasoning_mode, show_reasoning_summary, locked_model, is_pinned, folder_id
		FROM chat.conversations
		WHERE tenant_id = $1
		  AND user_id = $2
		  AND is_deleted = false
		  AND (
		    $3::timestamptz IS NULL
		    OR (is_pinned, updated_at, id) < ($6::bool, $3::timestamptz, $4::uuid)
		  )
		ORDER BY is_pinned DESC, updated_at DESC, id DESC
		LIMIT $5
	`
	rows, err := r.db.Query(ctx, q, tenantID, userID, cursorTs, cursorID, limit+1, cursorPinned)
	if err != nil {
		return nil, uuid.Nil, fmt.Errorf("list conversations: %w", err)
	}
	defer rows.Close()

	out := make([]model.Conversation, 0, limit)
	for rows.Next() {
		var c model.Conversation
		if err := rows.Scan(
			&c.ID, &c.TenantID, &c.UserID, &c.Title, &c.ModelID,
			&c.MessageCount, &c.IsDeleted, &c.DeletedAt,
			&c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
			&c.ReasoningEnabled, &c.ReasoningMode, &c.ShowReasoningSummary, &c.LockedModel, &c.IsPinned, &c.FolderID,
		); err != nil {
			return nil, uuid.Nil, fmt.Errorf("list scan: %w", err)
		}
		out = append(out, c)
	}
	if err := rows.Err(); err != nil {
		return nil, uuid.Nil, err
	}

	// has_more iff the limit+1 probe row came back. The cursor for
	// the next page MUST be the last SERVED row (out[limit-1]) — NOT
	// the probe row — otherwise the strict ``<`` predicate skips
	// the probe row on the next query and we silently drop one row
	// per page advance.
	var next uuid.UUID
	if len(out) > limit {
		out = out[:limit]
		next = out[limit-1].ID
	}
	return out, next, nil
}

// SearchByUser runs a server-side ILIKE on conversation titles. We
// don't bother with full-text indexes yet — for SaaS scale (<100k
// per tenant) ILIKE on the BTREE-indexed (tenant_id, user_id)
// partition is fast enough; a GIN trigram index is a future
// migration.
//
// Returns conversations sorted updated_at DESC, capped at “limit“.
func (r *ConversationRepo) SearchByUser(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	query string,
	limit int,
) ([]model.Conversation, error) {
	const q = `
		SELECT id, tenant_id, user_id, title, model_id, message_count,
		       is_deleted, deleted_at, created_at, updated_at, knowledge_base_id, memory_enabled,
		       reasoning_enabled, reasoning_mode, show_reasoning_summary, locked_model, is_pinned, folder_id
		FROM chat.conversations
		WHERE tenant_id = $1
		  AND user_id = $2
		  AND is_deleted = false
		  AND title ILIKE $3
		ORDER BY updated_at DESC, id DESC
		LIMIT $4
	`
	rows, err := r.db.Query(ctx, q, tenantID, userID, "%"+query+"%", limit)
	if err != nil {
		return nil, fmt.Errorf("search conversations: %w", err)
	}
	defer rows.Close()
	out := make([]model.Conversation, 0)
	for rows.Next() {
		var c model.Conversation
		if err := rows.Scan(
			&c.ID, &c.TenantID, &c.UserID, &c.Title, &c.ModelID,
			&c.MessageCount, &c.IsDeleted, &c.DeletedAt,
			&c.CreatedAt, &c.UpdatedAt, &c.KnowledgeBaseID, &c.MemoryEnabled,
			&c.ReasoningEnabled, &c.ReasoningMode, &c.ShowReasoningSummary, &c.LockedModel, &c.IsPinned, &c.FolderID,
		); err != nil {
			return nil, fmt.Errorf("search scan: %w", err)
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// ConversationSearchHit is one row from SearchTitlesForUser —
// includes the live message count so the search service can show
// "Conversation 'Q1 review' (24 messages)" without a second query
// per row.
type ConversationSearchHit struct {
	ID           uuid.UUID
	Title        string
	UpdatedAt    time.Time
	MessageCount int
	Rank         float64
}

// SearchTitlesForUser is the S6 tsvector counterpart to
// SearchByUser. Different use case from the sidebar's
// search-as-you-type (which uses ILIKE for substring matches
// against partial words): this one does whole-word matching for
// the AI's search_past_conversations tool + the /api/v1/search
// endpoint. Returns hits sorted by ts_rank desc then updated_at.
//
// Limit defaults to 10, capped at 50.
func (r *ConversationRepo) SearchTitlesForUser(
	ctx context.Context, tenantID, userID uuid.UUID, query string, limit int,
) ([]ConversationSearchHit, error) {
	q := strings.TrimSpace(query)
	if q == "" {
		return nil, nil
	}
	if limit <= 0 {
		limit = 10
	}
	if limit > 50 {
		limit = 50
	}
	const sql = `
		SELECT
			c.id,
			c.title,
			c.updated_at,
			c.message_count,
			ts_rank(c.search_vector, plainto_tsquery('simple', $3))::float8 AS rank
		FROM chat.conversations c
		WHERE c.tenant_id = $1
		  AND c.user_id   = $2
		  AND c.is_deleted = false
		  AND c.search_vector @@ plainto_tsquery('simple', $3)
		ORDER BY rank DESC, c.updated_at DESC
		LIMIT $4
	`
	rows, err := r.db.Query(ctx, sql, tenantID, userID, q, limit)
	if err != nil {
		return nil, fmt.Errorf("search titles for user: %w", err)
	}
	defer rows.Close()
	out := make([]ConversationSearchHit, 0, limit)
	for rows.Next() {
		var h ConversationSearchHit
		if err := rows.Scan(&h.ID, &h.Title, &h.UpdatedAt, &h.MessageCount, &h.Rank); err != nil {
			return nil, fmt.Errorf("search titles scan: %w", err)
		}
		out = append(out, h)
	}
	return out, rows.Err()
}

// UpdateTitle changes “title“ and bumps “updated_at“. The
// WHERE clause also enforces the tenant scope so a wrong tenant_id
// can't accidentally rename someone else's conversation.
func (r *ConversationRepo) UpdateTitle(ctx context.Context, tenantID, convID uuid.UUID, title string) error {
	const q = `
		UPDATE chat.conversations
		SET title = $3, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, title)
	if err != nil {
		return fmt.Errorf("update title: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// UpdateModel changes the conversation's default model_id and
// bumps “updated_at“. Tenant-scoped via the WHERE clause so a
// wrong tenant_id can't reassign someone else's conversation.
func (r *ConversationRepo) UpdateModel(ctx context.Context, tenantID, convID uuid.UUID, modelID string) error {
	// Clearing locked_model here is the "user override" half of Fix 5:
	// when the user changes the picker we drop any auto-lock so a switch
	// back to "auto" re-resolves fresh, and an explicit pick is stable on
	// its own (model_id is concrete).
	const q = `
		UPDATE chat.conversations
		SET model_id = $3, locked_model = '', updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, modelID)
	if err != nil {
		return fmt.Errorf("update model: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// SetLockedModel pins the concrete model an "auto" conversation resolved
// to on its first turn (Fix 5). Best-effort from the send path: a failed
// write just means the next turn re-resolves (to the same model anyway,
// given a stable catalog), so the caller logs and continues rather than
// failing the user's message. Tenant-scoped via the WHERE clause.
func (r *ConversationRepo) SetLockedModel(ctx context.Context, tenantID, convID uuid.UUID, modelID string) error {
	const q = `
		UPDATE chat.conversations
		SET locked_model = $3
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, modelID)
	if err != nil {
		return fmt.Errorf("set locked model: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// UpdateKnowledgeBase binds (or unbinds, with kbID = uuid.Nil) the
// conversation to a knowledge base. The FK on conversations enforces
// tenant-cross-pollination isolation at the service layer — the
// handler is expected to verify the KB belongs to the caller's
// tenant before invoking this method.
func (r *ConversationRepo) UpdateKnowledgeBase(
	ctx context.Context, tenantID, convID uuid.UUID, kbID uuid.UUID,
) error {
	const q = `
		UPDATE chat.conversations
		SET knowledge_base_id = $3, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	// uuid.Nil → NULL so callers can unbind via the same entry point.
	var arg any
	if kbID == uuid.Nil {
		arg = nil
	} else {
		arg = kbID
	}
	tag, err := r.db.Exec(ctx, q, convID, tenantID, arg)
	if err != nil {
		return fmt.Errorf("update knowledge_base_id: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// UpdateMemoryEnabled toggles the per-conversation cross-conversation
// memory layer (S1 — migration 014). Tenant-scoped via the WHERE
// clause; handler/service is expected to have asserted caller
// ownership first.
func (r *ConversationRepo) UpdateMemoryEnabled(
	ctx context.Context, tenantID, convID uuid.UUID, enabled bool,
) error {
	const q = `
		UPDATE chat.conversations
		SET memory_enabled = $3, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, enabled)
	if err != nil {
		return fmt.Errorf("update memory_enabled: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// UpdatePinned flips the sidebar pin flag (migration 021). Tenant-scoped
// via the WHERE clause; the service layer asserts caller ownership first.
// Deliberately does NOT touch updated_at — ListByUser orders pinned rows
// first regardless of their timestamp, and within the PINNED section the
// frontend sorts by the TRUE last-activity updated_at, so a pin/unpin must
// not masquerade as fresh activity.
func (r *ConversationRepo) UpdatePinned(
	ctx context.Context, tenantID, convID uuid.UUID, pinned bool,
) error {
	const q = `
		UPDATE chat.conversations
		SET is_pinned = $3
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, pinned)
	if err != nil {
		return fmt.Errorf("update is_pinned: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// MoveToFolder sets (or clears, with folderID = nil) a conversation's
// folder membership (migration 022). Tenant-scoped via the WHERE clause;
// the service asserts caller ownership AND folder ownership first. nil →
// NULL = "unfiled". Bumps updated_at so the move surfaces as fresh activity
// in the destination group.
func (r *ConversationRepo) MoveToFolder(
	ctx context.Context, tenantID, convID uuid.UUID, folderID *uuid.UUID,
) error {
	const q = `
		UPDATE chat.conversations
		SET folder_id = $3, updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	// *uuid.UUID nil → NULL so the same entry point files and unfiles.
	var arg any
	if folderID != nil {
		arg = *folderID
	}
	tag, err := r.db.Exec(ctx, q, convID, tenantID, arg)
	if err != nil {
		return fmt.Errorf("move conversation to folder: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// MoveMultipleToFolder files (or unfiles, with folderID = nil) several
// conversations at once. Owner-scoped to the tenant; ids the tenant doesn't
// own simply match nothing. Returns the number of rows actually moved.
func (r *ConversationRepo) MoveMultipleToFolder(
	ctx context.Context, tenantID uuid.UUID, convIDs []uuid.UUID, folderID *uuid.UUID,
) (int64, error) {
	if len(convIDs) == 0 {
		return 0, nil
	}
	const q = `
		UPDATE chat.conversations
		SET folder_id = $3, updated_at = NOW()
		WHERE id = ANY($1) AND tenant_id = $2 AND is_deleted = false
	`
	var arg any
	if folderID != nil {
		arg = *folderID
	}
	tag, err := r.db.Exec(ctx, q, convIDs, tenantID, arg)
	if err != nil {
		return 0, fmt.Errorf("move conversations to folder: %w", err)
	}
	return tag.RowsAffected(), nil
}

// UpdateReasoningSettings persists T4 thinking-mode preferences for
// a single conversation. All three fields are pointer-typed so the
// handler can patch any subset (e.g. flip enabled without touching
// the previously-chosen mode). Tenant-scoped via WHERE; ownership
// gate runs at the service layer.
func (r *ConversationRepo) UpdateReasoningSettings(
	ctx context.Context, tenantID, convID uuid.UUID,
	enabled *bool, mode *string, showSummary *bool,
) error {
	// COALESCE keeps unset fields at their current value — same
	// pattern the memory toggle would use if it ever grew a partial
	// PATCH. Doing it in one statement keeps the update atomic.
	const q = `
		UPDATE chat.conversations
		SET reasoning_enabled      = COALESCE($3, reasoning_enabled),
		    reasoning_mode         = COALESCE($4, reasoning_mode),
		    show_reasoning_summary = COALESCE($5, show_reasoning_summary),
		    updated_at             = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, enabled, mode, showSummary)
	if err != nil {
		return fmt.Errorf("update reasoning settings: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// SoftDelete sets “is_deleted = true“ + “deleted_at = NOW()“.
// Idempotent: re-deleting a soft-deleted row returns ErrNotFound
// (we treat the row as no-longer-present at this layer).
func (r *ConversationRepo) SoftDelete(ctx context.Context, tenantID, convID uuid.UUID) error {
	const q = `
		UPDATE chat.conversations
		SET is_deleted = true, deleted_at = NOW(), updated_at = NOW()
		WHERE id = $1 AND tenant_id = $2 AND is_deleted = false
	`
	tag, err := r.db.Exec(ctx, q, convID, tenantID)
	if err != nil {
		return fmt.Errorf("soft delete conversation: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// IncrementMessageCount bumps “message_count“ by “delta“ (can
// be negative if a regenerate flow needs to retract). Also touches
// “updated_at“ so the sidebar sort surfaces the active
// conversation. Best-effort logical update — does NOT verify
// ownership (the caller has already done so via GetByID).
func (r *ConversationRepo) IncrementMessageCount(ctx context.Context, convID uuid.UUID, delta int) error {
	const q = `
		UPDATE chat.conversations
		SET message_count = message_count + $2, updated_at = NOW()
		WHERE id = $1
	`
	if _, err := r.db.Exec(ctx, q, convID, delta); err != nil {
		return fmt.Errorf("increment message count: %w", err)
	}
	return nil
}

// UpdateTimestamp touches “updated_at“ without changing anything
// else. Used by the streaming path between user-message persist and
// assistant-message persist so the sidebar shows fresh activity even
// while the assistant reply is still generating.
func (r *ConversationRepo) UpdateTimestamp(ctx context.Context, convID uuid.UUID) error {
	const q = `UPDATE chat.conversations SET updated_at = NOW() WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, convID); err != nil {
		return fmt.Errorf("update timestamp: %w", err)
	}
	return nil
}
