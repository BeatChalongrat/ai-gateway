package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// MessageRepo persists conversation messages.
//
// The table is named “chat.messages_masked“ for schema lineage —
// the original gateway design stored masked-only content. Web Chat
// mode stores REAL content in the same column; the rename to
// “content“ is a Phase 2 migration. The column name on disk is
// “content_masked“ to keep migrations forward-compatible.
type MessageRepo struct {
	db DB
	// canonicalHistory gates whether history reads SELECT the canonical
	// columns (Phase 4A.2.2.1, H4 deploy-order). When false, queries never
	// reference canonical_* columns, so a pre-migration DB (migration 024
	// not applied) keeps working with the legacy path. Set from config at
	// wiring time; defaults false.
	canonicalHistory bool
	// displayLifecycle gates whether writes/reads reference the
	// ``display_state`` column (Phase 4A.2.4, migration 025). Same H4
	// deploy-order contract as ``canonicalHistory``: when false, queries never
	// reference ``display_state`` so a pre-migration DB keeps working.
	displayLifecycle bool
}

func NewMessageRepo(db *pgxpool.Pool) *MessageRepo {
	return &MessageRepo{db: db}
}

// SetCanonicalHistory toggles canonical-column reads (H4). Only set true
// once migration 024 is applied AND the feature is enabled.
func (r *MessageRepo) SetCanonicalHistory(enabled bool) {
	r.canonicalHistory = enabled
}

// SetDisplayLifecycle toggles display_state column reads/writes (Phase
// 4A.2.4). Only set true once migration 025 is applied AND the feature is
// enabled.
func (r *MessageRepo) SetDisplayLifecycle(enabled bool) {
	r.displayLifecycle = enabled
}

func (r *MessageRepo) WithTx(tx pgx.Tx) *MessageRepo {
	return &MessageRepo{db: tx, canonicalHistory: r.canonicalHistory, displayLifecycle: r.displayLifecycle}
}

// Create inserts a new message row. “ContentMasked“ carries the
// real text in Web Chat mode; the column rename is deferred to keep
// schemas backward-compatible with the gateway-only build.
func (r *MessageRepo) Create(ctx context.Context, m *model.Message) (*model.Message, error) {
	const q = `
		INSERT INTO chat.messages_masked
			(conversation_id, tenant_id, user_id, role, content_masked,
			 status, model_id, token_usage_input, token_usage_output, secure_trace_id,
			 parent_id, branch_index, is_active,
			 run_id, is_test)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
		        $14, COALESCE((SELECT c.is_test FROM chat.conversations c WHERE c.id = $1), false))
		RETURNING id, created_at
	`
	// Defaults so callers don't have to set them for the common
	// (non-branched) path: branch_index=0, is_active=true.
	if !m.IsActive && m.BranchIndex == 0 && m.ParentID == nil {
		m.IsActive = true
	}
	err := r.db.QueryRow(ctx, q,
		m.ConversationID, m.TenantID, m.UserID, m.Role, m.ContentMasked,
		m.Status, m.ModelID, m.TokenUsageInput, m.TokenUsageOutput, m.SecureTraceID,
		m.ParentID, m.BranchIndex, m.IsActive,
		m.RunID,
	).Scan(&m.ID, &m.CreatedAt)
	if err != nil {
		// FIX121 — migration 047: one user row per (conversation, run id)
		// on the native send path. Losing that insert means another copy
		// of the same request got there first.
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" && pgErr.ConstraintName == userRunUniqueIndex {
			return nil, ErrDuplicateRun
		}
		return nil, fmt.Errorf("create message: %w", err)
	}
	return m, nil
}

// userRunUniqueIndex is the unique index of migration 047.
const userRunUniqueIndex = "messages_masked_user_run_uidx"

// ErrDuplicateRun — a user row for this (conversation, run id) exists.
var ErrDuplicateRun = errors.New("message: a user row for this run id already exists")

// FindByRun returns the native (non-imported) row of one role that a run id
// produced in a conversation: the newest one, should an older database hold
// several. ErrNotFound when there is none. FIX121 — the send path's
// idempotency lookup.
func (r *MessageRepo) FindByRun(
	ctx context.Context, tenantID, convID uuid.UUID, runID, role string,
) (*model.Message, error) {
	// The ending columns are read too: a repeat of a turn that FAILED is
	// answered with that failure's own code and attempt log.
	const q = `
		SELECT id, conversation_id, tenant_id, user_id, role, content_masked,
		       status, model_id, created_at,
		       terminal_cause, error_code, run_id, stop_reason, fallback_from, attempts
		FROM chat.messages_masked
		WHERE conversation_id = $1 AND tenant_id = $2 AND run_id = $3 AND role = $4
		  AND client_id IS NULL
		ORDER BY created_at DESC, id DESC
		LIMIT 1
	`
	m := &model.Message{}
	err := r.db.QueryRow(ctx, q, convID, tenantID, runID, role).Scan(
		&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role, &m.ContentMasked,
		&m.Status, &m.ModelID, &m.CreatedAt,
		&m.TerminalCause, &m.ErrorCode, &m.RunID, &m.StopReason, &m.FallbackFrom, &m.Attempts,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("find message by run: %w", err)
	}
	return m, nil
}

// CreateImported inserts a desktop-imported message (migration 026).
// “ON CONFLICT (conversation_id, client_id) DO NOTHING“ makes replays of
// the same import batch no-ops — the caller counts skips via the returned
// bool. “createdAt“ is the desktop's historical timestamp, preserved
// verbatim (never re-stamped to NOW()). The canonical fields keep their
// 'legacy' defaults so imported content is excluded from canonical-history
// replay.
//
// FIX118: status, terminal_cause and error_code come from the caller — the
// desktop knows how its turn ended and says so. Before this the column was
// the literal 'completed', so a turn that failed on the desktop became a
// success the moment it was imported.
//
// Returns the row's SERVER message id in both outcomes (FIX 10): the
// freshly-inserted id, or — on a conflict replay — the existing row's id
// resolved by (conversation_id, client_id), so the caller can link file
// attachments idempotently.
func (r *MessageRepo) CreateImported(
	ctx context.Context, m *model.Message, clientID string, createdAt time.Time,
) (uuid.UUID, bool, error) {
	status := m.Status
	if status == "" {
		status = "completed"
	}
	const q = `
		INSERT INTO chat.messages_masked
			(conversation_id, tenant_id, user_id, role, content_masked,
			 status, model_id, client_id, created_at, is_active,
			 terminal_cause, error_code, run_id, stop_reason, fallback_from, attempts, is_test)
		VALUES ($1, $2, $3, $4, $5, $9, $6, $7, $8, true, $10, $11, $12, $13, $14, $15::jsonb,
		        COALESCE((SELECT c.is_test FROM chat.conversations c WHERE c.id = $1), false))
		ON CONFLICT (conversation_id, client_id) WHERE client_id IS NOT NULL
		DO NOTHING
		RETURNING id
	`
	var id uuid.UUID
	err := r.db.QueryRow(ctx, q,
		m.ConversationID, m.TenantID, m.UserID, m.Role, m.ContentMasked,
		m.ModelID, clientID, createdAt, status, m.TerminalCause, m.ErrorCode, m.RunID, m.StopReason, m.FallbackFrom,
		jsonOrNull(m.Attempts),
	).Scan(&id)
	if err == nil {
		return id, true, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, false, fmt.Errorf("create imported message: %w", err)
	}
	// Conflict — resolve the existing row (idempotent replay).
	const sel = `
		SELECT id FROM chat.messages_masked
		WHERE conversation_id = $1 AND client_id = $2
	`
	if err := r.db.QueryRow(ctx, sel, m.ConversationID, clientID).Scan(&id); err != nil {
		return uuid.Nil, false, fmt.Errorf("resolve imported message by client id: %w", err)
	}
	return id, false, nil
}

// ResolveByClientID maps a desktop host's own message id (client_id,
// e.g. "asst_<hex>") to the SERVER message id (FIX47.6 Part D). The
// desktop page addresses messages by the id the host minted; anything
// keyed server-side (feedback) must resolve through this or 400 on
// every host-authored message. Tenant-scoped; newest wins if a client
// id was ever reused across conversations.
func (r *MessageRepo) ResolveByClientID(
	ctx context.Context, tenantID uuid.UUID, clientID string,
) (uuid.UUID, error) {
	const q = `
		SELECT id FROM chat.messages_masked
		WHERE tenant_id = $1 AND client_id = $2
		ORDER BY created_at DESC
		LIMIT 1
	`
	var id uuid.UUID
	if err := r.db.QueryRow(ctx, q, tenantID, clientID).Scan(&id); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return uuid.Nil, ErrNotFound
		}
		return uuid.Nil, fmt.Errorf("resolve message by client id: %w", err)
	}
	return id, nil
}

// ListByConversation returns the message history for one
// conversation, ordered ASCENDING by “created_at“ so the chat UI
// renders oldest-first. “cursor“ is used for backwards-paging
// (load older messages); empty cursor → latest page.
//
// Returns (rows, hasMore, error).
func (r *MessageRepo) ListByConversation(
	ctx context.Context,
	tenantID, convID uuid.UUID,
	limit int,
	cursor uuid.UUID,
) ([]model.Message, bool, error) {
	// Composite cursor (created_at, id) so rows that tie on the
	// microsecond timestamp aren't silently dropped between pages.
	var cursorTs *string
	var cursorID *string
	if cursor != uuid.Nil {
		var ts string
		err := r.db.QueryRow(ctx,
			`SELECT created_at::text FROM chat.messages_masked WHERE id = $1 AND tenant_id = $2`,
			cursor, tenantID,
		).Scan(&ts)
		if err != nil {
			if !errors.Is(err, pgx.ErrNoRows) {
				return nil, false, fmt.Errorf("cursor lookup: %w", err)
			}
			cursorTs = nil
		} else {
			cursorTs = &ts
			cid := cursor.String()
			cursorID = &cid
		}
	}

	// limit+1 lets us tell ``has_more``. The cursor for the next
	// page is the LAST served row (see conversation_repo for the
	// rationale) — never the probe row. Filters to is_active=true
	// so inactive branches don't pollute the default view.
	// T4: reasoning_content surfaces the thinking summary the model
	// emitted for assistant turns where the user enabled reasoning.
	// Only fetched on the user-facing list (this read powers the
	// React chat thread); the context-building reads below leave it
	// out so prompt history stays focused on visible content.
	// Phase 4A.2.4 (H4-style): the display-lifecycle columns are selected ONLY
	// when the feature is enabled, so a pre-migration-025 DB never references a
	// missing column. The read handler then applies the representation policy.
	extraCols := ""
	if r.displayLifecycle {
		extraCols = `, content_canonical, canonical_status, display_state`
	}
	q := `
		SELECT id, conversation_id, tenant_id, user_id, role, content_masked,
		       status, model_id, token_usage_input, token_usage_output,
		       secure_trace_id, created_at, parent_id, branch_index, is_active,
		       reasoning_content, search_sources, client_id,
		       terminal_cause, error_code, run_id, stop_reason, fallback_from, attempts` + extraCols + `
		FROM chat.messages_masked
		WHERE conversation_id = $1
		  AND tenant_id = $2
		  AND is_active = true
		  AND (
		    $3::timestamptz IS NULL
		    OR (created_at, id) < ($3::timestamptz, $4::uuid)
		  )
		ORDER BY created_at DESC, id DESC
		LIMIT $5
	`
	rows, err := r.db.Query(ctx, q, convID, tenantID, cursorTs, cursorID, limit+1)
	if err != nil {
		return nil, false, fmt.Errorf("list messages: %w", err)
	}
	defer rows.Close()

	out := make([]model.Message, 0, limit)
	for rows.Next() {
		var m model.Message
		dest := []any{
			&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
			&m.ContentMasked, &m.Status, &m.ModelID,
			&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
			&m.CreatedAt, &m.ParentID, &m.BranchIndex, &m.IsActive,
			&m.ReasoningContent, &m.SearchSources, &m.ClientID,
			&m.TerminalCause, &m.ErrorCode, &m.RunID, &m.StopReason, &m.FallbackFrom, &m.Attempts,
		}
		if r.displayLifecycle {
			dest = append(dest, &m.ContentCanonical, &m.CanonicalStatus, &m.DisplayState)
		}
		if err := rows.Scan(dest...); err != nil {
			return nil, false, fmt.Errorf("list scan: %w", err)
		}
		out = append(out, m)
	}
	if err := rows.Err(); err != nil {
		return nil, false, err
	}

	hasMore := len(out) > limit
	if hasMore {
		out = out[:limit]
	}
	// Flip to ascending for the wire response.
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, hasMore, nil
}

// ListByIDs returns exactly the requested rows (T3 — the share
// snapshot read), chronological. Deliberately NOT filtered on
// is_active: the snapshot pins rows as they stood at share time, so a
// message later edited away (forked to an inactive branch) still
// renders on the public view, while a hard-DELETED/purged row simply
// isn't returned — deletion wins over the snapshot.
func (r *MessageRepo) ListByIDs(
	ctx context.Context,
	tenantID uuid.UUID,
	ids []uuid.UUID,
) ([]model.Message, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	idStrs := make([]string, len(ids))
	for i, id := range ids {
		idStrs[i] = id.String()
	}
	extraCols := ""
	if r.displayLifecycle {
		extraCols = `, content_canonical, canonical_status, display_state`
	}
	q := `
		SELECT id, conversation_id, tenant_id, user_id, role, content_masked,
		       status, model_id, token_usage_input, token_usage_output,
		       secure_trace_id, created_at, parent_id, branch_index, is_active,
		       reasoning_content, search_sources, client_id` + extraCols + `
		FROM chat.messages_masked
		WHERE tenant_id = $1
		  AND id = ANY($2::uuid[])
		ORDER BY created_at ASC, id ASC
	`
	rows, err := r.db.Query(ctx, q, tenantID, idStrs)
	if err != nil {
		return nil, fmt.Errorf("list messages by ids: %w", err)
	}
	defer rows.Close()

	out := make([]model.Message, 0, len(ids))
	for rows.Next() {
		var m model.Message
		dest := []any{
			&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
			&m.ContentMasked, &m.Status, &m.ModelID,
			&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
			&m.CreatedAt, &m.ParentID, &m.BranchIndex, &m.IsActive,
			&m.ReasoningContent, &m.SearchSources, &m.ClientID,
		}
		if r.displayLifecycle {
			dest = append(dest, &m.ContentCanonical, &m.CanonicalStatus, &m.DisplayState)
		}
		if err := rows.Scan(dest...); err != nil {
			return nil, fmt.Errorf("list by ids scan: %w", err)
		}
		out = append(out, m)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return out, nil
}

// MaxContextLoadCap bounds how many history rows one context load may
// pull from chat.messages_masked (FIX33 A-W1). It is a query-cost
// ceiling, NOT the context limit — MAX_CONTEXT_MESSAGES (default 1000)
// is what the service asks for, and the ContextCompiler trims by
// tokens. Any rows beyond the loaded window are reported to the
// compiler via CountMessages so they are never lost silently.
const MaxContextLoadCap = 2000

// ListContextByConversation is a tighter variant used by the
// MessageService to build the conversation context that ships to
// the Gateway. Returns the most recent “limit“ messages in
// CHRONOLOGICAL order (oldest → newest) and includes all statuses
// so a paused / cancelled mid-stream still gets accounted for in
// future turns.
//
// FIX100 Part A: superseded rows are excluded here for the same reason
// as in ListContextBeforeMessage — a regenerate leaves the replaced row
// is_active, so without this every SUBSEQUENT ordinary send re-loaded it
// too, and a fix to the regenerate query alone would have been undone by
// the next message the user typed.
//
// FIX33 A-W1: the old hard cap of 200 dropped the head of a long
// conversation BEFORE the ContextCompiler ever saw it — with no
// marker (FIX32 §1.1 measured it: recall of turns 1–100 failed at
// 200/400 turns). The cap is now MaxContextLoadCap, a sanity
// ceiling on the DB load only; the compiler decides by TOKEN
// budget, and MessageService reports any rows beyond the loaded
// window (CountMessages − len) as OmittedOlder so they are counted,
// marked, and folded into the state extraction. Nothing is dropped
// silently any more.
func (r *MessageRepo) ListContextByConversation(
	ctx context.Context,
	tenantID, convID uuid.UUID,
	limit int,
) ([]model.Message, error) {
	if limit <= 0 {
		limit = 50
	}
	if limit > MaxContextLoadCap {
		limit = MaxContextLoadCap
	}
	q := `
		SELECT ` + r.messageContextColumns() + `
		FROM chat.messages_masked
		WHERE conversation_id = $1 AND tenant_id = $2 AND is_active = true
		  AND status <> 'regenerated'
		ORDER BY created_at DESC
		LIMIT $3
	`
	rows, err := r.db.Query(ctx, q, convID, tenantID, limit)
	if err != nil {
		return nil, fmt.Errorf("list context messages: %w", err)
	}
	defer rows.Close()

	out := make([]model.Message, 0, limit)
	for rows.Next() {
		var m model.Message
		if err := r.scanContextRow(rows, &m); err != nil {
			return nil, fmt.Errorf("context scan: %w", err)
		}
		out = append(out, m)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, nil
}

// messageContextColumns returns the SELECT column list for context reads.
// H4: the canonical columns are included ONLY when canonical history is
// enabled, so a pre-migration DB never hits a missing column.
func (r *MessageRepo) messageContextColumns() string {
	base := `id, conversation_id, tenant_id, user_id, role, content_masked,
	       status, model_id, token_usage_input, token_usage_output,
	       secure_trace_id, created_at, parent_id, branch_index, is_active`
	if !r.canonicalHistory {
		return base
	}
	// H1: load the integrity fields so the history builder can RE-VALIDATE
	// (not just trust canonical_status).
	return base + `, content_canonical, canonical_status,
	       canonical_schema_version, canonical_generation_id,
	       canonical_sha256, canonical_length`
}

// scanContextRow scans a row produced by messageContextColumns().
func (r *MessageRepo) scanContextRow(rows pgx.Rows, m *model.Message) error {
	if !r.canonicalHistory {
		return rows.Scan(
			&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
			&m.ContentMasked, &m.Status, &m.ModelID,
			&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
			&m.CreatedAt, &m.ParentID, &m.BranchIndex, &m.IsActive,
		)
	}
	return rows.Scan(
		&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
		&m.ContentMasked, &m.Status, &m.ModelID,
		&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
		&m.CreatedAt, &m.ParentID, &m.BranchIndex, &m.IsActive,
		&m.ContentCanonical, &m.CanonicalStatus,
		&m.CanonicalSchemaVersion, &m.CanonicalGenerationID,
		&m.CanonicalSHA256, &m.CanonicalLength,
	)
}

// MessageSearchHit is a single row from SearchUserMessages —
// JOINs the matched message row with its conversation's title so
// the search service can render results without a second lookup.
//
// Rank is ts_rank() against the matched query; the search service
// thresholds it to label results as "high" / "medium" relevance.
type MessageSearchHit struct {
	MessageID         uuid.UUID
	ConversationID    uuid.UUID
	ConversationTitle string
	Role              string
	Content           string
	CreatedAt         time.Time
	Rank              float64
	// Phase 4A.2.4.1 — display-lifecycle classification, loaded ONLY when the
	// feature is enabled (H4 deploy-order). The search SERVICE uses these to
	// emit a non-raw representation (legacy → redacted, canonical_safe →
	// tokenized) so a legacy ``content_masked`` raw value never reaches the
	// search index or the LLM ``search_past_conversations`` tool. ``Content``
	// is the raw ``content_masked`` and MUST NOT be surfaced directly when
	// display lifecycle is on.
	DisplayState     string
	ContentCanonical *string
	CanonicalStatus  string
}

// SearchUserMessages runs a tsvector full-text search across every
// active message in conversations owned by (userID, tenantID).
// JOINs through chat.conversations so the ownership scope is
// enforced server-side — a leaked message id from another tenant
// can never surface here.
//
// Only user / assistant messages are indexed (see migration 016's
// trigger); the WHERE clause restates the role filter as defence
// in depth in case a backfill pass somehow indexed a system row.
//
// Limit defaults to 10, capped at 50 — keeps the formatted tool
// result inside a usable prompt budget.
func (r *MessageRepo) SearchUserMessages(
	ctx context.Context, userID, tenantID uuid.UUID, query string, limit int,
) ([]MessageSearchHit, error) {
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
	// Phase 4A.2.4.1 (H4-style): the display-lifecycle columns are selected
	// ONLY when the feature is enabled, so a pre-migration-025 DB never
	// references a missing column. The search SERVICE then derives a non-raw
	// representation from them.
	extraCols := ""
	if r.displayLifecycle {
		extraCols = `, m.display_state, m.content_canonical, m.canonical_status`
	}
	// Hybrid engine (migration 028): unsegmented-script queries (Thai,
	// CJK, …) can't match tsvector word tokens, so they route to a
	// trigram-indexed substring match ranked by similarity(). Spaced
	// languages keep the original ts_rank branch unchanged.
	rankExpr := `ts_rank(m.search_vector, plainto_tsquery('simple', $3))::float8`
	matchExpr := `m.search_vector @@ plainto_tsquery('simple', $3)`
	args := []any{userID, tenantID, q, limit}
	if NeedsTrigramSearch(q) {
		rankExpr = `similarity(m.content_masked, $3)::float8`
		matchExpr = `m.content_masked ILIKE $5`
		args = append(args, TrigramPattern(q))
	}
	sql := `
		SELECT
			m.id,
			m.conversation_id,
			c.title,
			m.role,
			m.content_masked,
			m.created_at,
			` + rankExpr + ` AS rank` + extraCols + `
		FROM chat.messages_masked m
		JOIN chat.conversations c ON c.id = m.conversation_id
		WHERE c.user_id = $1
		  AND c.tenant_id = $2
		  AND c.is_deleted = false
		  AND m.is_active = true
		  AND m.role IN ('user', 'assistant')
		  AND ` + matchExpr + `
		ORDER BY rank DESC, m.created_at DESC
		LIMIT $4
	`
	rows, err := r.db.Query(ctx, sql, args...)
	if err != nil {
		return nil, fmt.Errorf("search user messages: %w", err)
	}
	defer rows.Close()
	out := make([]MessageSearchHit, 0, limit)
	for rows.Next() {
		var hit MessageSearchHit
		dest := []any{
			&hit.MessageID, &hit.ConversationID, &hit.ConversationTitle,
			&hit.Role, &hit.Content, &hit.CreatedAt, &hit.Rank,
		}
		if r.displayLifecycle {
			dest = append(dest, &hit.DisplayState, &hit.ContentCanonical, &hit.CanonicalStatus)
		}
		if err := rows.Scan(dest...); err != nil {
			return nil, fmt.Errorf("search user messages scan: %w", err)
		}
		out = append(out, hit)
	}
	return out, rows.Err()
}

// CountMessages returns the active message count for one
// conversation. Tenant-scoped. Used by the S3 auto-extraction
// trigger to decide whether enough new messages have accumulated
// since the last summary to warrant another extraction pass.
// Cheaper than ListByConversation when we only need the number.
// CountMessagesBefore (FIX33.1) — active messages created strictly before
// `before`; the regenerate path's OmittedOlder denominator.
func (r *MessageRepo) CountMessagesBefore(
	ctx context.Context, tenantID, convID uuid.UUID, before time.Time,
) (int, error) {
	const q = `
		SELECT COUNT(*)::int
		FROM chat.messages_masked
		WHERE conversation_id = $1 AND tenant_id = $2 AND is_active = true AND created_at < $3
	`
	var n int
	if err := r.db.QueryRow(ctx, q, convID, tenantID, before).Scan(&n); err != nil {
		return 0, fmt.Errorf("count messages before: %w", err)
	}
	return n, nil
}

// CountAfterUpTo — FIX117: the denominator of compaction progress. Counts the
// active messages that sit after afterMessageID (exclusive; uuid.Nil = from
// the start) up to and including uptoID (nil = everything after the anchor),
// in the same (created_at, id) order ListAfter folds them. An uptoID that
// names no row is an error, never a silent zero: the caller reports "unknown"
// rather than a made-up total.
func (r *MessageRepo) CountAfterUpTo(
	ctx context.Context, tenantID, convID, afterMessageID uuid.UUID, uptoID *uuid.UUID,
) (int, error) {
	var upto any
	if uptoID != nil {
		upto = *uptoID
	}
	const q = `
		WITH anchor AS (
			SELECT created_at FROM chat.messages_masked WHERE id = $3 AND tenant_id = $2
		), upto AS (
			SELECT created_at, id FROM chat.messages_masked WHERE id = $4::uuid AND tenant_id = $2 AND conversation_id = $1
		)
		SELECT
			(SELECT COUNT(*)::int FROM chat.messages_masked m
			 WHERE m.conversation_id = $1 AND m.tenant_id = $2 AND m.is_active = true
			   AND m.created_at > COALESCE((SELECT created_at FROM anchor), 'epoch'::timestamptz)
			   AND ($4::uuid IS NULL OR (m.created_at, m.id) <= (SELECT created_at, id FROM upto))),
			(SELECT COUNT(*)::int FROM upto)
	`
	var n, found int
	if err := r.db.QueryRow(ctx, q, convID, tenantID, afterMessageID, upto).Scan(&n, &found); err != nil {
		return 0, fmt.Errorf("count messages after up to: %w", err)
	}
	if uptoID != nil && found == 0 {
		return 0, fmt.Errorf("count messages after up to: message %s not in conversation", uptoID.String())
	}
	return n, nil
}

func (r *MessageRepo) CountMessages(
	ctx context.Context, tenantID, convID uuid.UUID,
) (int, error) {
	const q = `
		SELECT COUNT(*)::int
		FROM chat.messages_masked
		WHERE conversation_id = $1 AND tenant_id = $2 AND is_active = true
	`
	var n int
	if err := r.db.QueryRow(ctx, q, convID, tenantID).Scan(&n); err != nil {
		return 0, fmt.Errorf("count messages: %w", err)
	}
	return n, nil
}

// ListAfter returns active messages with created_at strictly AFTER
// the given anchor message's timestamp, in chronological order.
// Used by the S3 state extractor to feed only the new tail of the
// conversation into the LLM extraction prompt.
//
// Anchor lookup uses the message's own created_at — we don't rely
// on monotonic id ordering because UUIDv4s don't sort by time.
// Limit defaults to 200 (same cap as ListContextByConversation).
func (r *MessageRepo) ListAfter(
	ctx context.Context, tenantID, convID, afterMessageID uuid.UUID, limit int,
) ([]model.Message, error) {
	if limit <= 0 {
		limit = 200
	}
	if limit > 500 {
		limit = 500
	}
	const q = `
		WITH anchor AS (
			SELECT created_at FROM chat.messages_masked
			WHERE id = $3 AND tenant_id = $2
		)
		SELECT id, conversation_id, tenant_id, user_id, role, content_masked,
		       status, model_id, token_usage_input, token_usage_output,
		       secure_trace_id, created_at, parent_id, branch_index, is_active
		FROM chat.messages_masked
		WHERE conversation_id = $1
		  AND tenant_id = $2
		  AND is_active = true
		  AND created_at > COALESCE((SELECT created_at FROM anchor), 'epoch'::timestamptz)
		ORDER BY created_at ASC, id ASC
		LIMIT $4
	`
	rows, err := r.db.Query(ctx, q, convID, tenantID, afterMessageID, limit)
	if err != nil {
		return nil, fmt.Errorf("list messages after: %w", err)
	}
	defer rows.Close()
	out := make([]model.Message, 0, limit)
	for rows.Next() {
		var m model.Message
		if err := rows.Scan(
			&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
			&m.ContentMasked, &m.Status, &m.ModelID,
			&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
			&m.CreatedAt, &m.ParentID, &m.BranchIndex, &m.IsActive,
		); err != nil {
			return nil, fmt.Errorf("list messages after scan: %w", err)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// GetByID returns a single message scoped by tenant. Used by the
// regenerate flow to look up the target assistant row.
func (r *MessageRepo) GetByID(ctx context.Context, tenantID, msgID uuid.UUID) (*model.Message, error) {
	const q = `
		SELECT id, conversation_id, tenant_id, user_id, role, content_masked,
		       status, model_id, token_usage_input, token_usage_output,
		       secure_trace_id, created_at, parent_id, branch_index, is_active
		FROM chat.messages_masked
		WHERE id = $1 AND tenant_id = $2
	`
	m := &model.Message{}
	err := r.db.QueryRow(ctx, q, msgID, tenantID).Scan(
		&m.ID, &m.ConversationID, &m.TenantID, &m.UserID, &m.Role,
		&m.ContentMasked, &m.Status, &m.ModelID,
		&m.TokenUsageInput, &m.TokenUsageOutput, &m.SecureTraceID,
		&m.CreatedAt, &m.ParentID, &m.BranchIndex, &m.IsActive,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get message by id: %w", err)
	}
	return m, nil
}

// UpdateStatus flips a single message's status. Used by regenerate
// (status=regenerated on the superseded assistant row). The status
// column is CHECK-constrained at the DB level — invalid values
// raise a 23514 violation that the caller surfaces as a 500.
// SetModelID overwrites the model an assistant row is attributed to
// (FIX35 Part B — the model that actually answered, not the "auto"
// sentinel the conversation is set to).
func (r *MessageRepo) SetModelID(ctx context.Context, msgID uuid.UUID, modelID string) error {
	const q = `UPDATE chat.messages_masked SET model_id = $2 WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, msgID, modelID); err != nil {
		return fmt.Errorf("set message model_id: %w", err)
	}
	return nil
}

// SetFallbackFrom records the model a rescued turn asked for (FIX120,
// migration 046). "" clears it.
func (r *MessageRepo) SetFallbackFrom(ctx context.Context, msgID uuid.UUID, from string) error {
	var v *string
	if from != "" {
		v = &from
	}
	const q = `UPDATE chat.messages_masked SET fallback_from = $2 WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, msgID, v); err != nil {
		return fmt.Errorf("set message fallback_from: %w", err)
	}
	return nil
}

func (r *MessageRepo) UpdateStatus(ctx context.Context, msgID uuid.UUID, status string) error {
	const q = `UPDATE chat.messages_masked SET status = $2 WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, msgID, status); err != nil {
		return fmt.Errorf("update message status: %w", err)
	}
	return nil
}

// ListContextBeforeMessage returns every message created strictly
// BEFORE “before“, in chronological order, capped at “limit“.
// Used by regenerate to rebuild the gateway context up to (but
// excluding) the assistant message being replaced.
//
// FIX100 Part A — “superseded“ is excluded here, and ONLY superseded.
// Regenerate flips the row it replaces to status=regenerated but leaves
// is_active=true (the row keeps its token usage and timestamps for
// audit, and the transcript still renders it), so every later
// regenerate re-loaded it and the message count climbed one per press:
// measured 25 → 26 → 27 on the owner's conversation e493ca28.
//
// status=regenerated is the one status that means “the user asked for
// this to be replaced and the replacement is right there“ — dropping it
// loses nothing anybody is still reading. It is NOT a licence to filter
// on status generally: measured on the live database, 109 assistant
// rows with status=error and 30 with status=cancelled hold REAL TEXT
// that streamed to the screen before the failure or the stop. Dropping
// those would delete something the user actually saw. Emptiness is the
// safe test for those, and it is applied where the provider payload is
// built (buildGatewayRequestWithImages), not here.
func (r *MessageRepo) ListContextBeforeMessage(
	ctx context.Context,
	tenantID, convID uuid.UUID,
	before time.Time,
	limit int,
) ([]model.Message, error) {
	if limit <= 0 {
		limit = 50
	}
	if limit > MaxContextLoadCap {
		limit = MaxContextLoadCap
	}
	q := `
		SELECT ` + r.messageContextColumns() + `
		FROM chat.messages_masked
		WHERE conversation_id = $1 AND tenant_id = $2 AND created_at < $3
		  AND is_active = true
		  AND status <> 'regenerated'
		ORDER BY created_at DESC
		LIMIT $4
	`
	rows, err := r.db.Query(ctx, q, convID, tenantID, before, limit)
	if err != nil {
		return nil, fmt.Errorf("list context before message: %w", err)
	}
	defer rows.Close()

	out := make([]model.Message, 0, limit)
	for rows.Next() {
		var m model.Message
		if err := r.scanContextRow(rows, &m); err != nil {
			return nil, fmt.Errorf("context before scan: %w", err)
		}
		out = append(out, m)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, nil
}

// ─── Branching helpers ───────────────────────────────────────

// SetParent stamps parent_id on a message. Used during edit to
// promote a never-edited original message to a fork-root.
func (r *MessageRepo) SetParent(ctx context.Context, msgID, parentID uuid.UUID) error {
	const q = `UPDATE chat.messages_masked SET parent_id = $2 WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, msgID, parentID); err != nil {
		return fmt.Errorf("set parent: %w", err)
	}
	return nil
}

// DeactivateChain marks every message in a conversation that lies
// at-or-after “from“ (by created_at) inactive. Used by the edit
// flow to kill the tail of the current branch before splicing in
// the new one.
func (r *MessageRepo) DeactivateChain(
	ctx context.Context, tenantID, convID uuid.UUID, from time.Time,
) error {
	const q = `
		UPDATE chat.messages_masked
		SET is_active = false
		WHERE tenant_id = $1
		  AND conversation_id = $2
		  AND created_at >= $3
		  AND is_active = true
	`
	if _, err := r.db.Exec(ctx, q, tenantID, convID, from); err != nil {
		return fmt.Errorf("deactivate chain: %w", err)
	}
	return nil
}

// StampForkLineage backfills parent_id / branch_index on every
// message in the conversation at-or-after the fork-root's
// timestamp. Called once per fork-group (the first time a message
// is edited) so subsequent messages can be identified as belonging
// to "branch 0" of the new fork group.
func (r *MessageRepo) StampForkLineage(
	ctx context.Context,
	tenantID, convID, forkRoot uuid.UUID,
	rootTs time.Time,
) error {
	const q = `
		UPDATE chat.messages_masked
		SET parent_id = $1
		WHERE tenant_id = $2
		  AND conversation_id = $3
		  AND created_at >= $4
		  AND parent_id IS NULL
	`
	if _, err := r.db.Exec(ctx, q, forkRoot, tenantID, convID, rootTs); err != nil {
		return fmt.Errorf("stamp fork lineage: %w", err)
	}
	return nil
}

// MaxBranchIndex returns the highest branch_index currently in use
// for a given fork-root id. Used by the edit handler to compute the
// next available index.
func (r *MessageRepo) MaxBranchIndex(
	ctx context.Context, tenantID, parentID uuid.UUID,
) (int, error) {
	const q = `
		SELECT COALESCE(MAX(branch_index), 0)
		FROM chat.messages_masked
		WHERE tenant_id = $1 AND parent_id = $2
	`
	var n int
	if err := r.db.QueryRow(ctx, q, tenantID, parentID).Scan(&n); err != nil {
		return 0, fmt.Errorf("max branch index: %w", err)
	}
	return n, nil
}

// SetActiveBranch flips the active branch flags for a fork group.
// Messages on the target branch become active; every other branch
// in the group becomes inactive. One UPDATE so the toggle is
// atomic from a reader's point of view.
func (r *MessageRepo) SetActiveBranch(
	ctx context.Context, tenantID, parentID uuid.UUID, branchIndex int,
) error {
	const q = `
		UPDATE chat.messages_masked
		SET is_active = (branch_index = $3)
		WHERE tenant_id = $1 AND parent_id = $2
	`
	if _, err := r.db.Exec(ctx, q, tenantID, parentID, branchIndex); err != nil {
		return fmt.Errorf("set active branch: %w", err)
	}
	return nil
}

// BranchCountByParent returns the count of distinct branch_index
// values per parent_id. Used by the list endpoint to populate the
// per-message branch_count field cheaply (one query for N
// messages).
func (r *MessageRepo) BranchCountByParent(
	ctx context.Context, tenantID uuid.UUID, parentIDs []uuid.UUID,
) (map[uuid.UUID]int, error) {
	out := make(map[uuid.UUID]int)
	if len(parentIDs) == 0 {
		return out, nil
	}
	const q = `
		SELECT parent_id, COUNT(DISTINCT branch_index)
		FROM chat.messages_masked
		WHERE tenant_id = $1 AND parent_id = ANY($2)
		GROUP BY parent_id
	`
	rows, err := r.db.Query(ctx, q, tenantID, parentIDs)
	if err != nil {
		return nil, fmt.Errorf("branch counts: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var pid uuid.UUID
		var n int
		if err := rows.Scan(&pid, &n); err != nil {
			return nil, err
		}
		out[pid] = n
	}
	return out, rows.Err()
}

// SetReasoningContent writes the per-message thinking summary
// captured during a streaming assistant turn (T4 — migration 019).
// Separate from UpdateContent so the existing 6 callsites don't
// need to fan out a new parameter — success-path finish closures
// call this BEFORE UpdateContent when “reasoning != ""“ and
// skip it otherwise. Empty / nil reasoning never reaches the DB.
//
// Idempotent: calling with the same text overwrites the same
// value; calling with an empty string leaves the column NULL
// (the caller is expected to skip the call instead).
func (r *MessageRepo) SetReasoningContent(
	ctx context.Context, msgID uuid.UUID, reasoning string,
) error {
	if reasoning == "" {
		return nil
	}
	const q = `UPDATE chat.messages_masked SET reasoning_content = $2 WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, msgID, reasoning); err != nil {
		return fmt.Errorf("set reasoning_content: %w", err)
	}
	return nil
}

// SetSearchSources persists the web_search citations on an assistant
// message (migration 023). “raw“ is a JSON array of source objects;
// nil/empty is a no-op (non-search turns leave the column NULL).
func (r *MessageRepo) SetSearchSources(
	ctx context.Context, msgID uuid.UUID, raw json.RawMessage,
) error {
	if len(raw) == 0 {
		return nil
	}
	const q = `UPDATE chat.messages_masked SET search_sources = $2 WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, msgID, []byte(raw)); err != nil {
		return fmt.Errorf("set search_sources: %w", err)
	}
	return nil
}

// GetSearchSources reads the persisted web_search citations for one
// message (tenant-scoped). Returns nil when the message has none.
func (r *MessageRepo) GetSearchSources(
	ctx context.Context, tenantID, msgID uuid.UUID,
) (json.RawMessage, error) {
	const q = `SELECT search_sources FROM chat.messages_masked
	           WHERE id = $1 AND tenant_id = $2`
	var raw json.RawMessage
	if err := r.db.QueryRow(ctx, q, msgID, tenantID).Scan(&raw); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		return nil, fmt.Errorf("get search_sources: %w", err)
	}
	return raw, nil
}

// UpdateContent overwrites the content + status + token usage of
// an existing row. Used by the streaming path to finalize the
// in-flight assistant message after the stream completes (it was
// inserted with status="streaming" + empty content for the
// streaming-start log; on completion we replace those fields with
// the accumulated content and final usage).
func (r *MessageRepo) UpdateContent(
	ctx context.Context,
	msgID uuid.UUID,
	content, status string,
	tokenInput, tokenOutput *int,
) error {
	// FIX125 M2 — never into a deleted conversation (liveConversationGuard).
	const q = `
		UPDATE chat.messages_masked AS m
		SET content_masked = $2, status = $3,
		    token_usage_input = $4, token_usage_output = $5
		WHERE m.id = $1` + liveConversationGuard
	if _, err := r.db.Exec(ctx, q, msgID, content, status, tokenInput, tokenOutput); err != nil {
		return fmt.Errorf("update message content: %w", err)
	}
	return nil
}

// liveConversationGuard (FIX125 M2) is appended to the WHERE clause of the
// finish-time content writes (UPDATE chat.messages_masked AS m ...). A
// conversation deleted while its answer was still streaming must not have
// that answer written into it afterwards: the delete is the person's, and
// the row is on the erasure queue (TranscriptRetentionService). The
// service's finish callbacks check first; this closes the window between
// that check and the write. Zero rows affected is not an error — there is
// nothing to write to.
const liveConversationGuard = `
		  AND NOT EXISTS (
		      SELECT 1 FROM chat.conversations c
		      WHERE c.id = m.conversation_id AND c.is_deleted
		  )`

// MarkInterrupted settles a row that a process restart orphaned
// mid-generation (Phase 5 stream resume). Guarded: only flips a row
// that is still in a non-terminal state, so it can never clobber a
// completed/cancelled/error status written by a live finish callback.
//
// FIX118: the row also says WHY — terminal_cause 'interrupted' and the
// resume code the replay ends on — so a reload distinguishes "the process
// died" from a provider error.
func (r *MessageRepo) MarkInterrupted(ctx context.Context, msgID uuid.UUID) error {
	const q = `
		UPDATE chat.messages_masked
		SET status = 'error', terminal_cause = 'interrupted', error_code = 'GENERATION_INTERRUPTED'
		WHERE id = $1 AND status IN ('streaming', 'pending')
	`
	if _, err := r.db.Exec(ctx, q, msgID); err != nil {
		return fmt.Errorf("mark interrupted: %w", err)
	}
	return nil
}

// SetTerminal records how a turn ended (FIX118, migration 043): the
// closed-set cause and, for a failed turn, the catalogue error code. An
// empty cause writes NULL (a parked turn has not ended); an empty code
// writes NULL (the turn did not fail). Called by every finish callback
// after the content/status write, and by the pre-stream failure paths.
//
// FIX119: stopReason (why the MODEL stopped, closed set) rides along; ""
// writes NULL (a failed run, a user row, a provider that said nothing and
// a caller that did not ask are all NULL — `unknown` is written only when
// the gateway itself said unknown).
func (r *MessageRepo) SetTerminal(ctx context.Context, msgID uuid.UUID, cause, errorCode, stopReason string) error {
	var c, e, sr *string
	if cause != "" {
		c = &cause
	}
	if errorCode != "" {
		e = &errorCode
	}
	if stopReason != "" {
		sr = &stopReason
	}
	const q = `UPDATE chat.messages_masked SET terminal_cause = $2, error_code = $3, stop_reason = $4 WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, msgID, c, e, sr); err != nil {
		return fmt.Errorf("set message terminal: %w", err)
	}
	return nil
}

// SetAttempts records a failed turn's per-model attempt log (FIX121,
// migration 047). raw is already bounded by service.NormalizeAttempts.
func (r *MessageRepo) SetAttempts(ctx context.Context, msgID uuid.UUID, raw json.RawMessage) error {
	const q = `UPDATE chat.messages_masked SET attempts = $2::jsonb WHERE id = $1`
	if _, err := r.db.Exec(ctx, q, msgID, jsonOrNull(raw)); err != nil {
		return fmt.Errorf("set message attempts: %w", err)
	}
	return nil
}

// jsonOrNull hands a JSON value to a ``$n::jsonb`` parameter as text, and an
// empty one as SQL NULL (never the JSON literal null).
func jsonOrNull(raw json.RawMessage) any {
	if len(raw) == 0 {
		return nil
	}
	return string(raw)
}

// CanonicalWrite carries the canonical artifact metadata persisted
// alongside the display content (Phase 4A.2.2). For a non-“complete“
// status, “Content“ and the integrity fields are nil — provider history
// will EXCLUDE the row (never a display fallback). “ErrorCode“ is a
// stable safe code only (never raw text).
type CanonicalWrite struct {
	Status        string
	Content       *string
	SchemaVersion *int
	GenerationID  *string
	SHA256        *string
	Length        *int
	ErrorCode     *string
	// DisplayState (Phase 4A.2.4) classifies what content_masked holds. Only
	// written when the repo's displayLifecycle flag is on. Empty → treated as
	// legacy_display.
	DisplayState string
}

// UpdateContentAndCanonical persists the restored DISPLAY content and the
// CANONICAL artifact metadata in a SINGLE atomic UPDATE, so a row can
// never end up “canonical_status='complete'“ with a missing canonical.
// Used by the streaming + non-stream consumers when canonical history is
// enabled.
func (r *MessageRepo) UpdateContentAndCanonical(
	ctx context.Context,
	msgID uuid.UUID,
	content, status string,
	tokenInput, tokenOutput *int,
	canon CanonicalWrite,
) error {
	set := `content_masked = $2, status = $3,
		    token_usage_input = $4, token_usage_output = $5,
		    content_canonical = $6, canonical_status = $7,
		    canonical_schema_version = $8, canonical_generation_id = $9,
		    canonical_sha256 = $10, canonical_length = $11,
		    canonical_error_code = $12, canonical_received_at = now()`
	args := []any{
		msgID, content, status, tokenInput, tokenOutput,
		canon.Content, canon.Status, canon.SchemaVersion, canon.GenerationID,
		canon.SHA256, canon.Length, canon.ErrorCode,
	}
	if r.displayLifecycle {
		ds := canon.DisplayState
		if ds == "" {
			ds = "legacy_display"
		}
		set += `, display_state = $13`
		args = append(args, ds)
	}
	q := `UPDATE chat.messages_masked AS m SET ` + set + ` WHERE m.id = $1` + liveConversationGuard
	if _, err := r.db.Exec(ctx, q, args...); err != nil {
		return fmt.Errorf("update message content+canonical: %w", err)
	}
	return nil
}

// UpdateContentInvalidateCanonical writes display content AND atomically
// INVALIDATES any prior canonical artifact (H3): NULLs the canonical
// content + integrity fields and sets a non-complete status. Used by every
// display-only finish/update path (error/cancel/edit/image/non-stream/
// fallback) when canonical history is enabled, so a stale “complete“
// canonical can never survive a display change and be replayed to the
// provider. “errCode“ is a stable safe code (never raw text).
func (r *MessageRepo) UpdateContentInvalidateCanonical(
	ctx context.Context,
	msgID uuid.UUID,
	content, status, canonicalStatus, errCode, displayState string,
	tokenInput, tokenOutput *int,
) error {
	set := `content_masked = $2, status = $3,
		    token_usage_input = $4, token_usage_output = $5,
		    content_canonical = NULL, canonical_status = $6,
		    canonical_schema_version = NULL, canonical_generation_id = NULL,
		    canonical_sha256 = NULL, canonical_length = NULL,
		    canonical_error_code = $7, canonical_received_at = now()`
	args := []any{
		msgID, content, status, tokenInput, tokenOutput,
		canonicalStatus, errCode,
	}
	if r.displayLifecycle {
		ds := displayState
		if ds == "" {
			ds = "legacy_display"
		}
		set += `, display_state = $8`
		args = append(args, ds)
	}
	q := `UPDATE chat.messages_masked AS m SET ` + set + ` WHERE m.id = $1` + liveConversationGuard
	if _, err := r.db.Exec(ctx, q, args...); err != nil {
		return fmt.Errorf("update message content (invalidate canonical): %w", err)
	}
	return nil
}

// TitleSeed is what naming a conversation needs, read from the HEAD of the
// conversation (ListContextByConversation reads its tail): the first user
// message — the placeholder title is its first words — and the first turn
// that was ANSWERED, which may not be the first turn at all.
type TitleSeed struct {
	// FirstUser is the conversation's first active user message; nil when
	// there is none.
	FirstUser *model.Message
	// Answer is the first active assistant row that completed with text;
	// nil while no turn has succeeded. Question is the user message it
	// answered.
	Answer   *model.Message
	Question *model.Message
}

// GetTitleSeed (FIX124) loads the TitleSeed. Only the columns a title needs.
func (r *MessageRepo) GetTitleSeed(ctx context.Context, tenantID, convID uuid.UUID) (*TitleSeed, error) {
	const cols = `id, role, content_masked, status, model_id, fallback_from, created_at`
	one := func(where, order string, args ...any) (*model.Message, error) {
		q := `SELECT ` + cols + ` FROM chat.messages_masked
		      WHERE conversation_id = $1 AND tenant_id = $2 AND is_active = true
		        AND status <> 'regenerated' AND ` + where + `
		      ORDER BY created_at ` + order + ` LIMIT 1`
		var m model.Message
		err := r.db.QueryRow(ctx, q, append([]any{convID, tenantID}, args...)...).
			Scan(&m.ID, &m.Role, &m.ContentMasked, &m.Status, &m.ModelID, &m.FallbackFrom, &m.CreatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		if err != nil {
			return nil, fmt.Errorf("title seed: %w", err)
		}
		return &m, nil
	}
	seed := &TitleSeed{}
	var err error
	if seed.FirstUser, err = one(`role = 'user'`, `ASC`); err != nil || seed.FirstUser == nil {
		return seed, err
	}
	if seed.Answer, err = one(`role = 'assistant' AND status = 'completed' AND btrim(content_masked) <> ''`, `ASC`); err != nil || seed.Answer == nil {
		return seed, err
	}
	if seed.Question, err = one(`role = 'user' AND created_at <= $3`, `DESC`, seed.Answer.CreatedAt); err != nil {
		return seed, err
	}
	if seed.Question == nil {
		seed.Question = seed.FirstUser
	}
	return seed, nil
}

// FirstUserText (FIX124) is the text of the conversation's first active user
// message — what the placeholder title is cut from. "" when there is none.
func (r *MessageRepo) FirstUserText(ctx context.Context, tenantID, convID uuid.UUID) (string, error) {
	const q = `SELECT content_masked FROM chat.messages_masked
	           WHERE conversation_id = $1 AND tenant_id = $2 AND is_active = true AND role = 'user'
	           ORDER BY created_at ASC LIMIT 1`
	var text string
	err := r.db.QueryRow(ctx, q, convID, tenantID).Scan(&text)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("first user text: %w", err)
	}
	return text, nil
}

// MarkRegeneratedByClientID (FIX124) retires the assistant row an imported
// answer replaces: status=regenerated, the value the web's Regenerate writes
// and the one every context read already leaves out. Scoped to the
// conversation and to assistant rows; an id that names nothing is a no-op.
func (r *MessageRepo) MarkRegeneratedByClientID(ctx context.Context, tenantID, convID uuid.UUID, clientID string) (bool, error) {
	const q = `UPDATE chat.messages_masked SET status = 'regenerated'
	           WHERE conversation_id = $1 AND tenant_id = $2 AND client_id = $3
	             AND role = 'assistant' AND status <> 'regenerated'`
	tag, err := r.db.Exec(ctx, q, convID, tenantID, clientID)
	if err != nil {
		return false, fmt.Errorf("mark regenerated by client id: %w", err)
	}
	return tag.RowsAffected() > 0, nil
}
