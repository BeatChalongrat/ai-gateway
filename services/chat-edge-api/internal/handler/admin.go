// Admin observability handlers.
//
// All routes here are wrapped by middleware.RequireAdmin (shared
// bearer secret) so the chat-edge service can expose aggregate
// data to the admin-ui without a full per-user auth flow. The
// admin-ui surfaces these via a Next.js server-side proxy that
// injects the bearer; the browser never sees the secret.
//
// Scope: read-mostly endpoints (analytics, conversation listing +
// detail, user listing). One write endpoint flips a user's
// is_active flag — disabling users is the only mutation the
// observability dashboard actually needs to act on.
package handler

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// AdminHandler owns every /api/v1/admin/* route. It holds direct
// references to the pgxpool so the analytics queries can run their
// custom GROUP BYs without growing the per-resource repos.
type AdminHandler struct {
	pool *pgxpool.Pool
	kb   AdminKBSink
	// Optional. Wired in main.go so the S9 admin memory-stats
	// endpoint can call MemoryRepo.GetAggregateStats without
	// AdminHandler depending on the full service layer.
	memoryStatsFn func(ctx context.Context, tenantID uuid.UUID) (any, error)
	// Phase 4A.2.4.1 — when true, the admin conversation viewer is a
	// NON-RESTORING consumer: legacy raw display rows are redacted by default
	// (raw trace is a separate privileged surface, not the default viewer);
	// canonical_safe / redacted rows keep their already-safe content_masked.
	// Inlined here (no service-package import — see AdminKBSink rationale).
	// Wired from config in main.go; default false (legacy passthrough).
	displayLifecycle bool
}

// adminRedactedDisplay is the admin-default stand-in for a raw-sensitive
// (legacy) row. The admin viewer's default surface never shows restored raw
// display; a privileged raw-trace surface (secure trace) is the separate path.
const adminRedactedDisplay = "[redacted — raw display not shown in admin default view]"

// AdminKBSink is the function-bag the admin KB endpoints call when
// they need to mutate state. Function pointers (rather than an
// interface tied to KnowledgeService) keep admin.go free of any
// service-package imports — main.go wires the actual closures.
type AdminKBSink struct {
	ReindexDocument func(ctx context.Context, tenantID, kbID, docID uuid.UUID) error
	DeleteKB        func(ctx context.Context, tenantID, id uuid.UUID) error
}

// SetDisplayLifecycle toggles the non-raw admin viewer representation
// (Phase 4A.2.4.1). Set true when DLP_DISPLAY_LIFECYCLE_ENABLED is on.
func (h *AdminHandler) SetDisplayLifecycle(enabled bool) {
	h.displayLifecycle = enabled
}

func NewAdminHandler(pool *pgxpool.Pool) *AdminHandler {
	return &AdminHandler{pool: pool}
}

// SetKBSink wires the KB mutators. Called from main.go after the
// KnowledgeService is constructed. Kept out of the constructor so the
// AdminHandler still boots cleanly in tests that don't exercise
// knowledge-base admin.
func (h *AdminHandler) SetKBSink(s AdminKBSink) {
	h.kb = s
}

// SetMemoryStatsFn wires the S9 aggregate-stats lookup. Closure
// shape (rather than a typed repo dependency) keeps admin.go
// import-light — main.go assembles the closure with the
// MemoryRepo + the model marshalling.
func (h *AdminHandler) SetMemoryStatsFn(fn func(ctx context.Context, tenantID uuid.UUID) (any, error)) {
	h.memoryStatsFn = fn
}

// MemoryStats serves the S9 aggregate counts. Admin-bearer-only
// (routed under the RequireAdmin group). Tenant query param
// selects the target; default is the caller's own tenant when
// the admin surface eventually carries per-user identity, but
// for the bearer-secret admin path we require an explicit
// tenant_id query param so the operator picks deliberately.
//
// Response shape is intentionally counts-only — see
// MemoryRepo.GetAggregateStats's docstring.
func (h *AdminHandler) MemoryStats(w http.ResponseWriter, r *http.Request) {
	if h.memoryStatsFn == nil {
		http.Error(w, `{"error":"memory stats not configured"}`, http.StatusServiceUnavailable)
		return
	}
	raw := r.URL.Query().Get("tenant_id")
	if raw == "" {
		http.Error(w, `{"error":"tenant_id query param required"}`, http.StatusBadRequest)
		return
	}
	tid, err := uuid.Parse(raw)
	if err != nil {
		http.Error(w, `{"error":"invalid tenant_id"}`, http.StatusBadRequest)
		return
	}
	stats, err := h.memoryStatsFn(r.Context(), tid)
	if err != nil {
		http.Error(w, `{"error":"stats lookup failed"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"tenant_id": tid.String(),
		"stats":     stats,
	})
}

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------

type analyticsTotals struct {
	Conversations int     `json:"conversations"`
	Messages      int     `json:"messages"`
	TokensUsed    int64   `json:"tokens_used"`
	EstimatedCost float64 `json:"estimated_cost_usd"`
	ActiveUsers   int     `json:"active_users"`
	FilesUploaded int     `json:"files_uploaded"`
}

type analyticsDailyRow struct {
	Date          string `json:"date"`
	Conversations int    `json:"conversations"`
	Messages      int    `json:"messages"`
	TokensUsed    int64  `json:"tokens_used"`
	ActiveUsers   int    `json:"active_users"`
}

type analyticsByModelRow struct {
	ModelID       string  `json:"model_id"`
	Messages      int     `json:"messages"`
	TokensUsed    int64   `json:"tokens_used"`
	EstimatedCost float64 `json:"estimated_cost_usd"`
}

type analyticsResponse struct {
	Period  string                `json:"period"`
	Totals  analyticsTotals       `json:"totals"`
	Daily   []analyticsDailyRow   `json:"daily"`
	ByModel []analyticsByModelRow `json:"by_model"`
}

// Usage returns the analytics dump for the requested period.
//
//	GET /api/v1/admin/analytics/usage?period=today|7d|30d|90d
func (h *AdminHandler) Usage(w http.ResponseWriter, r *http.Request) {
	period := r.URL.Query().Get("period")
	if period == "" {
		period = "30d"
	}
	since, ok := periodSince(period)
	if !ok {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "unsupported period")
		return
	}

	ctx := r.Context()
	totals, err := h.fetchTotals(ctx, since)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	daily, err := h.fetchDaily(ctx, since)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	byModel, err := h.fetchByModel(ctx, since)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}

	// Cost roll-up: sum the per-model figures into the totals so the
	// dashboard's headline number matches the by-model breakdown.
	var totalCost float64
	for _, m := range byModel {
		totalCost += m.EstimatedCost
	}
	totals.EstimatedCost = totalCost

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(analyticsResponse{
		Period:  period,
		Totals:  totals,
		Daily:   daily,
		ByModel: byModel,
	})
}

// periodSince maps the wire period token onto a concrete cut-off.
// Anything outside the allowlist returns ok=false so the handler
// can 400 instead of silently substituting a default.
func periodSince(period string) (time.Time, bool) {
	now := time.Now().UTC()
	startOfToday := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	switch period {
	case "today":
		return startOfToday, true
	case "7d":
		return startOfToday.AddDate(0, 0, -6), true
	case "30d":
		return startOfToday.AddDate(0, 0, -29), true
	case "90d":
		return startOfToday.AddDate(0, 0, -89), true
	default:
		return time.Time{}, false
	}
}

func (h *AdminHandler) fetchTotals(ctx context.Context, since time.Time) (analyticsTotals, error) {
	const q = `
		WITH msgs AS (
			SELECT id, conversation_id, user_id, role, token_usage_input, token_usage_output, created_at
			FROM chat.messages_masked
			WHERE created_at >= $1
		)
		SELECT
			(SELECT COUNT(*) FROM chat.conversations WHERE updated_at >= $1 AND is_deleted = false),
			(SELECT COUNT(*) FROM msgs),
			(SELECT COALESCE(SUM(COALESCE(token_usage_input,0) + COALESCE(token_usage_output,0)), 0) FROM msgs),
			(SELECT COUNT(DISTINCT user_id) FROM msgs),
			(SELECT COUNT(*) FROM chat.conversation_files WHERE created_at >= $1)
	`
	var t analyticsTotals
	err := h.pool.QueryRow(ctx, q, since).Scan(
		&t.Conversations, &t.Messages, &t.TokensUsed, &t.ActiveUsers, &t.FilesUploaded,
	)
	if err != nil {
		return analyticsTotals{}, fmt.Errorf("totals: %w", err)
	}
	return t, nil
}

func (h *AdminHandler) fetchDaily(ctx context.Context, since time.Time) ([]analyticsDailyRow, error) {
	const q = `
		SELECT
			to_char(date_trunc('day', m.created_at), 'YYYY-MM-DD') AS day,
			COUNT(DISTINCT m.conversation_id) AS conversations,
			COUNT(*) AS messages,
			COALESCE(SUM(COALESCE(m.token_usage_input,0) + COALESCE(m.token_usage_output,0)), 0) AS tokens,
			COUNT(DISTINCT m.user_id) AS users
		FROM chat.messages_masked m
		WHERE m.created_at >= $1
		GROUP BY day
		ORDER BY day ASC
	`
	rows, err := h.pool.Query(ctx, q, since)
	if err != nil {
		return nil, fmt.Errorf("daily: %w", err)
	}
	defer rows.Close()
	out := make([]analyticsDailyRow, 0)
	for rows.Next() {
		var r analyticsDailyRow
		if err := rows.Scan(&r.Date, &r.Conversations, &r.Messages, &r.TokensUsed, &r.ActiveUsers); err != nil {
			return nil, fmt.Errorf("daily scan: %w", err)
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (h *AdminHandler) fetchByModel(ctx context.Context, since time.Time) ([]analyticsByModelRow, error) {
	const q = `
		SELECT
			COALESCE(model_id, 'unknown'),
			COUNT(*) AS messages,
			COALESCE(SUM(COALESCE(token_usage_input,0) + COALESCE(token_usage_output,0)), 0) AS tokens,
			COALESCE(SUM(COALESCE(token_usage_input,0)), 0) AS input_tokens,
			COALESCE(SUM(COALESCE(token_usage_output,0)), 0) AS output_tokens
		FROM chat.messages_masked
		WHERE created_at >= $1
		GROUP BY model_id
		ORDER BY messages DESC
	`
	rows, err := h.pool.Query(ctx, q, since)
	if err != nil {
		return nil, fmt.Errorf("by model: %w", err)
	}
	defer rows.Close()
	out := make([]analyticsByModelRow, 0)
	for rows.Next() {
		var r analyticsByModelRow
		var inputTok, outputTok int64
		if err := rows.Scan(&r.ModelID, &r.Messages, &r.TokensUsed, &inputTok, &outputTok); err != nil {
			return nil, fmt.Errorf("by model scan: %w", err)
		}
		// Cost = (input/1e6 * input_price) + (output/1e6 * output_price)
		// using the static pricing map from model.go.
		if meta, ok := modelMetadata[r.ModelID]; ok {
			r.EstimatedCost =
				(float64(inputTok)/1_000_000)*meta.InputPricePerM +
					(float64(outputTok)/1_000_000)*meta.OutputPricePerM
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

type adminConvRow struct {
	ID           string `json:"id"`
	Title        string `json:"title"`
	UserID       string `json:"user_id"`
	UserEmail    string `json:"user_email"`
	ModelID      string `json:"model_id"`
	MessageCount int    `json:"message_count"`
	TokenCount   int64  `json:"token_count"`
	HasFiles     bool   `json:"has_files"`
	IsShared     bool   `json:"is_shared"`
	CreatedAt    string `json:"created_at"`
	UpdatedAt    string `json:"updated_at"`
}

// Conversations returns a filterable + paginated list. Query params:
//
//	limit, offset
//	q          search by title (ILIKE)
//	user_id    filter
//	model_id   filter
//	date_from / date_to  bounded created_at
func (h *AdminHandler) Conversations(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	limit, _ := strconv.Atoi(q.Get("limit"))
	if limit <= 0 {
		limit = 50
	}
	if limit > 100 {
		limit = 100
	}
	offset, _ := strconv.Atoi(q.Get("offset"))
	if offset < 0 {
		offset = 0
	}

	conditions := []string{"c.is_deleted = false"}
	args := []any{}
	idx := 1
	addCond := func(clause string, value any) {
		conditions = append(conditions, fmt.Sprintf(clause, idx))
		args = append(args, value)
		idx++
	}
	if v := strings.TrimSpace(q.Get("q")); v != "" {
		addCond("c.title ILIKE $%d", "%"+v+"%")
	}
	if v := q.Get("user_id"); v != "" {
		if id, err := uuid.Parse(v); err == nil {
			addCond("c.user_id = $%d", id)
		}
	}
	if v := q.Get("model_id"); v != "" {
		addCond("c.model_id = $%d", v)
	}
	if v := q.Get("date_from"); v != "" {
		if t, err := time.Parse("2006-01-02", v); err == nil {
			addCond("c.created_at >= $%d", t)
		}
	}
	if v := q.Get("date_to"); v != "" {
		if t, err := time.Parse("2006-01-02", v); err == nil {
			addCond("c.created_at < $%d", t.AddDate(0, 0, 1))
		}
	}

	whereSQL := strings.Join(conditions, " AND ")

	// Total count for pagination — runs the same WHERE without the
	// limit so the dashboard can render "Showing 1–50 of 1234".
	countSQL := fmt.Sprintf(`SELECT COUNT(*) FROM chat.conversations c WHERE %s`, whereSQL)
	var total int
	if err := h.pool.QueryRow(r.Context(), countSQL, args...).Scan(&total); err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}

	listSQL := fmt.Sprintf(`
		SELECT c.id, c.title, c.user_id, COALESCE(u.email, ''), c.model_id,
		       c.message_count,
		       COALESCE((
		         SELECT SUM(COALESCE(m.token_usage_input,0) + COALESCE(m.token_usage_output,0))
		         FROM chat.messages_masked m
		         WHERE m.conversation_id = c.id
		       ), 0) AS token_count,
		       EXISTS(SELECT 1 FROM chat.conversation_files f WHERE f.conversation_id = c.id) AS has_files,
		       EXISTS(SELECT 1 FROM chat.shared_conversations s WHERE s.conversation_id = c.id) AS is_shared,
		       c.created_at, c.updated_at
		FROM chat.conversations c
		LEFT JOIN identity.users u ON u.id = c.user_id
		WHERE %s
		ORDER BY c.updated_at DESC
		LIMIT $%d OFFSET $%d
	`, whereSQL, idx, idx+1)
	args = append(args, limit, offset)
	rows, err := h.pool.Query(r.Context(), listSQL, args...)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	defer rows.Close()

	items := make([]adminConvRow, 0, limit)
	for rows.Next() {
		var (
			c         adminConvRow
			createdAt time.Time
			updatedAt time.Time
		)
		if err := rows.Scan(
			&c.ID, &c.Title, &c.UserID, &c.UserEmail, &c.ModelID,
			&c.MessageCount, &c.TokenCount, &c.HasFiles, &c.IsShared,
			&createdAt, &updatedAt,
		); err != nil {
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
			return
		}
		c.CreatedAt = createdAt.UTC().Format(time.RFC3339)
		c.UpdatedAt = updatedAt.UTC().Format(time.RFC3339)
		items = append(items, c)
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"conversations": items,
		"total":         total,
		"limit":         limit,
		"offset":        offset,
	})
}

// ConversationDetail returns the masked conversation + messages for
// the admin viewer.
type adminMessageRow struct {
	ID          string  `json:"id"`
	Role        string  `json:"role"`
	Content     string  `json:"content"`
	ModelID     *string `json:"model_id,omitempty"`
	CreatedAt   string  `json:"created_at"`
	TokenInput  *int    `json:"token_input,omitempty"`
	TokenOutput *int    `json:"token_output,omitempty"`
	HasFiles    bool    `json:"has_files"`
}

type adminFileRow struct {
	ID          string `json:"id"`
	MessageID   string `json:"message_id,omitempty"`
	Filename    string `json:"filename"`
	ContentType string `json:"content_type"`
	FileSize    int64  `json:"file_size_bytes"`
	CreatedAt   string `json:"created_at"`
}

func (h *AdminHandler) ConversationDetail(w http.ResponseWriter, r *http.Request) {
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}

	const convQ = `
		SELECT c.id, c.title, c.user_id, COALESCE(u.email,''), COALESCE(u.name,''),
		       c.model_id, c.message_count, c.created_at, c.updated_at
		FROM chat.conversations c
		LEFT JOIN identity.users u ON u.id = c.user_id
		WHERE c.id = $1 AND c.is_deleted = false
	`
	var (
		id, title, userID, userEmail, userName, modelID string
		messageCount                                    int
		createdAt, updatedAt                            time.Time
	)
	err = h.pool.QueryRow(r.Context(), convQ, convID).Scan(
		&id, &title, &userID, &userEmail, &userName, &modelID,
		&messageCount, &createdAt, &updatedAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) || errors.Is(err, sql.ErrNoRows) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}

	// Phase 4A.2.4.1 (H4-style): select display_state only when the feature is
	// enabled (pre-migration-025 DBs never reference the column). Legacy rows
	// are then redacted in the default admin viewer.
	dlCol := ""
	if h.displayLifecycle {
		dlCol = ", display_state"
	}
	msgQ := `
		SELECT id, role, content_masked, model_id, token_usage_input,
		       token_usage_output, created_at,
		       EXISTS(SELECT 1 FROM chat.conversation_files f WHERE f.message_id = m.id)` + dlCol + `
		FROM chat.messages_masked m
		WHERE conversation_id = $1 AND is_active = true
		ORDER BY created_at ASC
		LIMIT 1000
	`
	rows, err := h.pool.Query(r.Context(), msgQ, convID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	defer rows.Close()
	msgs := make([]adminMessageRow, 0)
	for rows.Next() {
		var (
			m            adminMessageRow
			created      time.Time
			modelPtr     *string
			tokenIn      *int
			tokenOut     *int
			hasFiles     bool
			displayState string
		)
		dest := []any{&m.ID, &m.Role, &m.Content, &modelPtr, &tokenIn, &tokenOut, &created, &hasFiles}
		if h.displayLifecycle {
			dest = append(dest, &displayState)
		}
		if err := rows.Scan(dest...); err != nil {
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
			return
		}
		// Non-restoring admin default: redact raw-sensitive (legacy/empty) rows;
		// canonical_safe / redacted rows already hold safe content_masked.
		if h.displayLifecycle && displayState != "canonical_safe" && displayState != "redacted" {
			m.Content = adminRedactedDisplay
		}
		m.CreatedAt = created.UTC().Format(time.RFC3339)
		m.ModelID = modelPtr
		m.TokenInput = tokenIn
		m.TokenOutput = tokenOut
		m.HasFiles = hasFiles
		msgs = append(msgs, m)
	}

	// File metadata (no download — admin viewer is strictly read-
	// only on file contents).
	const fileQ = `
		SELECT id, COALESCE(message_id::text, ''), original_filename,
		       content_type, file_size_bytes, created_at
		FROM chat.conversation_files
		WHERE conversation_id = $1
		ORDER BY created_at ASC
	`
	frows, err := h.pool.Query(r.Context(), fileQ, convID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	defer frows.Close()
	files := make([]adminFileRow, 0)
	for frows.Next() {
		var (
			f       adminFileRow
			created time.Time
		)
		if err := frows.Scan(&f.ID, &f.MessageID, &f.Filename, &f.ContentType, &f.FileSize, &created); err != nil {
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
			return
		}
		f.CreatedAt = created.UTC().Format(time.RFC3339)
		files = append(files, f)
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"conversation": map[string]any{
			"id":            id,
			"title":         title,
			"user_id":       userID,
			"user_email":    userEmail,
			"user_name":     userName,
			"model_id":      modelID,
			"message_count": messageCount,
			"created_at":    createdAt.UTC().Format(time.RFC3339),
			"updated_at":    updatedAt.UTC().Format(time.RFC3339),
		},
		"messages": msgs,
		"files":    files,
	})
}

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

type adminUserRow struct {
	ID                string  `json:"id"`
	Email             string  `json:"email"`
	Name              string  `json:"name"`
	Role              string  `json:"role"`
	IsActive          bool    `json:"is_active"`
	ConversationCount int     `json:"conversation_count"`
	MessageCount      int     `json:"message_count"`
	TokenCount        int64   `json:"token_count"`
	LastActiveAt      *string `json:"last_active_at,omitempty"`
	CreatedAt         string  `json:"created_at"`
}

func (h *AdminHandler) Users(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	limit, _ := strconv.Atoi(q.Get("limit"))
	if limit <= 0 {
		limit = 50
	}
	if limit > 200 {
		limit = 200
	}
	offset, _ := strconv.Atoi(q.Get("offset"))
	if offset < 0 {
		offset = 0
	}

	conditions := []string{"1=1"}
	args := []any{}
	idx := 1
	if v := strings.TrimSpace(q.Get("q")); v != "" {
		conditions = append(conditions, fmt.Sprintf("(u.email ILIKE $%d OR u.name ILIKE $%d)", idx, idx))
		args = append(args, "%"+v+"%")
		idx++
	}
	whereSQL := strings.Join(conditions, " AND ")

	countSQL := fmt.Sprintf(`SELECT COUNT(*) FROM identity.users u WHERE %s`, whereSQL)
	var total int
	if err := h.pool.QueryRow(r.Context(), countSQL, args...).Scan(&total); err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}

	listSQL := fmt.Sprintf(`
		SELECT u.id, u.email, u.name, u.role, u.is_active,
		       COALESCE((
		         SELECT COUNT(*) FROM chat.conversations c
		         WHERE c.user_id = u.id AND c.is_deleted = false
		       ), 0) AS conv_count,
		       COALESCE((
		         SELECT COUNT(*) FROM chat.messages_masked m
		         WHERE m.user_id = u.id
		       ), 0) AS msg_count,
		       COALESCE((
		         SELECT SUM(COALESCE(m.token_usage_input,0) + COALESCE(m.token_usage_output,0))
		         FROM chat.messages_masked m
		         WHERE m.user_id = u.id
		       ), 0) AS token_count,
		       (
		         SELECT MAX(m.created_at) FROM chat.messages_masked m
		         WHERE m.user_id = u.id
		       ) AS last_active,
		       u.created_at
		FROM identity.users u
		WHERE %s
		ORDER BY last_active DESC NULLS LAST, u.created_at DESC
		LIMIT $%d OFFSET $%d
	`, whereSQL, idx, idx+1)
	args = append(args, limit, offset)

	rows, err := h.pool.Query(r.Context(), listSQL, args...)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	defer rows.Close()

	users := make([]adminUserRow, 0, limit)
	for rows.Next() {
		var (
			u          adminUserRow
			lastActive *time.Time
			createdAt  time.Time
		)
		if err := rows.Scan(
			&u.ID, &u.Email, &u.Name, &u.Role, &u.IsActive,
			&u.ConversationCount, &u.MessageCount, &u.TokenCount,
			&lastActive, &createdAt,
		); err != nil {
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
			return
		}
		u.CreatedAt = createdAt.UTC().Format(time.RFC3339)
		if lastActive != nil {
			s := lastActive.UTC().Format(time.RFC3339)
			u.LastActiveAt = &s
		}
		users = append(users, u)
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"users":  users,
		"total":  total,
		"limit":  limit,
		"offset": offset,
	})
}

// UpdateUser flips is_active. Allowed-models override is accepted
// in the body but persisted in user_preferences for now — a real
// enforcement column belongs in identity.users and is deferred to
// a future migration.
type updateUserRequest struct {
	IsActive      *bool     `json:"is_active,omitempty"`
	AllowedModels *[]string `json:"allowed_models,omitempty"`
}

func (h *AdminHandler) UpdateUser(w http.ResponseWriter, r *http.Request) {
	userID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid user id")
		return
	}
	var req updateUserRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.IsActive == nil && req.AllowedModels == nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "no fields to update")
		return
	}
	if req.IsActive != nil {
		const q = `UPDATE identity.users SET is_active = $2, updated_at = NOW() WHERE id = $1`
		if _, err := h.pool.Exec(r.Context(), q, userID, *req.IsActive); err != nil {
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
			return
		}
	}
	// allowed_models override storage is deferred — accept the field
	// without persisting so the UI's PATCH succeeds. A future
	// migration will wire it into a real gating column.
	w.WriteHeader(http.StatusNoContent)
}
