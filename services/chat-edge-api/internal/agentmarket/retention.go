package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// ── Retention (AM2 Phase G; AM4: an accounting archive) ──────────────────
//
// What Agent Market keeps, and for how long. AM2 wrote this for throwaway
// output; AM4 keeps it as what it became — the organisation's accounting
// record of what was computed from which documents:
//
//   - A finished round's evidence (the files sent in, the files made, the
//     computed table behind them) is kept for the organisation's retention
//     (org_settings.retention_years, default 5 — Thai practice keeps
//     accounting records and their supporting documents at least 5 years),
//     counted from the round's finish, and never deleted silently: before it
//     ends the admins' page lists what will expire, and an earlier removal is
//     an admin's decision with a reason, told to the round's owner.
//   - A failed or cancelled round's data (its uploads, its step blobs): ~7
//     days. The run's row stays as the record that it happened.
//   - A file dropped but never used in any round: ~30 days.
//   - A removed install: after ~30 days its conversation and settings go;
//     its finished rounds' evidence stays until their retention ends.
//   - The duplicate index expires by its own retention (docindex.go).
//
// Artifacts are versioned and never overwritten; that non-alteration is
// what makes them worth keeping. This is not the company's document
// archive: the originals stay wherever the company keeps them.
// The runner runs the pass every hour.

// RetentionCounts is one pass's outcome.
type RetentionCounts struct {
	StagedFiles     int `json:"staged_files"`
	ScratchRuns     int `json:"scratch_runs"`
	ScratchObjects  int `json:"scratch_objects"`
	RemovedInstalls int `json:"removed_installs"`
	RemovedObjects  int `json:"removed_objects"`
	IndexRows       int `json:"index_rows"`
	ExpiredRuns     int `json:"expired_runs"`
}

// DefaultRetentionYears is the organisation's default (org_settings.retention_years).
const DefaultRetentionYears = 5

// Retention runs one pass. staged: a file never used in a round; scratch: a
// failed or cancelled round's data; removed: a removed install's conversation.
func (s *Store) Retention(ctx context.Context, files storage.Backend, staged, scratch, removed time.Duration) (RetentionCounts, error) {
	var c RetentionCounts
	n, err := s.PurgeExpiredIndex(ctx)
	if err != nil {
		return c, err
	}
	c.IndexRows = int(n)

	// files dropped and never used in a round: staged and never attached, or
	// attached to a round that ended without using them
	type fileKey struct {
		id  uuid.UUID
		key string
	}
	var unused []fileKey
	rows, err := s.Pool.Query(ctx, `SELECT f.id, f.storage_key FROM agent_market.agent_files f
		LEFT JOIN agent_market.agent_runs r ON r.id = f.run_id
		WHERE f.created_at < now() - make_interval(secs => $1::double precision)
		  AND ((f.status = 'staged' AND f.run_id IS NULL)
		    OR (f.status IN ('pending_decision', 'next_round') AND r.status IN ('succeeded', 'failed', 'cancelled')))
		LIMIT 500`, staged.Seconds())
	if err != nil {
		return c, err
	}
	for rows.Next() {
		var f fileKey
		if rows.Scan(&f.id, &f.key) == nil {
			unused = append(unused, f)
		}
	}
	rows.Close()
	for _, f := range unused {
		if err := files.Delete(ctx, f.key); err != nil && !errors.Is(err, storage.ErrNotFound) {
			continue // the row stays until its object is gone
		}
		tag, err := s.Pool.Exec(ctx, `DELETE FROM agent_market.agent_files WHERE id = $1`, f.id)
		if err == nil {
			c.StagedFiles += int(tag.RowsAffected())
		}
	}

	// a failed or cancelled round's data: its step blobs and its uploads
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
		up, err := s.runUploadKeys(ctx, id)
		if err != nil {
			return c, err
		}
		for _, k := range up {
			keys[k] = true
		}
		for k := range keys {
			if err := files.Delete(ctx, k); err == nil {
				c.ScratchObjects++
			}
		}
		if _, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_files SET status = 'removed' WHERE run_id = $1`, id); err != nil {
			return c, err
		}
		if _, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET scratch_purged_at = now() WHERE id = $1`, id); err != nil {
			return c, err
		}
		c.ScratchRuns++
	}

	// finished rounds whose retention has ended: the organisation's own number of years
	expired, err := s.ids(ctx, `SELECT r.id FROM agent_market.agent_runs r
		JOIN agent_market.agent_installs i ON i.id = r.install_id
		LEFT JOIN agent_market.org_settings o ON o.org_id = i.org_id
		WHERE r.kind = 'run' AND r.status = 'succeeded' AND i.status <> 'catalog'
		  AND r.finished_at < now() - make_interval(years => COALESCE(o.retention_years, $1))
		LIMIT 100`, DefaultRetentionYears)
	if err != nil {
		return c, err
	}
	for _, id := range expired {
		objs, err := s.purgeRuns(ctx, files, []uuid.UUID{id})
		if err != nil {
			return c, err
		}
		c.RemovedObjects += objs
		c.ExpiredRuns++
	}

	// removed installs: the conversation and settings after `removed`; the
	// whole install once no finished round's evidence is left in it
	gone, err := s.ids(ctx, `SELECT id FROM agent_market.agent_installs
		WHERE status = 'removed' AND removed_at < now() - make_interval(secs => $1::double precision) LIMIT 50`, removed.Seconds())
	if err != nil {
		return c, err
	}
	var empty []uuid.UUID
	for _, id := range gone {
		kept, err := s.ids(ctx, `SELECT id FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'run' AND status = 'succeeded'
			AND evidence_removed_at IS NULL`, id)
		if err != nil {
			return c, err
		}
		if len(kept) == 0 {
			empty = append(empty, id)
			continue
		}
		objs, err := s.purgePersonal(ctx, files, []uuid.UUID{id})
		if err != nil {
			return c, err
		}
		c.RemovedObjects += objs
	}
	if len(empty) > 0 {
		objs, installs, err := s.PurgeInstalls(ctx, files, empty)
		c.RemovedObjects += objs
		c.RemovedInstalls += installs
		if err != nil {
			return c, err
		}
	}
	return c, nil
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

func (s *Store) runUploadKeys(ctx context.Context, run uuid.UUID) ([]string, error) {
	rows, err := s.Pool.Query(ctx, `SELECT storage_key FROM agent_market.agent_files WHERE run_id = $1 AND status <> 'removed'`, run)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var k string
		if rows.Scan(&k) == nil && k != "" {
			out = append(out, k)
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

// runsAndChildren are the runs plus the small jobs hanging off them (renders,
// replies, sign-offs) — their files are part of the same record.
func (s *Store) runsAndChildren(ctx context.Context, runs []uuid.UUID) ([]uuid.UUID, error) {
	return s.ids(ctx, `SELECT id FROM agent_market.agent_runs WHERE id = ANY($1) OR parent_run_id = ANY($1)`, runs)
}

// purgeRuns erases rounds completely (their objects, then their rows) —
// the end of their retention.
func (s *Store) purgeRuns(ctx context.Context, files storage.Backend, runs []uuid.UUID) (int, error) {
	all, err := s.runsAndChildren(ctx, runs)
	if err != nil || len(all) == 0 {
		return 0, err
	}
	keys, err := s.runObjectKeys(ctx, all, false)
	if err != nil {
		return 0, err
	}
	for _, id := range all {
		up, err := s.runUploadKeys(ctx, id)
		if err != nil {
			return 0, err
		}
		for _, k := range up {
			keys[k] = true
		}
	}
	n := 0
	for k := range keys {
		if err := files.Delete(ctx, k); err == nil {
			n++
		}
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return n, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	for _, q := range []string{
		`DELETE FROM agent_market.agent_thread_items WHERE run_id = ANY($1)`,
		`DELETE FROM agent_market.agent_files WHERE run_id = ANY($1)`,
		`DELETE FROM agent_market.agent_budget_ledger WHERE run_id = ANY($1)`,
		`DELETE FROM agent_market.problem_reports WHERE run_id = ANY($1)`,
		`DELETE FROM agent_market.agent_runs WHERE parent_run_id = ANY($1)`,
		`DELETE FROM agent_market.agent_runs WHERE id = ANY($1)`,
	} {
		if _, err := tx.Exec(ctx, q, all); err != nil {
			return n, err
		}
	}
	return n, tx.Commit(ctx)
}

// purgePersonal erases what belongs to the person, not to the organisation's
// books: the conversation (threads and their items that are not a run's
// record), the install's settings and remembered answers, files never used,
// failed or cancelled rounds. A finished round's evidence stays.
func (s *Store) purgePersonal(ctx context.Context, files storage.Backend, installs []uuid.UUID) (int, error) {
	n := 0
	lost, err := s.ids(ctx, `SELECT id FROM agent_market.agent_runs WHERE install_id = ANY($1) AND kind = 'run'
		AND status IN ('failed', 'cancelled')`, installs)
	if err != nil {
		return 0, err
	}
	if len(lost) > 0 {
		k, err := s.purgeRuns(ctx, files, lost)
		n += k
		if err != nil {
			return n, err
		}
	}
	rows, err := s.Pool.Query(ctx, `SELECT id, storage_key FROM agent_market.agent_files WHERE install_id = ANY($1)
		AND (run_id IS NULL OR status IN ('pending_decision', 'next_round', 'staged'))`, installs)
	if err != nil {
		return n, err
	}
	type fk struct {
		id  uuid.UUID
		key string
	}
	var fl []fk
	for rows.Next() {
		var f fk
		if rows.Scan(&f.id, &f.key) == nil {
			fl = append(fl, f)
		}
	}
	rows.Close()
	for _, f := range fl {
		if err := files.Delete(ctx, f.key); err == nil || errors.Is(err, storage.ErrNotFound) {
			n++
			_, _ = s.Pool.Exec(ctx, `DELETE FROM agent_market.agent_files WHERE id = $1`, f.id)
		}
	}
	for _, q := range []string{
		// the conversation: everything but a finished round's own progress and files card
		`DELETE FROM agent_market.agent_thread_items WHERE install_id = ANY($1)
			AND NOT (kind IN ('progress', 'outputs') AND run_id IN (SELECT id FROM agent_market.agent_runs WHERE status = 'succeeded'))`,
		`UPDATE agent_market.agent_thread_items SET thread_id = NULL WHERE install_id = ANY($1)`,
		`UPDATE agent_market.agent_runs SET thread_id = NULL WHERE install_id = ANY($1)`,
		`DELETE FROM agent_market.agent_threads WHERE install_id = ANY($1)`,
		`DELETE FROM agent_market.agent_install_defaults WHERE install_id = ANY($1)`,
	} {
		if _, err := s.Pool.Exec(ctx, q, installs); err != nil {
			return n, err
		}
	}
	return n, nil
}

// PurgeInstalls erases installs completely: objects first (uploads,
// artifacts, step blobs), then the rows (the install cascade reaches runs,
// steps, cards, artifacts, approvals, exceptions, files, thread, defaults and
// the duplicate index; the credit ledger goes by install). Returns objects
// deleted and installs erased. Only for installs with no evidence left
// inside their retention (and for the feature's removal).
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

// RemoveRunEvidence is an admin removing a finished round's files before its
// retention ends: the files sent in, the files made and the step blobs go;
// the run's row stays with who removed them, when and why.
func (s *Store) RemoveRunEvidence(ctx context.Context, files storage.Backend, run Run, by, reason string) (int, error) {
	all, err := s.runsAndChildren(ctx, []uuid.UUID{run.ID})
	if err != nil {
		return 0, err
	}
	keys, err := s.runObjectKeys(ctx, all, false)
	if err != nil {
		return 0, err
	}
	for _, id := range all {
		up, err := s.runUploadKeys(ctx, id)
		if err != nil {
			return 0, err
		}
		for _, k := range up {
			keys[k] = true
		}
	}
	n := 0
	for k := range keys {
		if err := files.Delete(ctx, k); err == nil {
			n++
		}
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return n, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	if _, err := tx.Exec(ctx, `DELETE FROM agent_market.agent_output_artifacts WHERE run_id = ANY($1)`, all); err != nil {
		return n, err
	}
	if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_files SET status = 'removed' WHERE run_id = ANY($1)`, all); err != nil {
		return n, err
	}
	if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_runs SET evidence_removed_at = now(), evidence_removed_by = $2,
		evidence_removed_reason = $3 WHERE id = $1`, run.ID, by, reason); err != nil {
		return n, err
	}
	return n, tx.Commit(ctx)
}

// ── storage used ─────────────────────────────────────────────────────────

// StorageUse is what one install keeps, in bytes.
type StorageUse struct {
	Uploads   int64 `json:"uploads"`
	Outputs   int64 `json:"outputs"`
	Total     int64 `json:"total"`
	WarnBytes int64 `json:"warn_bytes"`
	Warn      bool  `json:"warn"`
}

// StorageFor sums an install's uploads (not removed) and output files.
func (s *Store) StorageFor(ctx context.Context, install uuid.UUID, warnMB int) (StorageUse, error) {
	var u StorageUse
	err := s.Pool.QueryRow(ctx, `SELECT
		COALESCE((SELECT sum(size) FROM agent_market.agent_files WHERE install_id = $1 AND status <> 'removed'), 0)::bigint,
		COALESCE((SELECT sum(size) FROM agent_market.agent_output_artifacts WHERE install_id = $1), 0)::bigint`, install).
		Scan(&u.Uploads, &u.Outputs)
	u.Total = u.Uploads + u.Outputs
	if warnMB <= 0 {
		warnMB = 2048
	}
	u.WarnBytes = int64(warnMB) << 20
	u.Warn = u.Total >= u.WarnBytes*8/10
	return u, err
}

// RetentionText is how long things are kept, in the words the upload area and
// the data sheet use (the organisation's own years).
func RetentionText(years int) string {
	if years <= 0 {
		years = DefaultRetentionYears
	}
	return "ไฟล์ที่ส่งเข้ามาและไฟล์ผลลัพธ์ของรอบที่สำเร็จเก็บไว้ " + strconv.Itoa(years) + " ปีตามที่องค์กรกำหนด · " +
		"ข้อมูลของรอบที่ล้มเหลวหรือยกเลิกเก็บ 7 วัน · ไฟล์ที่ไม่ได้ใช้ในรอบใดเก็บ 30 วัน"
}
