// MemoryRepo persists chat.user_memories + chat.memory_events.
//
// Every method that returns or mutates memory data filters by
// (user_id, tenant_id) — no exceptions. The repo is the single
// place SQL touches the memory tables; the service layer wraps it
// with DLP / dedup / audit-log calls.
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
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type MemoryRepo struct {
	db DB
}

func NewMemoryRepo(db *pgxpool.Pool) *MemoryRepo {
	return &MemoryRepo{db: db}
}

func (r *MemoryRepo) WithTx(tx pgx.Tx) *MemoryRepo {
	return &MemoryRepo{db: tx}
}

// memoryColumns is the single source of truth for SELECT projections.
// Mirrors the model.Memory field order so scanMemory can stay a
// straightforward unpack. Nullable text columns (category,
// normalized_key, evidence_excerpt) COALESCE to ” so the scan
// targets stay plain string instead of *string — the model exposes
// these as JSON omitempty so the wire shape doesn't change.
const memoryColumns = `
	id, tenant_id, user_id,
	scope_type, scope_id,
	memory_type, COALESCE(category, ''),
	content, COALESCE(normalized_key, ''),
	source_type, source_conversation_id, source_message_id, COALESCE(evidence_excerpt, ''),
	status,
	confidence_score::float8, importance_score::float8, is_user_confirmed, is_pinned,
	sensitivity_level,
	last_used_at, use_count,
	valid_until,
	created_at, updated_at
`

func scanMemory(row pgx.Row, m *model.Memory) error {
	return row.Scan(
		&m.ID, &m.TenantID, &m.UserID,
		&m.ScopeType, &m.ScopeID,
		&m.MemoryType, &m.Category,
		&m.Content, &m.NormalizedKey,
		&m.SourceType, &m.SourceConversationID, &m.SourceMessageID, &m.EvidenceExcerpt,
		&m.Status,
		&m.ConfidenceScore, &m.ImportanceScore, &m.IsUserConfirmed, &m.IsPinned,
		&m.SensitivityLevel,
		&m.LastUsedAt, &m.UseCount,
		&m.ValidUntil,
		&m.CreatedAt, &m.UpdatedAt,
	)
}

// MemoryListFilters is the typed filter set ListByUser accepts.
// Empty string fields skip that predicate.
type MemoryListFilters struct {
	ScopeType  string
	MemoryType string
	Status     string // empty → "active"
	Limit      int    // default 50, max 100
	Offset     int
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

// Create inserts a new memory row. The DB trigger fills search_vector
// + updated_at; the service layer populates NormalizedKey before
// calling this. Returns the post-insert row so the caller gets the
// generated id + timestamps.
func (r *MemoryRepo) Create(ctx context.Context, m *model.Memory) (*model.Memory, error) {
	const q = `
		INSERT INTO chat.user_memories (
			tenant_id, user_id,
			scope_type, scope_id,
			memory_type, category,
			content, normalized_key,
			source_type, source_conversation_id, source_message_id, evidence_excerpt,
			status,
			confidence_score, importance_score, is_user_confirmed, is_pinned,
			sensitivity_level,
			valid_until
		)
		VALUES (
			$1, $2,
			$3, $4,
			$5, $6,
			$7, $8,
			$9, $10, $11, $12,
			$13,
			$14, $15, $16, $17,
			$18,
			$19
		)
		RETURNING ` + memoryColumns
	if m.Status == "" {
		m.Status = model.MemoryStatusActive
	}
	if m.SensitivityLevel == "" {
		m.SensitivityLevel = model.MemorySensitivityNormal
	}
	if m.ScopeType == "" {
		m.ScopeType = model.MemoryScopeGlobalUser
	}
	out := &model.Memory{}
	err := scanMemory(r.db.QueryRow(ctx, q,
		m.TenantID, m.UserID,
		m.ScopeType, m.ScopeID,
		m.MemoryType, nullableText(m.Category),
		m.Content, nullableText(m.NormalizedKey),
		m.SourceType, m.SourceConversationID, m.SourceMessageID, nullableText(m.EvidenceExcerpt),
		m.Status,
		m.ConfidenceScore, m.ImportanceScore, m.IsUserConfirmed, m.IsPinned,
		m.SensitivityLevel,
		m.ValidUntil,
	), out)
	if err != nil {
		return nil, fmt.Errorf("create memory: %w", err)
	}
	return out, nil
}

// GetByID returns a single memory row. Hard-scoped to (user_id,
// tenant_id) so a leaked id from another user is indistinguishable
// from a missing row.
func (r *MemoryRepo) GetByID(
	ctx context.Context, id, userID, tenantID uuid.UUID,
) (*model.Memory, error) {
	q := `SELECT ` + memoryColumns + `
	      FROM chat.user_memories
	      WHERE id = $1 AND user_id = $2 AND tenant_id = $3`
	m := &model.Memory{}
	if err := scanMemory(r.db.QueryRow(ctx, q, id, userID, tenantID), m); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get memory: %w", err)
	}
	return m, nil
}

// ---------------------------------------------------------------------------
// Update / Pin / Confirm / Delete
// ---------------------------------------------------------------------------

// Update writes the mutable fields. Leaves status, source, audit
// fields, and use_count alone — those mutate through their dedicated
// methods. Returns the post-update row.
func (r *MemoryRepo) Update(ctx context.Context, m *model.Memory) error {
	const q = `
		UPDATE chat.user_memories
		SET content          = $4,
		    normalized_key   = $5,
		    category         = $6,
		    memory_type      = $7,
		    importance_score = $8,
		    confidence_score = $9,
		    sensitivity_level= $10,
		    valid_until      = $11
		WHERE id = $1 AND user_id = $2 AND tenant_id = $3
	`
	tag, err := r.db.Exec(ctx, q,
		m.ID, m.UserID, m.TenantID,
		m.Content, nullableText(m.NormalizedKey), nullableText(m.Category),
		m.MemoryType, m.ImportanceScore, m.ConfidenceScore,
		m.SensitivityLevel, m.ValidUntil,
	)
	if err != nil {
		return fmt.Errorf("update memory: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// SoftDelete marks a memory as deleted AND scrubs the content +
// search vector + evidence so the row no longer carries the user's
// data. The audit-log row still records the action so support can
// confirm a deletion happened, but the memory text itself is gone.
func (r *MemoryRepo) SoftDelete(
	ctx context.Context, id, userID, tenantID uuid.UUID,
) error {
	const q = `
		UPDATE chat.user_memories
		SET status           = 'deleted',
		    content          = '[deleted]',
		    normalized_key   = NULL,
		    evidence_excerpt = NULL,
		    search_vector    = NULL,
		    updated_at       = NOW()
		WHERE id = $1 AND user_id = $2 AND tenant_id = $3
	`
	tag, err := r.db.Exec(ctx, q, id, userID, tenantID)
	if err != nil {
		return fmt.Errorf("soft delete memory: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// SetPinned toggles the is_pinned flag. Pinned memories sort to the
// top of the injection block regardless of importance_score.
func (r *MemoryRepo) SetPinned(
	ctx context.Context, id, userID, tenantID uuid.UUID, pinned bool,
) error {
	const q = `
		UPDATE chat.user_memories
		SET is_pinned = $4, updated_at = NOW()
		WHERE id = $1 AND user_id = $2 AND tenant_id = $3
	`
	tag, err := r.db.Exec(ctx, q, id, userID, tenantID, pinned)
	if err != nil {
		return fmt.Errorf("set pinned: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// SetConfirmed flips is_user_confirmed. Phase 4 inferred memories
// land in status='pending_confirmation'; confirming them promotes to
// 'active' so the injection block picks them up.
func (r *MemoryRepo) SetConfirmed(
	ctx context.Context, id, userID, tenantID uuid.UUID, confirmed bool,
) error {
	const q = `
		UPDATE chat.user_memories
		SET is_user_confirmed = $4,
		    status = CASE WHEN $4 AND status = 'pending_confirmation' THEN 'active' ELSE status END,
		    updated_at = NOW()
		WHERE id = $1 AND user_id = $2 AND tenant_id = $3
	`
	tag, err := r.db.Exec(ctx, q, id, userID, tenantID, confirmed)
	if err != nil {
		return fmt.Errorf("set confirmed: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// MemoryAggregateStats is the per-tenant rollup the S9 admin
// dashboard reads. All counts; no content; no per-user breakdown.
type MemoryAggregateStats struct {
	TotalUsersWithMemories int
	TotalActive            int
	TotalPending           int
	TotalDeleted           int
	TotalEvents            int
	AvgMemoriesPerUser     float64
	TopMemoryTypes         map[string]int
}

// GetAggregateStats returns the per-tenant rollup the S9 admin
// /memory-stats endpoint serves. ZERO content / per-user data
// leaves this method — only counts grouped by status + type. The
// memory_event count is taken from the audit table to give the
// admin a sense of activity level.
func (r *MemoryRepo) GetAggregateStats(
	ctx context.Context, tenantID uuid.UUID,
) (*MemoryAggregateStats, error) {
	out := &MemoryAggregateStats{
		TopMemoryTypes: map[string]int{},
	}
	// Counts query — one round trip via FILTER aggregates.
	const countsQ = `
		SELECT
			COUNT(DISTINCT user_id)                                 AS users_with,
			COUNT(*) FILTER (WHERE status = 'active')               AS active,
			COUNT(*) FILTER (WHERE status = 'pending_confirmation') AS pending,
			COUNT(*) FILTER (WHERE status = 'deleted')              AS deleted
		FROM chat.user_memories
		WHERE tenant_id = $1
	`
	if err := r.db.QueryRow(ctx, countsQ, tenantID).Scan(
		&out.TotalUsersWithMemories, &out.TotalActive, &out.TotalPending, &out.TotalDeleted,
	); err != nil {
		return nil, fmt.Errorf("aggregate counts: %w", err)
	}
	if out.TotalUsersWithMemories > 0 {
		out.AvgMemoriesPerUser = float64(out.TotalActive) / float64(out.TotalUsersWithMemories)
	}

	// Top memory types — only active rows; deleted tombstones
	// have their type intact but shouldn't count toward "what's
	// in use" stats.
	const typesQ = `
		SELECT memory_type, COUNT(*)::int AS n
		FROM chat.user_memories
		WHERE tenant_id = $1 AND status = 'active'
		GROUP BY memory_type
		ORDER BY n DESC
	`
	rows, err := r.db.Query(ctx, typesQ, tenantID)
	if err != nil {
		return nil, fmt.Errorf("aggregate types: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var t string
		var n int
		if err := rows.Scan(&t, &n); err != nil {
			return nil, fmt.Errorf("aggregate types scan: %w", err)
		}
		out.TopMemoryTypes[t] = n
	}

	// Event count — separate query so a missing event table won't
	// drag down the rest of the stats. Best-effort: errors leave
	// TotalEvents at 0.
	const eventsQ = `SELECT COUNT(*)::int FROM chat.memory_events WHERE tenant_id = $1`
	_ = r.db.QueryRow(ctx, eventsQ, tenantID).Scan(&out.TotalEvents)
	return out, nil
}

// ListAllForExport returns every memory row for the user — active,
// pending, superseded, and deleted tombstones included so the
// exported file is a complete history. Caller is the GDPR-export
// path; the deleted rows still ship with their scrubbed content
// (set to '[deleted]' / '[expired]' by the soft-delete +
// retention paths).
func (r *MemoryRepo) ListAllForExport(
	ctx context.Context, userID, tenantID uuid.UUID,
) ([]model.Memory, error) {
	q := `SELECT ` + memoryColumns + `
	      FROM chat.user_memories
	      WHERE user_id = $1 AND tenant_id = $2
	      ORDER BY created_at ASC`
	rows, err := r.db.Query(ctx, q, userID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list all for export: %w", err)
	}
	defer rows.Close()
	out := make([]model.Memory, 0)
	for rows.Next() {
		var m model.Memory
		if err := scanMemory(rows, &m); err != nil {
			return nil, fmt.Errorf("list all export scan: %w", err)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// HardDeleteAllByUser is the GDPR right-to-erasure path. Issues a
// genuine DELETE (not the soft-delete that SoftDelete + ClearAll
// use) so no trace of the row content remains. Returns the
// affected row count for the audit log + the response envelope.
func (r *MemoryRepo) HardDeleteAllByUser(
	ctx context.Context, userID, tenantID uuid.UUID,
) (int, error) {
	const q = `DELETE FROM chat.user_memories WHERE user_id = $1 AND tenant_id = $2`
	tag, err := r.db.Exec(ctx, q, userID, tenantID)
	if err != nil {
		return 0, fmt.Errorf("hard delete memories: %w", err)
	}
	return int(tag.RowsAffected()), nil
}

// HardDeleteAllEventsByUser purges the memory-events audit trail
// for one user as part of the GDPR purge cascade. Even the audit
// rows go — the spec's contract is "permanently delete EVERYTHING
// memory-related", and the audit table carries action timestamps
// + actor metadata that could still identify behaviour.
func (r *MemoryRepo) HardDeleteAllEventsByUser(
	ctx context.Context, userID, tenantID uuid.UUID,
) (int, error) {
	const q = `DELETE FROM chat.memory_events WHERE user_id = $1 AND tenant_id = $2`
	tag, err := r.db.Exec(ctx, q, userID, tenantID)
	if err != nil {
		return 0, fmt.Errorf("hard delete memory events: %w", err)
	}
	return int(tag.RowsAffected()), nil
}

// ExpireOlderThan is the RetentionEnforcer's nightly write. Soft-
// deletes (status='deleted') active memories whose created_at is
// older than the cutoff AND scrubs the content + search_vector so
// the data really is gone from indexable form. Pinned memories
// are EXEMPT — if a user pinned it, they want to keep it.
//
// Returns the affected row count for observability.
func (r *MemoryRepo) ExpireOlderThan(
	ctx context.Context, tenantID uuid.UUID, cutoff time.Time,
) (int, error) {
	const q = `
		UPDATE chat.user_memories
		SET status           = 'deleted',
		    content          = '[expired]',
		    normalized_key   = NULL,
		    evidence_excerpt = NULL,
		    search_vector    = NULL,
		    updated_at       = NOW()
		WHERE tenant_id = $1
		  AND status = 'active'
		  AND is_pinned = false
		  AND created_at < $2
	`
	tag, err := r.db.Exec(ctx, q, tenantID, cutoff)
	if err != nil {
		return 0, fmt.Errorf("expire older than: %w", err)
	}
	return int(tag.RowsAffected()), nil
}

// CountByStatus returns the number of rows in the given status
// for the user. Backs the S8 pending-count badge (GET
// /memories/pending-count). Hard-scoped by user_id + tenant_id.
func (r *MemoryRepo) CountByStatus(
	ctx context.Context, userID, tenantID uuid.UUID, status string,
) (int, error) {
	const q = `
		SELECT COUNT(*)::int FROM chat.user_memories
		WHERE user_id = $1 AND tenant_id = $2 AND status = $3
	`
	var n int
	if err := r.db.QueryRow(ctx, q, userID, tenantID, status).Scan(&n); err != nil {
		return 0, fmt.Errorf("count memories by status: %w", err)
	}
	return n, nil
}

// SetStatus updates a memory's status without touching any other
// field. Used by the S8 auto-promotion path (pending → active for
// the newest of a similar set, superseded for the older
// duplicates) and the rare future "demote / re-pend" flow.
//
// Tenant + user scoped — the WHERE clause prevents a leaked id
// from mutating another tenant's row.
func (r *MemoryRepo) SetStatus(
	ctx context.Context, id, userID, tenantID uuid.UUID, status string,
) error {
	const q = `
		UPDATE chat.user_memories
		SET status = $4, updated_at = NOW()
		WHERE id = $1 AND user_id = $2 AND tenant_id = $3
	`
	tag, err := r.db.Exec(ctx, q, id, userID, tenantID, status)
	if err != nil {
		return fmt.Errorf("set status: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// FindSimilarPending returns rows whose normalized_key matches
// the supplied key and whose status is pending OR active. The S8
// auto-promotion path uses the count to decide: 3+ observations
// of the same suggestion across distinct conversations → promote
// the newest to active and supersede the rest.
//
// Filter is restricted to inferred_preference rows so a manual
// active memory matching a fresh inferred suggestion doesn't get
// counted toward the promotion threshold.
func (r *MemoryRepo) FindSimilarPending(
	ctx context.Context, userID, tenantID uuid.UUID, normalizedKey string,
) ([]model.Memory, error) {
	if strings.TrimSpace(normalizedKey) == "" {
		return nil, nil
	}
	q := `SELECT ` + memoryColumns + `
	      FROM chat.user_memories
	      WHERE user_id = $1
	        AND tenant_id = $2
	        AND memory_type = 'inferred_preference'
	        AND status IN ('pending_confirmation', 'active')
	        AND normalized_key = $3
	      ORDER BY created_at DESC`
	rows, err := r.db.Query(ctx, q, userID, tenantID, normalizedKey)
	if err != nil {
		return nil, fmt.Errorf("find similar pending: %w", err)
	}
	defer rows.Close()
	out := make([]model.Memory, 0, 4)
	for rows.Next() {
		var m model.Memory
		if err := scanMemory(rows, &m); err != nil {
			return nil, fmt.Errorf("find similar pending scan: %w", err)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// ExistsByNormalizedKeyOrPending is the dedup gate used by the S8
// extractor. Returns true when an active row OR a pending
// inferred-preference row already carries the same normalised
// content — either way we shouldn't re-create the same suggestion.
//
// Sibling of ExistsByNormalizedKey (which checks active only and
// powers the explicit-save dedup); extractor uses this stricter
// variant so a freshly-extracted pattern isn't re-suggested when
// the user hasn't yet confirmed an identical earlier suggestion.
func (r *MemoryRepo) ExistsByNormalizedKeyOrPending(
	ctx context.Context, userID, tenantID uuid.UUID, normalizedKey string,
) (bool, error) {
	if strings.TrimSpace(normalizedKey) == "" {
		return false, nil
	}
	const q = `
		SELECT EXISTS (
		  SELECT 1
		  FROM chat.user_memories
		  WHERE user_id = $1 AND tenant_id = $2
		    AND normalized_key = $3
		    AND status IN ('active', 'pending_confirmation')
		)
	`
	var exists bool
	if err := r.db.QueryRow(ctx, q, userID, tenantID, normalizedKey).Scan(&exists); err != nil {
		return false, fmt.Errorf("exists by normalized key or pending: %w", err)
	}
	return exists, nil
}

// ExistsByNormalizedKey is the dedup gate. Returns true when an
// active row already carries the same normalised content for this
// user (we don't fire on superseded / deleted rows so re-saving an
// old fact after a delete works).
func (r *MemoryRepo) ExistsByNormalizedKey(
	ctx context.Context, userID, tenantID uuid.UUID, normalizedKey string,
) (bool, error) {
	if strings.TrimSpace(normalizedKey) == "" {
		return false, nil
	}
	const q = `
		SELECT EXISTS (
		  SELECT 1
		  FROM chat.user_memories
		  WHERE user_id = $1 AND tenant_id = $2
		    AND normalized_key = $3
		    AND status = 'active'
		)
	`
	var exists bool
	if err := r.db.QueryRow(ctx, q, userID, tenantID, normalizedKey).Scan(&exists); err != nil {
		return false, fmt.Errorf("exists by normalized key: %w", err)
	}
	return exists, nil
}

// SearchByContent runs a tsvector full-text search across the
// user's active memories. Reuses the GIN index from migration 014.
// Tie-breaks by importance then recency so the most useful matches
// surface first.
//
// Limit defaults to 10, capped at 50. Empty query short-circuits
// to nil — the search service is responsible for sanitising the
// input string before calling.
func (r *MemoryRepo) SearchByContent(
	ctx context.Context, userID, tenantID uuid.UUID, query string, limit int,
) ([]model.Memory, error) {
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
	// Hybrid engine (migration 028) — see MessageRepo.SearchUserMessages.
	rankExpr := `ts_rank(search_vector, plainto_tsquery('simple', $3))`
	matchExpr := `search_vector @@ plainto_tsquery('simple', $3)`
	args := []any{userID, tenantID, q, limit}
	if NeedsTrigramSearch(q) {
		rankExpr = `similarity(content, $3)`
		matchExpr = `content ILIKE $5`
		args = append(args, TrigramPattern(q))
	}
	sql := `SELECT ` + memoryColumns + `
	        FROM chat.user_memories
	        WHERE user_id = $1
	          AND tenant_id = $2
	          AND status = 'active'
	          AND ` + matchExpr + `
	        ORDER BY ` + rankExpr + ` DESC,
	                 importance_score DESC,
	                 updated_at DESC
	        LIMIT $4`
	rows, err := r.db.Query(ctx, sql, args...)
	if err != nil {
		return nil, fmt.Errorf("search memories by content: %w", err)
	}
	defer rows.Close()
	out := make([]model.Memory, 0, limit)
	for rows.Next() {
		var m model.Memory
		if err := scanMemory(rows, &m); err != nil {
			return nil, fmt.Errorf("search memories scan: %w", err)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// IncrementUseCount bumps use_count and stamps last_used_at. Called
// in the background after the injection block ships, so a write
// failure must not block the message turn — caller swallows the
// error and logs.
func (r *MemoryRepo) IncrementUseCount(ctx context.Context, id uuid.UUID) error {
	const q = `
		UPDATE chat.user_memories
		SET use_count = use_count + 1, last_used_at = NOW()
		WHERE id = $1
	`
	if _, err := r.db.Exec(ctx, q, id); err != nil {
		return fmt.Errorf("increment use count: %w", err)
	}
	return nil
}

// ClearAll soft-deletes every active memory matching the optional
// scopeType filter. Returns the count actually cleared so the
// handler can report "deleted N memories". scopeType="" matches all.
func (r *MemoryRepo) ClearAll(
	ctx context.Context, userID, tenantID uuid.UUID, scopeType *string,
) (int, error) {
	// Same scrubbing as SoftDelete — content + search vector + evidence
	// gone, audit row stays via the event log.
	q := `
		UPDATE chat.user_memories
		SET status           = 'deleted',
		    content          = '[deleted]',
		    normalized_key   = NULL,
		    evidence_excerpt = NULL,
		    search_vector    = NULL,
		    updated_at       = NOW()
		WHERE user_id = $1 AND tenant_id = $2
		  AND status = 'active'
	`
	args := []any{userID, tenantID}
	if scopeType != nil && *scopeType != "" {
		q += " AND scope_type = $3"
		args = append(args, *scopeType)
	}
	tag, err := r.db.Exec(ctx, q, args...)
	if err != nil {
		return 0, fmt.Errorf("clear all memories: %w", err)
	}
	return int(tag.RowsAffected()), nil
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

// ListByUser returns the user's memories, ordered for the
// settings/management surface (newest first). Filters skip empty
// fields. Limit defaults to 50, capped at 100.
func (r *MemoryRepo) ListByUser(
	ctx context.Context, userID, tenantID uuid.UUID, f MemoryListFilters,
) ([]model.Memory, error) {
	limit := f.Limit
	if limit <= 0 {
		limit = 50
	}
	if limit > 100 {
		limit = 100
	}
	offset := f.Offset
	if offset < 0 {
		offset = 0
	}
	status := f.Status
	if status == "" {
		status = model.MemoryStatusActive
	}

	q := `SELECT ` + memoryColumns + `
	      FROM chat.user_memories
	      WHERE user_id = $1 AND tenant_id = $2 AND status = $3`
	args := []any{userID, tenantID, status}
	if f.ScopeType != "" {
		q += fmt.Sprintf(" AND scope_type = $%d", len(args)+1)
		args = append(args, f.ScopeType)
	}
	if f.MemoryType != "" {
		q += fmt.Sprintf(" AND memory_type = $%d", len(args)+1)
		args = append(args, f.MemoryType)
	}
	q += fmt.Sprintf(`
	  ORDER BY is_pinned DESC, importance_score DESC, updated_at DESC
	  LIMIT $%d OFFSET $%d`, len(args)+1, len(args)+2)
	args = append(args, limit, offset)

	rows, err := r.db.Query(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("list memories: %w", err)
	}
	defer rows.Close()
	out := make([]model.Memory, 0, limit)
	for rows.Next() {
		var m model.Memory
		if err := scanMemory(rows, &m); err != nil {
			return nil, fmt.Errorf("list memories scan: %w", err)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// CountByUser returns the matching row count for the same filter
// shape ListByUser uses. The handler returns it alongside the
// page slice so the frontend can render "12 memories" without a
// second hop.
func (r *MemoryRepo) CountByUser(
	ctx context.Context, userID, tenantID uuid.UUID, f MemoryListFilters,
) (int, error) {
	status := f.Status
	if status == "" {
		status = model.MemoryStatusActive
	}
	q := `SELECT COUNT(*)::int FROM chat.user_memories
	      WHERE user_id = $1 AND tenant_id = $2 AND status = $3`
	args := []any{userID, tenantID, status}
	if f.ScopeType != "" {
		q += fmt.Sprintf(" AND scope_type = $%d", len(args)+1)
		args = append(args, f.ScopeType)
	}
	if f.MemoryType != "" {
		q += fmt.Sprintf(" AND memory_type = $%d", len(args)+1)
		args = append(args, f.MemoryType)
	}
	var n int
	if err := r.db.QueryRow(ctx, q, args...).Scan(&n); err != nil {
		return 0, fmt.Errorf("count memories: %w", err)
	}
	return n, nil
}

// GetActiveForInjection is the system-prompt builder's read path.
// Returns the user's active global memories, pinned first, ordered
// by importance then recency. Capped at 50 so the prompt block
// never blows up the context window.
func (r *MemoryRepo) GetActiveForInjection(
	ctx context.Context, userID, tenantID uuid.UUID,
) ([]model.Memory, error) {
	q := `SELECT ` + memoryColumns + `
	      FROM chat.user_memories
	      WHERE user_id = $1 AND tenant_id = $2
	        AND status = 'active'
	        AND scope_type = 'global_user'
	        AND (valid_until IS NULL OR valid_until > NOW())
	      ORDER BY is_pinned DESC, importance_score DESC, updated_at DESC
	      LIMIT 50`
	rows, err := r.db.Query(ctx, q, userID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("get active for injection: %w", err)
	}
	defer rows.Close()
	out := make([]model.Memory, 0, 50)
	for rows.Next() {
		var m model.Memory
		if err := scanMemory(rows, &m); err != nil {
			return nil, fmt.Errorf("get active scan: %w", err)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// ---------------------------------------------------------------------------
// Audit events
// ---------------------------------------------------------------------------

// LogEvent appends a row to chat.memory_events. The tenant_id +
// user_id columns let the audit endpoint scope reads without joining
// back to the (possibly deleted) parent memory row.
func (r *MemoryRepo) LogEvent(ctx context.Context, e *model.MemoryEvent) error {
	var metaJSON []byte
	if e.Metadata != nil {
		b, err := json.Marshal(e.Metadata)
		if err != nil {
			return fmt.Errorf("log event marshal metadata: %w", err)
		}
		metaJSON = b
	}
	const q = `
		INSERT INTO chat.memory_events
			(memory_id, tenant_id, user_id, action, actor_type, reason, metadata)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		RETURNING id, created_at
	`
	if err := r.db.QueryRow(ctx, q,
		e.MemoryID, e.TenantID, e.UserID,
		e.Action, e.ActorType, nullableText(e.Reason), metaJSON,
	).Scan(&e.ID, &e.CreatedAt); err != nil {
		return fmt.Errorf("log memory event: %w", err)
	}
	return nil
}

// GetEvents returns the audit trail for a single memory id, newest
// first. Tenant + user scoped so a cross-user id leak returns an
// empty slice.
func (r *MemoryRepo) GetEvents(
	ctx context.Context, memoryID, userID, tenantID uuid.UUID, limit int,
) ([]model.MemoryEvent, error) {
	if limit <= 0 {
		limit = 50
	}
	if limit > 200 {
		limit = 200
	}
	const q = `
		SELECT id, memory_id, tenant_id, user_id,
		       action, actor_type, reason, metadata, created_at
		FROM chat.memory_events
		WHERE memory_id = $1 AND user_id = $2 AND tenant_id = $3
		ORDER BY created_at DESC
		LIMIT $4
	`
	rows, err := r.db.Query(ctx, q, memoryID, userID, tenantID, limit)
	if err != nil {
		return nil, fmt.Errorf("get memory events: %w", err)
	}
	defer rows.Close()
	out := make([]model.MemoryEvent, 0)
	for rows.Next() {
		var e model.MemoryEvent
		var metaRaw []byte
		var reason *string
		if err := rows.Scan(
			&e.ID, &e.MemoryID, &e.TenantID, &e.UserID,
			&e.Action, &e.ActorType, &reason, &metaRaw, &e.CreatedAt,
		); err != nil {
			return nil, fmt.Errorf("get events scan: %w", err)
		}
		if reason != nil {
			e.Reason = *reason
		}
		if len(metaRaw) > 0 {
			_ = json.Unmarshal(metaRaw, &e.Metadata)
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// nullableText converts the empty string to a SQL NULL via a typed
// pointer. Keeps the memories table tidy (NULL category, not "") so
// the JSON marshaller can omitempty cleanly.
func nullableText(s string) any {
	if s == "" {
		return nil
	}
	return s
}
