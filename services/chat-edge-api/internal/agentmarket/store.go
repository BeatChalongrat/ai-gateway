package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ErrNotFound is returned for any row the caller may not see or that does not exist.
var ErrNotFound = errors.New("agentmarket: not found")

// Store is the agent_market data access used by both planes. Every query
// names schema agent_market explicitly; none touches another schema.
type Store struct {
	Pool *pgxpool.Pool
}

// NewStore wraps a pool that belongs to Agent Market alone.
func NewStore(pool *pgxpool.Pool) *Store { return &Store{Pool: pool} }

func jsonOr(b json.RawMessage, def string) json.RawMessage {
	if len(b) == 0 {
		return json.RawMessage(def)
	}
	return b
}

// ── definitions ──────────────────────────────────────────────────────────

const defCols = `id, slug, version, name, manifest, instructions, checksum, engine_checksum, status, updated_at`

func scanDef(r pgx.Row) (Definition, error) {
	var d Definition
	err := r.Scan(&d.ID, &d.Slug, &d.Version, &d.Name, &d.Manifest, &d.Instructions, &d.Checksum, &d.EngineChecksum, &d.Status, &d.UpdatedAt)
	return d, err
}

// UpsertDefinition registers a package version (runner, at start).
func (s *Store) UpsertDefinition(ctx context.Context, d Definition) (Definition, error) {
	row := s.Pool.QueryRow(ctx, `
		INSERT INTO agent_market.agent_definitions (slug, version, name, manifest, instructions, checksum, engine_checksum)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (slug, version) DO UPDATE SET name = EXCLUDED.name, manifest = EXCLUDED.manifest,
			instructions = EXCLUDED.instructions, checksum = EXCLUDED.checksum,
			engine_checksum = EXCLUDED.engine_checksum, status = 'active', updated_at = now()
		RETURNING `+defCols, d.Slug, d.Version, d.Name, d.Manifest, d.Instructions, d.Checksum, d.EngineChecksum)
	return scanDef(row)
}

// ListDefinitions returns the newest active version of every package.
func (s *Store) ListDefinitions(ctx context.Context) ([]Definition, error) {
	rows, err := s.Pool.Query(ctx, `
		SELECT DISTINCT ON (slug) `+defCols+`
		FROM agent_market.agent_definitions WHERE status = 'active'
		ORDER BY slug, string_to_array(version, '.')::int[] DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Definition
	for rows.Next() {
		d, err := scanDef(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// DefinitionBySlug returns the newest active version of a package.
func (s *Store) DefinitionBySlug(ctx context.Context, slug string) (Definition, error) {
	d, err := scanDef(s.Pool.QueryRow(ctx, `
		SELECT `+defCols+` FROM agent_market.agent_definitions WHERE slug = $1 AND status = 'active'
		ORDER BY string_to_array(version, '.')::int[] DESC LIMIT 1`, slug))
	if errors.Is(err, pgx.ErrNoRows) {
		return d, ErrNotFound
	}
	return d, err
}

// DefinitionByID returns one version.
func (s *Store) DefinitionByID(ctx context.Context, id uuid.UUID) (Definition, error) {
	d, err := scanDef(s.Pool.QueryRow(ctx, `SELECT `+defCols+` FROM agent_market.agent_definitions WHERE id = $1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return d, ErrNotFound
	}
	return d, err
}

// ── installs ─────────────────────────────────────────────────────────────

const installCols = `id, tenant_id, user_id, definition_id, slug, definition_version, checksum, title,
	capability_bindings, binding_version, budget_credits::float8, status, self_test_run_id, created_at, updated_at`

func scanInstall(r pgx.Row) (Install, error) {
	var i Install
	err := r.Scan(&i.ID, &i.TenantID, &i.UserID, &i.DefinitionID, &i.Slug, &i.DefinitionVersion, &i.Checksum, &i.Title,
		&i.CapabilityBindings, &i.BindingVersion, &i.BudgetCredits, &i.Status, &i.SelfTestRunID, &i.CreatedAt, &i.UpdatedAt)
	return i, err
}

// CreateInstall is idempotent on (user, idempotency key): a double-clicked
// install returns the first one.
func (s *Store) CreateInstall(ctx context.Context, tx pgx.Tx, in Install, idem string) (Install, bool, error) {
	var idemArg any
	if idem != "" {
		idemArg = idem
		existing, err := scanInstall(tx.QueryRow(ctx, `SELECT `+installCols+` FROM agent_market.agent_installs
			WHERE user_id = $1 AND idempotency_key = $2`, in.UserID, idem))
		if err == nil {
			return existing, false, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return Install{}, false, err
		}
	}
	row := tx.QueryRow(ctx, `
		INSERT INTO agent_market.agent_installs (tenant_id, user_id, definition_id, slug, definition_version, checksum,
			title, capability_bindings, binding_version, budget_credits, idempotency_key)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
		RETURNING `+installCols,
		in.TenantID, in.UserID, in.DefinitionID, in.Slug, in.DefinitionVersion, in.Checksum, in.Title,
		jsonOr(in.CapabilityBindings, "{}"), in.BindingVersion, in.BudgetCredits, idemArg)
	i, err := scanInstall(row)
	return i, err == nil, err
}

// ListInstalls returns a user's active installs, newest first.
func (s *Store) ListInstalls(ctx context.Context, tenant, user uuid.UUID) ([]Install, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+installCols+` FROM agent_market.agent_installs
		WHERE tenant_id = $1 AND user_id = $2 AND status = 'active' ORDER BY created_at DESC`, tenant, user)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Install
	for rows.Next() {
		i, err := scanInstall(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, i)
	}
	return out, rows.Err()
}

// GetInstall scopes by owner; another user's install is ErrNotFound.
func (s *Store) GetInstall(ctx context.Context, tenant, user, id uuid.UUID) (Install, error) {
	i, err := scanInstall(s.Pool.QueryRow(ctx, `SELECT `+installCols+` FROM agent_market.agent_installs
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3 AND status = 'active'`, id, tenant, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return i, ErrNotFound
	}
	return i, err
}

// InstallByID is the runner's unscoped read (it acts for the run's owner).
func (s *Store) InstallByID(ctx context.Context, id uuid.UUID) (Install, error) {
	i, err := scanInstall(s.Pool.QueryRow(ctx, `SELECT `+installCols+` FROM agent_market.agent_installs WHERE id = $1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return i, ErrNotFound
	}
	return i, err
}

// RemoveInstall hides an install (its rows stay for the audit trail).
func (s *Store) RemoveInstall(ctx context.Context, tenant, user, id uuid.UUID) error {
	tag, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET status = 'removed', removed_at = now(), updated_at = now()
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3 AND status = 'active'`, id, tenant, user)
	if err == nil && tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return err
}

// SetSelfTestRun links the install to its self-test run.
func (s *Store) SetSelfTestRun(ctx context.Context, install, run uuid.UUID) error {
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET self_test_run_id = $2, updated_at = now() WHERE id = $1`, install, run)
	return err
}

// ── defaults ─────────────────────────────────────────────────────────────

// SetDefault writes one remembered answer / setting.
func (s *Store) SetDefault(ctx context.Context, q Querier, install uuid.UUID, d Default) error {
	_, err := q.Exec(ctx, `
		INSERT INTO agent_market.agent_install_defaults (install_id, key, value, source, label, note, from_run_id, from_card_id)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		ON CONFLICT (install_id, key) DO UPDATE SET value = EXCLUDED.value, source = EXCLUDED.source,
			label = EXCLUDED.label, note = EXCLUDED.note, from_run_id = EXCLUDED.from_run_id,
			from_card_id = EXCLUDED.from_card_id, updated_at = now()`,
		install, d.Key, d.Value, d.Source, d.Label, d.Note, d.FromRunID, d.FromCardID)
	return err
}

// DeleteDefault clears one remembered answer.
func (s *Store) DeleteDefault(ctx context.Context, install uuid.UUID, key string) (bool, error) {
	tag, err := s.Pool.Exec(ctx, `DELETE FROM agent_market.agent_install_defaults WHERE install_id = $1 AND key = $2`, install, key)
	return tag.RowsAffected() > 0, err
}

// ListDefaults returns every remembered answer and setting of an install.
func (s *Store) ListDefaults(ctx context.Context, install uuid.UUID) ([]Default, error) {
	rows, err := s.Pool.Query(ctx, `SELECT key, value, source, label, note, from_run_id, from_card_id, updated_at
		FROM agent_market.agent_install_defaults WHERE install_id = $1 ORDER BY key`, install)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Default
	for rows.Next() {
		var d Default
		if err := rows.Scan(&d.Key, &d.Value, &d.Source, &d.Label, &d.Note, &d.FromRunID, &d.FromCardID, &d.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// ── themes ───────────────────────────────────────────────────────────────

// LatestTheme returns the tenant's newest theme (version 0 = none yet).
func (s *Store) LatestTheme(ctx context.Context, tenant uuid.UUID) (json.RawMessage, int, *string, error) {
	var theme json.RawMessage
	var version int
	var logo *string
	err := s.Pool.QueryRow(ctx, `SELECT theme, version, logo_key FROM agent_market.agent_themes
		WHERE tenant_id = $1 ORDER BY version DESC LIMIT 1`, tenant).Scan(&theme, &version, &logo)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, 0, nil, nil
	}
	return theme, version, logo, err
}

// ThemeVersion returns one version.
func (s *Store) ThemeVersion(ctx context.Context, tenant uuid.UUID, version int) (json.RawMessage, *string, error) {
	var theme json.RawMessage
	var logo *string
	err := s.Pool.QueryRow(ctx, `SELECT theme, logo_key FROM agent_market.agent_themes WHERE tenant_id = $1 AND version = $2`,
		tenant, version).Scan(&theme, &logo)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, ErrNotFound
	}
	return theme, logo, err
}

// SaveTheme stores a new version of the tenant's theme.
func (s *Store) SaveTheme(ctx context.Context, tenant, by uuid.UUID, theme json.RawMessage, logo *string) (int, error) {
	var v int
	err := s.Pool.QueryRow(ctx, `
		INSERT INTO agent_market.agent_themes (tenant_id, version, theme, logo_key, created_by)
		VALUES ($1, COALESCE((SELECT max(version) FROM agent_market.agent_themes WHERE tenant_id = $1), 0) + 1, $2, $3, $4)
		RETURNING version`, tenant, theme, logo, by).Scan(&v)
	return v, err
}

// ── files ────────────────────────────────────────────────────────────────

const fileCols = `id, install_id, run_id, tenant_id, user_id, name, content_type, size, sha256, storage_key, status, client_id, created_at`

func scanFile(r pgx.Row) (File, error) {
	var f File
	err := r.Scan(&f.ID, &f.InstallID, &f.RunID, &f.TenantID, &f.UserID, &f.Name, &f.ContentType, &f.Size, &f.SHA256,
		&f.StorageKey, &f.Status, &f.ClientID, &f.CreatedAt)
	return f, err
}

// CreateFile records an uploaded file (bytes already in storage).
func (s *Store) CreateFile(ctx context.Context, q Querier, f File) (File, error) {
	return scanFile(q.QueryRow(ctx, `
		INSERT INTO agent_market.agent_files (id, tenant_id, user_id, install_id, run_id, name, content_type, size, sha256,
			storage_key, status, client_id)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
		RETURNING `+fileCols, f.ID, f.TenantID, f.UserID, f.InstallID, f.RunID, f.Name, f.ContentType, f.Size, f.SHA256,
		f.StorageKey, f.Status, f.ClientID))
}

// GetFile scopes by owner.
func (s *Store) GetFile(ctx context.Context, tenant, user, id uuid.UUID) (File, error) {
	f, err := scanFile(s.Pool.QueryRow(ctx, `SELECT `+fileCols+` FROM agent_market.agent_files
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`, id, tenant, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return f, ErrNotFound
	}
	return f, err
}

// RunFiles lists a run's files with the given statuses (all when none).
func (s *Store) RunFiles(ctx context.Context, run uuid.UUID, statuses ...string) ([]File, error) {
	q := `SELECT ` + fileCols + ` FROM agent_market.agent_files WHERE run_id = $1`
	args := []any{run}
	if len(statuses) > 0 {
		q += ` AND status = ANY($2)`
		args = append(args, statuses)
	}
	rows, err := s.Pool.Query(ctx, q+` ORDER BY created_at, name`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []File
	for rows.Next() {
		f, err := scanFile(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// AttachFiles moves staged files (owned by the caller) onto a run.
func (s *Store) AttachFiles(ctx context.Context, q Querier, tenant, user, run uuid.UUID, ids []uuid.UUID, status string) (int64, error) {
	tag, err := q.Exec(ctx, `UPDATE agent_market.agent_files SET run_id = $4, status = $5
		WHERE id = ANY($1) AND tenant_id = $2 AND user_id = $3 AND status IN ('staged', 'pending_decision')`,
		ids, tenant, user, run, status)
	return tag.RowsAffected(), err
}

// SetFileStatus changes a run file's status (e.g. removed after "use a different file").
func (s *Store) SetFileStatus(ctx context.Context, q Querier, run uuid.UUID, names []string, status string) error {
	_, err := q.Exec(ctx, `UPDATE agent_market.agent_files SET status = $3 WHERE run_id = $1 AND name = ANY($2)`, run, names, status)
	return err
}

// PendingFiles lists files waiting for a include-or-new-round decision.
func (s *Store) PendingFiles(ctx context.Context, run uuid.UUID) ([]File, error) {
	return s.RunFiles(ctx, run, "pending_decision")
}

// ── runs ─────────────────────────────────────────────────────────────────

const runCols = `id, install_id, parent_run_id, tenant_id, user_id, kind, status, status_reason, period, message, params,
	definition_version, checksum, engine_checksum, binding_version, formats, answers, assumptions, cancel_requested,
	lease_owner, lease_until, attempts, credits_spent::float8, model_used, model_self_hosted, awaiting_since, awaiting_ms,
	created_at, started_at, finished_at, updated_at`

func scanRun(r pgx.Row) (Run, error) {
	var x Run
	err := r.Scan(&x.ID, &x.InstallID, &x.ParentRunID, &x.TenantID, &x.UserID, &x.Kind, &x.Status, &x.StatusReason,
		&x.Period, &x.Message, &x.Params, &x.DefinitionVersion, &x.Checksum, &x.EngineChecksum, &x.BindingVersion,
		&x.Formats, &x.Answers, &x.Assumptions, &x.CancelRequested, &x.LeaseOwner, &x.LeaseUntil, &x.Attempts,
		&x.CreditsSpent, &x.ModelUsed, &x.ModelSelfHosted, &x.AwaitingSince, &x.AwaitingMS, &x.CreatedAt, &x.StartedAt,
		&x.FinishedAt, &x.UpdatedAt)
	return x, err
}

// Querier is satisfied by a pool and a transaction.
type Querier interface {
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
}

// CreateRun is idempotent on (install, idempotency key).
func (s *Store) CreateRun(ctx context.Context, q Querier, r Run, idem string) (Run, bool, error) {
	var idemArg any
	if idem != "" {
		idemArg = idem
		existing, err := scanRun(q.QueryRow(ctx, `SELECT `+runCols+` FROM agent_market.agent_runs
			WHERE install_id = $1 AND idempotency_key = $2`, r.InstallID, idem))
		if err == nil {
			return existing, false, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return Run{}, false, err
		}
	}
	if r.ID == uuid.Nil {
		r.ID = uuid.New()
	}
	x, err := scanRun(q.QueryRow(ctx, `
		INSERT INTO agent_market.agent_runs (id, install_id, parent_run_id, tenant_id, user_id, kind, status, period, message,
			params, definition_version, checksum, engine_checksum, binding_version, formats, answers, idempotency_key)
		VALUES ($1, $2, $3, $4, $5, $6, 'queued', $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
		RETURNING `+runCols,
		r.ID, r.InstallID, r.ParentRunID, r.TenantID, r.UserID, r.Kind, r.Period, r.Message, jsonOr(r.Params, "{}"),
		r.DefinitionVersion, r.Checksum, r.EngineChecksum, r.BindingVersion, jsonOr(r.Formats, "[]"),
		jsonOr(r.Answers, "{}"), idemArg))
	return x, err == nil, err
}

// GetRun scopes by owner.
func (s *Store) GetRun(ctx context.Context, tenant, user, id uuid.UUID) (Run, error) {
	x, err := scanRun(s.Pool.QueryRow(ctx, `SELECT `+runCols+` FROM agent_market.agent_runs
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`, id, tenant, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	return x, err
}

// RunByID is the runner's read.
func (s *Store) RunByID(ctx context.Context, id uuid.UUID) (Run, error) {
	x, err := scanRun(s.Pool.QueryRow(ctx, `SELECT `+runCols+` FROM agent_market.agent_runs WHERE id = $1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	return x, err
}

// ListRuns lists an install's runs (kind run/self_test), newest first.
func (s *Store) ListRuns(ctx context.Context, install uuid.UUID, limit int) ([]Run, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+runCols+` FROM agent_market.agent_runs
		WHERE install_id = $1 AND kind IN ('run', 'self_test') ORDER BY created_at DESC LIMIT $2`, install, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Run
	for rows.Next() {
		x, err := scanRun(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

// ChildRuns lists jobs hanging off a run.
func (s *Store) ChildRuns(ctx context.Context, parent uuid.UUID) ([]Run, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+runCols+` FROM agent_market.agent_runs WHERE parent_run_id = $1 ORDER BY created_at`, parent)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Run
	for rows.Next() {
		x, err := scanRun(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

// LatestRun returns the install's newest full run (any status), or ErrNotFound.
func (s *Store) LatestRun(ctx context.Context, install uuid.UUID) (Run, error) {
	x, err := scanRun(s.Pool.QueryRow(ctx, `SELECT `+runCols+` FROM agent_market.agent_runs
		WHERE install_id = $1 AND kind = 'run' ORDER BY created_at DESC LIMIT 1`, install))
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	return x, err
}

// RequestCancel is idempotent: a second call finds the same final state.
func (s *Store) RequestCancel(ctx context.Context, tenant, user, id uuid.UUID) (Run, error) {
	x, err := scanRun(s.Pool.QueryRow(ctx, `
		UPDATE agent_market.agent_runs SET cancel_requested = true,
			status = CASE WHEN status IN ('queued', 'awaiting_input') THEN 'cancelled' ELSE status END,
			status_reason = CASE WHEN status IN ('queued', 'awaiting_input') THEN 'cancelled_by_user' ELSE status_reason END,
			finished_at = CASE WHEN status IN ('queued', 'awaiting_input') THEN now() ELSE finished_at END,
			awaiting_ms = awaiting_ms + CASE WHEN status = 'awaiting_input' AND awaiting_since IS NOT NULL
				THEN (extract(epoch FROM now() - awaiting_since) * 1000)::bigint ELSE 0 END,
			awaiting_since = CASE WHEN status = 'awaiting_input' THEN NULL ELSE awaiting_since END,
			updated_at = now()
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3
		RETURNING `+runCols, id, tenant, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	return x, err
}

// Resume moves a parked run back to the queue (an answer or a file arrived).
func (s *Store) Resume(ctx context.Context, q Querier, run uuid.UUID) (bool, error) {
	tag, err := q.Exec(ctx, `
		UPDATE agent_market.agent_runs SET status = 'queued',
			awaiting_ms = awaiting_ms + CASE WHEN awaiting_since IS NOT NULL
				THEN (extract(epoch FROM now() - awaiting_since) * 1000)::bigint ELSE 0 END,
			awaiting_since = NULL, updated_at = now()
		WHERE id = $1 AND status IN ('awaiting_input', 'succeeded') AND NOT cancel_requested`, run)
	return tag.RowsAffected() > 0, err
}

// MergeAnswers adds resolved answers to the run (jsonb merge by key). An
// answer from the person (a click, a typed sentence, a skip) replaces the
// "ran on the default for now" assumption its question was given, so the
// files never say a default was used for something the person chose.
func (s *Store) MergeAnswers(ctx context.Context, q Querier, run uuid.UUID, answers map[string]Answer) error {
	if len(answers) == 0 {
		return nil
	}
	var answered []string
	for key, a := range answers {
		if a.Via == "card" || a.Via == "text" || a.Via == "skip" {
			code := a.Code
			if code == "" {
				code = key
			}
			answered = append(answered, code)
		}
	}
	b, _ := json.Marshal(answers)
	_, err := q.Exec(ctx, `UPDATE agent_market.agent_runs SET answers = answers || $2::jsonb,
		assumptions = CASE WHEN cardinality($3::text[]) = 0 THEN assumptions ELSE (
			SELECT COALESCE(jsonb_agg(x), '[]'::jsonb) FROM jsonb_array_elements(assumptions) AS x
			WHERE NOT (x->>'source' = 'default' AND x->>'code' = ANY($3::text[]))) END,
		updated_at = now() WHERE id = $1`, run, b, answered)
	return err
}

// AddAssumptions appends to the run's assumption list (deduplicated by code+text).
func (s *Store) AddAssumptions(ctx context.Context, q Querier, run uuid.UUID, a []Assumption) error {
	if len(a) == 0 {
		return nil
	}
	b, _ := json.Marshal(a)
	_, err := q.Exec(ctx, `
		UPDATE agent_market.agent_runs SET assumptions = (
			SELECT COALESCE(jsonb_agg(DISTINCT x), '[]'::jsonb) FROM jsonb_array_elements(assumptions || $2::jsonb) AS x
		), updated_at = now() WHERE id = $1`, run, b)
	return err
}

// ── runner: claim, lease, finish ─────────────────────────────────────────

// ClaimRun takes the oldest queued run, or a running one whose lease
// expired (its runner died). SKIP LOCKED keeps two runners from taking the
// same run; the lease keeps a live runner's run from being stolen.
func (s *Store) ClaimRun(ctx context.Context, runner string, lease time.Duration) (Run, error) {
	x, err := scanRun(s.Pool.QueryRow(ctx, `
		UPDATE agent_market.agent_runs r SET status = 'running', lease_owner = $1, lease_until = now() + $2::interval,
			attempts = attempts + 1, started_at = COALESCE(started_at, now()), updated_at = now()
		WHERE r.id = (
			SELECT id FROM agent_market.agent_runs
			WHERE (status = 'queued' OR (status = 'running' AND lease_until < now()))
			ORDER BY CASE kind WHEN 'reply' THEN 0 WHEN 'mapping_preview' THEN 0 WHEN 'render' THEN 1 ELSE 2 END, created_at
			FOR UPDATE SKIP LOCKED LIMIT 1)
		RETURNING `+runCols, runner, fmt.Sprintf("%d milliseconds", lease.Milliseconds())))
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	return x, err
}

// RenewLease extends the lease; false means the run is no longer ours
// (cancelled to a terminal state, or taken over).
func (s *Store) RenewLease(ctx context.Context, run uuid.UUID, runner string, lease time.Duration) (bool, bool, error) {
	var cancel bool
	err := s.Pool.QueryRow(ctx, `UPDATE agent_market.agent_runs SET lease_until = now() + $3::interval
		WHERE id = $1 AND lease_owner = $2 AND status = 'running' RETURNING cancel_requested`,
		run, runner, fmt.Sprintf("%d milliseconds", lease.Milliseconds())).Scan(&cancel)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, false, nil
	}
	return err == nil, cancel, err
}

// Park puts a run in awaiting_input and releases the lease.
func (s *Store) Park(ctx context.Context, run uuid.UUID, runner, reason string) error {
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input', status_reason = $3,
		awaiting_since = COALESCE(awaiting_since, now()), lease_owner = NULL, lease_until = NULL, updated_at = now()
		WHERE id = $1 AND lease_owner = $2`, run, runner, reason)
	return err
}

// Finish ends a run in a terminal state.
func (s *Store) Finish(ctx context.Context, run uuid.UUID, runner, status, reason string) error {
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = $3, status_reason = $4, finished_at = now(),
		lease_owner = NULL, lease_until = NULL, updated_at = now()
		WHERE id = $1 AND (lease_owner = $2 OR $2 = '')`, run, runner, status, reason)
	return err
}

// SetRunFields updates the period / model facts the runner learns.
func (s *Store) SetRunFields(ctx context.Context, run uuid.UUID, period *string, model *string, selfHosted *bool) error {
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET period = COALESCE($2, period),
		model_used = COALESCE($3, model_used), model_self_hosted = COALESCE($4, model_self_hosted), updated_at = now()
		WHERE id = $1`, run, period, model, selfHosted)
	return err
}

// SetFormats replaces the run's output set.
func (s *Store) SetFormats(ctx context.Context, q Querier, run uuid.UUID, formats []string) error {
	b, _ := json.Marshal(formats)
	_, err := q.Exec(ctx, `UPDATE agent_market.agent_runs SET formats = $2, updated_at = now() WHERE id = $1`, run, b)
	return err
}

// ── steps ────────────────────────────────────────────────────────────────

const stepCols = `id, run_id, seq, name, kind, status, idempotency_key, input, output, script_id, model_id, error, attempt,
	credits::float8, started_at, finished_at, duration_ms`

func scanStep(r pgx.Row) (Step, error) {
	var x Step
	err := r.Scan(&x.ID, &x.RunID, &x.Seq, &x.Name, &x.Kind, &x.Status, &x.IdempotencyKey, &x.Input, &x.Output,
		&x.ScriptID, &x.ModelID, &x.Error, &x.Attempt, &x.Credits, &x.StartedAt, &x.FinishedAt, &x.DurationMS)
	return x, err
}

// StepByKey returns the step with this idempotency key, if any.
func (s *Store) StepByKey(ctx context.Context, run uuid.UUID, key string) (Step, error) {
	x, err := scanStep(s.Pool.QueryRow(ctx, `SELECT `+stepCols+` FROM agent_market.agent_run_steps
		WHERE run_id = $1 AND idempotency_key = $2`, run, key))
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	return x, err
}

// BeginStep starts (or restarts) the step with this key. A step that
// already succeeded is returned as-is with done=true: it is never re-run.
func (s *Store) BeginStep(ctx context.Context, run uuid.UUID, name, kind, key string, input json.RawMessage) (Step, bool, error) {
	x, err := s.StepByKey(ctx, run, key)
	if err == nil {
		if x.Status == "succeeded" || x.Status == "skipped" {
			return x, true, nil
		}
		x, err = scanStep(s.Pool.QueryRow(ctx, `UPDATE agent_market.agent_run_steps SET status = 'running',
			attempt = attempt + 1, started_at = now(), finished_at = NULL, duration_ms = NULL, error = ''
			WHERE id = $1 RETURNING `+stepCols, x.ID))
		return x, false, err
	}
	if !errors.Is(err, ErrNotFound) {
		return x, false, err
	}
	x, err = scanStep(s.Pool.QueryRow(ctx, `
		INSERT INTO agent_market.agent_run_steps (run_id, seq, name, kind, status, idempotency_key, input)
		VALUES ($1, COALESCE((SELECT max(seq) FROM agent_market.agent_run_steps WHERE run_id = $1), 0) + 1, $2, $3, 'running', $4, $5)
		ON CONFLICT (run_id, idempotency_key) DO UPDATE SET status = 'running', attempt = agent_market.agent_run_steps.attempt + 1
		RETURNING `+stepCols, run, name, kind, key, jsonOr(input, "{}")))
	return x, false, err
}

// FinishStep records the outcome.
func (s *Store) FinishStep(ctx context.Context, id uuid.UUID, status string, output json.RawMessage, errText, script, model string, credits float64) error {
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_run_steps SET status = $2, output = $3, error = $4,
		script_id = $5, model_id = $6, credits = $7, finished_at = now(),
		duration_ms = (extract(epoch FROM now() - started_at) * 1000)::bigint WHERE id = $1`,
		id, status, jsonOr(output, "{}"), errText, script, model, credits)
	return err
}

// AwaitStep marks a clarification step as waiting on a person.
func (s *Store) AwaitStep(ctx context.Context, id uuid.UUID) error {
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_run_steps SET status = 'awaiting' WHERE id = $1`, id)
	return err
}

// AwaitingSteps lists a run's steps still waiting on a person.
func (s *Store) AwaitingSteps(ctx context.Context, run uuid.UUID) ([]Step, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+stepCols+` FROM agent_market.agent_run_steps
		WHERE run_id = $1 AND status = 'awaiting' ORDER BY seq`, run)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Step
	for rows.Next() {
		x, err := scanStep(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

// DeleteAnswers drops answers (e.g. about a file that was replaced).
func (s *Store) DeleteAnswers(ctx context.Context, run uuid.UUID, keys []string) error {
	if len(keys) == 0 {
		return nil
	}
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET answers = answers - $2::text[], updated_at = now() WHERE id = $1`, run, keys)
	return err
}

// ListSteps returns a run's steps in order.
func (s *Store) ListSteps(ctx context.Context, run uuid.UUID) ([]Step, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+stepCols+` FROM agent_market.agent_run_steps WHERE run_id = $1 ORDER BY seq`, run)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Step
	for rows.Next() {
		x, err := scanStep(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

// ── cards ────────────────────────────────────────────────────────────────

const cardCols = `id, run_id, install_id, tenant_id, user_id, moment, seq, kind, blocking, supersedes, questions, status,
	answer, answer_text, answered_via, answered_at, attempts, last_error, created_at`

func scanCard(r pgx.Row) (Card, error) {
	var c Card
	err := r.Scan(&c.ID, &c.RunID, &c.InstallID, &c.TenantID, &c.UserID, &c.Moment, &c.Seq, &c.Kind, &c.Blocking,
		&c.Supersedes, &c.Questions, &c.Status, &c.Answer, &c.AnswerText, &c.AnsweredVia, &c.AnsweredAt, &c.Attempts,
		&c.LastError, &c.CreatedAt)
	return c, err
}

// CreateCard adds a card to a run.
func (s *Store) CreateCard(ctx context.Context, q Querier, c Card) (Card, error) {
	if c.ID == uuid.Nil {
		c.ID = uuid.New()
	}
	return scanCard(q.QueryRow(ctx, `
		INSERT INTO agent_market.agent_run_clarifications (id, run_id, install_id, tenant_id, user_id, moment, seq, kind,
			blocking, supersedes, questions, status, attempts)
		VALUES ($1, $2, $3, $4, $5, $6,
			COALESCE((SELECT max(seq) FROM agent_market.agent_run_clarifications WHERE run_id = $2), 0) + 1,
			$7, $8, $9, $10, 'open', $11)
		RETURNING `+cardCols, c.ID, c.RunID, c.InstallID, c.TenantID, c.UserID, c.Moment, c.Kind, c.Blocking, c.Supersedes,
		c.Questions, c.Attempts))
}

// GetCard scopes by owner.
func (s *Store) GetCard(ctx context.Context, tenant, user, id uuid.UUID) (Card, error) {
	c, err := scanCard(s.Pool.QueryRow(ctx, `SELECT `+cardCols+` FROM agent_market.agent_run_clarifications
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`, id, tenant, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return c, ErrNotFound
	}
	return c, err
}

// CardByID is the runner's read.
func (s *Store) CardByID(ctx context.Context, id uuid.UUID) (Card, error) {
	c, err := scanCard(s.Pool.QueryRow(ctx, `SELECT `+cardCols+` FROM agent_market.agent_run_clarifications WHERE id = $1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return c, ErrNotFound
	}
	return c, err
}

// RunCards lists a run's cards in order.
func (s *Store) RunCards(ctx context.Context, run uuid.UUID) ([]Card, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+cardCols+` FROM agent_market.agent_run_clarifications WHERE run_id = $1 ORDER BY seq`, run)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Card
	for rows.Next() {
		c, err := scanCard(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// CloseCard moves an OPEN (or interpreting) card to a final status in one
// conditional update. Exactly one caller wins; a second submit of the same
// card — or of a superseded card — changes nothing and gets the recorded row.
func (s *Store) CloseCard(ctx context.Context, q Querier, id uuid.UUID, status string, answer json.RawMessage, via string,
	by *uuid.UUID) (Card, bool, error) {
	c, err := scanCard(q.QueryRow(ctx, `
		UPDATE agent_market.agent_run_clarifications SET status = $2, answer = $3, answered_via = $4, answered_by = $5,
			answered_at = now()
		WHERE id = $1 AND status IN ('open', 'interpreting') RETURNING `+cardCols, id, status, answer, via, by))
	if errors.Is(err, pgx.ErrNoRows) {
		cur, err2 := scanCard(q.QueryRow(ctx, `SELECT `+cardCols+` FROM agent_market.agent_run_clarifications WHERE id = $1`, id))
		return cur, false, err2
	}
	return c, err == nil, err
}

// MarkInterpreting parks free text on an open card for the runner to read.
func (s *Store) MarkInterpreting(ctx context.Context, q Querier, id uuid.UUID, text string, partial json.RawMessage) (Card, bool, error) {
	c, err := scanCard(q.QueryRow(ctx, `
		UPDATE agent_market.agent_run_clarifications SET status = 'interpreting', answer_text = $2, answer = $3
		WHERE id = $1 AND status = 'open' RETURNING `+cardCols, id, text, partial))
	if errors.Is(err, pgx.ErrNoRows) {
		cur, err2 := scanCard(q.QueryRow(ctx, `SELECT `+cardCols+` FROM agent_market.agent_run_clarifications WHERE id = $1`, id))
		return cur, false, err2
	}
	return c, err == nil, err
}

// ReopenForReask returns an interpreting card to open with one more attempt counted.
func (s *Store) ReopenForReask(ctx context.Context, id uuid.UUID, errText string) error {
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_run_clarifications SET status = 'open', attempts = attempts + 1,
		last_error = $2, answer_text = NULL WHERE id = $1 AND status = 'interpreting'`, id, errText)
	return err
}

// SupersedeOpenCards closes a run's open cards as superseded or cancelled.
// Each card's thread item moves with its row: the web renders cards from the
// thread, so a row closed on its own would stay clickable on screen.
func (s *Store) SupersedeOpenCards(ctx context.Context, q Querier, run uuid.UUID, status string) ([]uuid.UUID, error) {
	return s.closeOpenCards(ctx, q, `run_id = $1`, run, status)
}

// SupersedeInstallCards closes every card still open on an install's runs —
// when a new round starts, the older rounds' questions are moot.
func (s *Store) SupersedeInstallCards(ctx context.Context, q Querier, install uuid.UUID, status string) ([]uuid.UUID, error) {
	return s.closeOpenCards(ctx, q, `install_id = $1`, install, status)
}

func (s *Store) closeOpenCards(ctx context.Context, q Querier, where string, id uuid.UUID, status string) ([]uuid.UUID, error) {
	rows, err := q.Query(ctx, `UPDATE agent_market.agent_run_clarifications SET status = $2
		WHERE `+where+` AND status IN ('open', 'interpreting') RETURNING id, install_id`, id, status)
	if err != nil {
		return nil, err
	}
	var ids, installs []uuid.UUID
	for rows.Next() {
		var cid, inst uuid.UUID
		if err := rows.Scan(&cid, &inst); err != nil {
			rows.Close()
			return nil, err
		}
		ids = append(ids, cid)
		installs = append(installs, inst)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for i, cid := range ids {
		if _, err := q.Exec(ctx, `UPDATE agent_market.agent_thread_items SET payload = payload || jsonb_build_object('status', $3::text),
			rev = nextval('agent_market.thread_rev'), updated_at = now()
			WHERE install_id = $1 AND kind = 'card' AND payload->>'card_id' = $2`, installs[i], cid.String(), status); err != nil {
			return ids, err
		}
	}
	return ids, nil
}

// ── artifacts ────────────────────────────────────────────────────────────

const artCols = `id, run_id, install_id, format, kind, name, version, storage_key, size, sha256, theme_id, theme_version,
	citations, validation, chart_bindings, produced_by_step, created_at`

func scanArt(r pgx.Row) (Artifact, error) {
	var a Artifact
	err := r.Scan(&a.ID, &a.RunID, &a.InstallID, &a.Format, &a.Kind, &a.Name, &a.Version, &a.StorageKey, &a.Size, &a.SHA256,
		&a.ThemeID, &a.ThemeVersion, &a.Citations, &a.Validation, &a.ChartBindings, &a.ProducedByStep, &a.CreatedAt)
	return a, err
}

// NextVersion is the version a new artifact of this format gets on a run.
func (s *Store) NextVersion(ctx context.Context, run uuid.UUID, format string) (int, error) {
	var v int
	err := s.Pool.QueryRow(ctx, `SELECT COALESCE(max(version), 0) + 1 FROM agent_market.agent_output_artifacts
		WHERE run_id = $1 AND format = $2`, run, format).Scan(&v)
	return v, err
}

// InsertArtifact is idempotent on (producing step, format): a publish step
// retried after a crash never creates a second artifact.
func (s *Store) InsertArtifact(ctx context.Context, a Artifact, tenant, user uuid.UUID) (Artifact, bool, error) {
	x, err := scanArt(s.Pool.QueryRow(ctx, `
		INSERT INTO agent_market.agent_output_artifacts (run_id, install_id, tenant_id, user_id, format, kind, name, version,
			storage_key, size, sha256, theme_id, theme_version, citations, validation, chart_bindings, produced_by_step)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
		ON CONFLICT (produced_by_step, format) DO NOTHING
		RETURNING `+artCols, a.RunID, a.InstallID, tenant, user, a.Format, a.Kind, a.Name, a.Version, a.StorageKey, a.Size,
		a.SHA256, a.ThemeID, a.ThemeVersion, jsonOr(a.Citations, "[]"), jsonOr(a.Validation, "{}"),
		jsonOr(a.ChartBindings, "[]"), a.ProducedByStep))
	if errors.Is(err, pgx.ErrNoRows) {
		cur, err2 := scanArt(s.Pool.QueryRow(ctx, `SELECT `+artCols+` FROM agent_market.agent_output_artifacts
			WHERE produced_by_step = $1 AND format = $2`, a.ProducedByStep, a.Format))
		return cur, false, err2
	}
	return x, err == nil, err
}

// RunArtifacts lists a run's artifacts, newest version first per format.
func (s *Store) RunArtifacts(ctx context.Context, run uuid.UUID) ([]Artifact, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+artCols+` FROM agent_market.agent_output_artifacts WHERE run_id = $1
		ORDER BY format, version DESC`, run)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Artifact
	for rows.Next() {
		a, err := scanArt(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// GetArtifact scopes by owner.
func (s *Store) GetArtifact(ctx context.Context, tenant, user, id uuid.UUID) (Artifact, error) {
	a, err := scanArt(s.Pool.QueryRow(ctx, `SELECT `+artCols+` FROM agent_market.agent_output_artifacts
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`, id, tenant, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return a, ErrNotFound
	}
	return a, err
}

// ── thread ───────────────────────────────────────────────────────────────

const itemCols = `id, install_id, run_id, kind, payload, client_id, rev, created_at, updated_at`

func scanItem(r pgx.Row) (ThreadItem, error) {
	var t ThreadItem
	err := r.Scan(&t.ID, &t.InstallID, &t.RunID, &t.Kind, &t.Payload, &t.ClientID, &t.Rev, &t.CreatedAt, &t.UpdatedAt)
	return t, err
}

// AddItem appends to the workspace thread. With a client id it is
// idempotent: a resent message returns the first item.
func (s *Store) AddItem(ctx context.Context, q Querier, install, tenant, user uuid.UUID, run *uuid.UUID, kind string,
	payload any, clientID *string) (ThreadItem, bool, error) {
	b, err := json.Marshal(payload)
	if err != nil {
		return ThreadItem{}, false, err
	}
	t, err := scanItem(q.QueryRow(ctx, `
		INSERT INTO agent_market.agent_thread_items (install_id, tenant_id, user_id, run_id, kind, payload, client_id)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (install_id, client_id) WHERE client_id IS NOT NULL DO NOTHING
		RETURNING `+itemCols, install, tenant, user, run, kind, b, clientID))
	if errors.Is(err, pgx.ErrNoRows) && clientID != nil {
		cur, err2 := scanItem(q.QueryRow(ctx, `SELECT `+itemCols+` FROM agent_market.agent_thread_items
			WHERE install_id = $1 AND client_id = $2`, install, *clientID))
		return cur, false, err2
	}
	return t, err == nil, err
}

// UpdateItem replaces an item's payload and moves it to the stream's head.
func (s *Store) UpdateItem(ctx context.Context, id uuid.UUID, payload any) error {
	b, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	_, err = s.Pool.Exec(ctx, `UPDATE agent_market.agent_thread_items SET payload = $2,
		rev = nextval('agent_market.thread_rev'), updated_at = now() WHERE id = $1`, id, b)
	return err
}

// PatchItem merges keys into an item's payload (e.g. a card's status).
func (s *Store) PatchItem(ctx context.Context, q Querier, id uuid.UUID, patch map[string]any) error {
	b, _ := json.Marshal(patch)
	_, err := q.Exec(ctx, `UPDATE agent_market.agent_thread_items SET payload = payload || $2::jsonb,
		rev = nextval('agent_market.thread_rev'), updated_at = now() WHERE id = $1`, id, b)
	return err
}

// ItemsAfter returns the thread from a cursor (the event stream's resume point).
func (s *Store) ItemsAfter(ctx context.Context, install uuid.UUID, after int64, limit int) ([]ThreadItem, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+itemCols+` FROM agent_market.agent_thread_items
		WHERE install_id = $1 AND rev > $2 ORDER BY rev LIMIT $3`, install, after, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ThreadItem
	for rows.Next() {
		t, err := scanItem(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

// ItemFor finds the thread item of a card or a run's progress/outputs.
func (s *Store) ItemFor(ctx context.Context, install uuid.UUID, kind, field, value string) (ThreadItem, error) {
	t, err := scanItem(s.Pool.QueryRow(ctx, `SELECT `+itemCols+` FROM agent_market.agent_thread_items
		WHERE install_id = $1 AND kind = $2 AND payload->>$3 = $4 ORDER BY rev DESC LIMIT 1`, install, kind, field, value))
	if errors.Is(err, pgx.ErrNoRows) {
		return t, ErrNotFound
	}
	return t, err
}

// PendingUserTexts lists composer messages no job has handled yet.
func (s *Store) PendingUserTexts(ctx context.Context, install uuid.UUID) ([]ThreadItem, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+itemCols+` FROM agent_market.agent_thread_items
		WHERE install_id = $1 AND kind = 'user_text' AND payload->>'status' = 'pending' ORDER BY rev`, install)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ThreadItem
	for rows.Next() {
		t, err := scanItem(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

// ── budget ───────────────────────────────────────────────────────────────

// SpentThisMonth is the install's credits used since the first of the month (UTC).
func (s *Store) SpentThisMonth(ctx context.Context, install uuid.UUID) (float64, error) {
	var v float64
	err := s.Pool.QueryRow(ctx, `SELECT COALESCE(sum(credits), 0)::float8 FROM agent_market.agent_budget_ledger
		WHERE install_id = $1 AND created_at >= date_trunc('month', now())`, install).Scan(&v)
	return v, err
}

// Charge records a step's credits once (idempotent on run + step key).
func (s *Store) Charge(ctx context.Context, install, run uuid.UUID, stepKey string, credits float64, model string, detail any) error {
	b, _ := json.Marshal(detail)
	tag, err := s.Pool.Exec(ctx, `INSERT INTO agent_market.agent_budget_ledger (install_id, run_id, step_key, credits, model, detail)
		VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (run_id, step_key) DO NOTHING`, install, run, stepKey, credits, model, b)
	if err != nil || tag.RowsAffected() == 0 {
		return err
	}
	_, err = s.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET credits_spent = credits_spent + $2 WHERE id = $1`, run, credits)
	return err
}

// ── runner heartbeat ─────────────────────────────────────────────────────

// Heartbeat records a runner as alive with its capabilities.
func (s *Store) Heartbeat(ctx context.Context, runner, version string, started time.Time, caps json.RawMessage, running, slots int) error {
	_, err := s.Pool.Exec(ctx, `
		INSERT INTO agent_market.runner_heartbeats (runner_id, version, started_at, seen_at, capabilities, running, slots)
		VALUES ($1, $2, $3, now(), $4, $5, $6)
		ON CONFLICT (runner_id) DO UPDATE SET version = EXCLUDED.version, seen_at = now(),
			capabilities = EXCLUDED.capabilities, running = EXCLUDED.running, slots = EXCLUDED.slots`,
		runner, version, started, caps, running, slots)
	return err
}

// RunnerStatus is the newest heartbeat.
type RunnerStatus struct {
	RunnerID     string          `json:"runner_id"`
	Version      string          `json:"version"`
	SeenAt       time.Time       `json:"seen_at"`
	Capabilities json.RawMessage `json:"capabilities"`
	Running      int             `json:"running"`
	Slots        int             `json:"slots"`
	Healthy      bool            `json:"healthy"`
}

// LatestRunner returns the most recent heartbeat.
func (s *Store) LatestRunner(ctx context.Context, staleAfter time.Duration) (RunnerStatus, error) {
	var r RunnerStatus
	err := s.Pool.QueryRow(ctx, `SELECT runner_id, version, seen_at, capabilities, running, slots
		FROM agent_market.runner_heartbeats ORDER BY seen_at DESC LIMIT 1`).
		Scan(&r.RunnerID, &r.Version, &r.SeenAt, &r.Capabilities, &r.Running, &r.Slots)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, ErrNotFound
	}
	r.Healthy = err == nil && time.Since(r.SeenAt) < staleAfter
	return r, err
}
