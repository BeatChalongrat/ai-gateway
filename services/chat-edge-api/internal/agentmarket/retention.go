package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// ── Retention (AM2 Phase G) ──────────────────────────────────────────────
//
// What Agent Market keeps, and for how long — matched to the core, not
// invented: an upload never attached to a run goes after ~48 hours; a failed
// or cancelled run's scratch (its step blobs: parsed documents, computed
// tables, page images) after ~7 days, the run's rows staying as its record;
// a removed install is erased 30 days after removal, like a deleted chat;
// a completed run's evidence stays while its install does, like a live
// chat; the duplicate index expires by its own retention (docindex.go).
// The runner runs the pass every hour.

// RetentionCounts is one pass's outcome.
type RetentionCounts struct {
	StagedFiles     int `json:"staged_files"`
	ScratchRuns     int `json:"scratch_runs"`
	ScratchObjects  int `json:"scratch_objects"`
	RemovedInstalls int `json:"removed_installs"`
	RemovedObjects  int `json:"removed_objects"`
	IndexRows       int `json:"index_rows"`
}

// Retention runs one pass.
func (s *Store) Retention(ctx context.Context, files storage.Backend, staged, scratch, removed time.Duration) (RetentionCounts, error) {
	var c RetentionCounts
	n, err := s.PurgeExpiredIndex(ctx)
	if err != nil {
		return c, err
	}
	c.IndexRows = int(n)

	// uploads staged and never attached to a run
	type fileKey struct {
		id  uuid.UUID
		key string
	}
	var stagedFiles []fileKey
	rows, err := s.Pool.Query(ctx, `SELECT id, storage_key FROM agent_market.agent_files
		WHERE status = 'staged' AND run_id IS NULL AND created_at < now() - make_interval(secs => $1::double precision)
		LIMIT 500`, staged.Seconds())
	if err != nil {
		return c, err
	}
	for rows.Next() {
		var f fileKey
		if rows.Scan(&f.id, &f.key) == nil {
			stagedFiles = append(stagedFiles, f)
		}
	}
	rows.Close()
	for _, f := range stagedFiles {
		if err := files.Delete(ctx, f.key); err != nil && !errors.Is(err, storage.ErrNotFound) {
			continue // the row stays until its object is gone
		}
		tag, err := s.Pool.Exec(ctx, `DELETE FROM agent_market.agent_files WHERE id = $1 AND status = 'staged' AND run_id IS NULL`, f.id)
		if err == nil {
			c.StagedFiles += int(tag.RowsAffected())
		}
	}

	// a failed or cancelled run's scratch
	scratchRuns, err := s.ids(ctx, `SELECT id FROM agent_market.agent_runs
		WHERE status IN ('failed', 'cancelled') AND scratch_purged_at IS NULL
		  AND finished_at < now() - make_interval(secs => $1::double precision) LIMIT 200`, scratch.Seconds())
	if err != nil {
		return c, err
	}
	for _, id := range scratchRuns {
		keys, err := s.runObjectKeys(ctx, []uuid.UUID{id}, true)
		if err != nil {
			return c, err
		}
		for k := range keys {
			if err := files.Delete(ctx, k); err == nil {
				c.ScratchObjects++
			}
		}
		if _, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET scratch_purged_at = now() WHERE id = $1`, id); err != nil {
			return c, err
		}
		c.ScratchRuns++
	}

	// removed installs, erased like deleted chats
	gone, err := s.ids(ctx, `SELECT id FROM agent_market.agent_installs
		WHERE status = 'removed' AND removed_at < now() - make_interval(secs => $1::double precision) LIMIT 50`, removed.Seconds())
	if err != nil {
		return c, err
	}
	if len(gone) > 0 {
		c.RemovedObjects, c.RemovedInstalls, err = s.PurgeInstalls(ctx, files, gone)
	}
	return c, err
}

func (s *Store) ids(ctx context.Context, q string, args ...any) ([]uuid.UUID, error) {
	rows, err := s.Pool.Query(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []uuid.UUID
	for rows.Next() {
		var id uuid.UUID
		if rows.Scan(&id) == nil {
			out = append(out, id)
		}
	}
	return out, rows.Err()
}

// runObjectKeys are the objects runs wrote: with scratchOnly, only their own
// step blobs (parsed documents, computed tables, page images); else also
// their artifacts. An upload or another run's object named in an output is
// never included.
func (s *Store) runObjectKeys(ctx context.Context, runs []uuid.UUID, scratchOnly bool) (map[string]bool, error) {
	keys := map[string]bool{}
	rows, err := s.Pool.Query(ctx, `SELECT run_id, output FROM agent_market.agent_run_steps WHERE run_id = ANY($1) AND output IS NOT NULL`, runs)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var run uuid.UUID
		var out json.RawMessage
		if rows.Scan(&run, &out) != nil {
			continue
		}
		var v any
		if json.Unmarshal(out, &v) != nil {
			continue
		}
		found := map[string]bool{}
		collectKeys(v, found)
		for k := range found {
			own := "/runs/" + run.String() + "/"
			if strings.Contains(k, own+"steps/") || (!scratchOnly && strings.Contains(k, own)) {
				keys[k] = true
			}
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if !scratchOnly {
		arts, err := s.Pool.Query(ctx, `SELECT storage_key FROM agent_market.agent_output_artifacts WHERE run_id = ANY($1)`, runs)
		if err != nil {
			return nil, err
		}
		for arts.Next() {
			var k string
			if arts.Scan(&k) == nil && k != "" {
				keys[k] = true
			}
		}
		arts.Close()
	}
	return keys, nil
}

// PurgeInstalls erases installs completely: objects first (uploads,
// artifacts, step blobs), then the rows (the install cascade reaches runs,
// steps, cards, artifacts, approvals, exceptions, files, thread, defaults and
// the duplicate index; the credit ledger goes by install). Returns objects
// deleted and installs erased.
func (s *Store) PurgeInstalls(ctx context.Context, files storage.Backend, installs []uuid.UUID) (int, int, error) {
	keys := map[string]bool{}
	rows, err := s.Pool.Query(ctx, `SELECT storage_key FROM agent_market.agent_files WHERE install_id = ANY($1)`, installs)
	if err != nil {
		return 0, 0, err
	}
	for rows.Next() {
		var k string
		if rows.Scan(&k) == nil && k != "" {
			keys[k] = true
		}
	}
	rows.Close()
	runs, err := s.ids(ctx, `SELECT id FROM agent_market.agent_runs WHERE install_id = ANY($1)`, installs)
	if err != nil {
		return 0, 0, err
	}
	if len(runs) > 0 {
		rk, err := s.runObjectKeys(ctx, runs, false)
		if err != nil {
			return 0, 0, err
		}
		for k := range rk {
			keys[k] = true
		}
	}
	objects := 0
	for k := range keys {
		if err := files.Delete(ctx, k); err == nil {
			objects++
		}
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return objects, 0, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck // rollback after commit is a no-op
	if _, err := tx.Exec(ctx, `DELETE FROM agent_market.agent_budget_ledger WHERE install_id = ANY($1)`, installs); err != nil {
		return objects, 0, err
	}
	// AM3: reports about these installs' runs go with them (no key: they may be a share viewer's)
	if _, err := tx.Exec(ctx, `DELETE FROM agent_market.problem_reports WHERE install_id = ANY($1)`, installs); err != nil {
		return objects, 0, err
	}
	tag, err := tx.Exec(ctx, `DELETE FROM agent_market.agent_installs WHERE id = ANY($1)`, installs)
	if err != nil {
		return objects, 0, err
	}
	return objects, int(tag.RowsAffected()), tx.Commit(ctx)
}
