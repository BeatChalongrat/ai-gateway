package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/google/uuid"
)

// ── Per-agent dashboard (AM2 Phase G) ────────────────────────────────────
//
// GET /installs/{id}/dashboard?days=30 — how one installed agent has been
// doing for its owner, from the rows the runs already leave (no new
// tables): runs by status and the share that finished without a question,
// how long runs queued, worked and waited on a person, what is waiting now
// and for how long, which questions it asks, the exceptions it left and
// what people decided, why runs failed, how sure the values it read were,
// what it delivered, which versions ran, what it spent (per run and per
// document) and which models saw the documents — self-hosted or not — and
// the last self-test.

// Dashboard is the whole view.
type Dashboard struct {
	Since        time.Time        `json:"since"`
	Days         int              `json:"days"`
	Version      string           `json:"version"`
	Runs         DashRuns         `json:"runs"`
	Time         DashTimes        `json:"time"`
	Waiting      DashWaiting      `json:"waiting"`
	Questions    []DashCount      `json:"questions"`
	Exceptions   DashExceptions   `json:"exceptions"`
	Failures     []DashCount      `json:"failures"`
	Confidence   map[string]int64 `json:"confidence"`
	Tiers        map[string]int64 `json:"tiers"`
	Documents    int64            `json:"documents"`
	Artifacts    []DashCount      `json:"artifacts"`
	Versions     []DashVersion    `json:"versions"`
	Spend        DashSpend        `json:"spend"`
	Models       []DashModel      `json:"models"`
	LastSelfTest *DashSelfTest    `json:"last_self_test"`
}

type DashCount struct {
	Key   string `json:"key"`
	Count int64  `json:"count"`
	Open  int64  `json:"open,omitempty"`
}

type DashRuns struct {
	Total         int64            `json:"total"`
	ByStatus      map[string]int64 `json:"by_status"`
	Succeeded     int64            `json:"succeeded"`
	FirstPass     int64            `json:"first_pass"` // succeeded without a single question card
	FirstPassRate *float64         `json:"first_pass_rate"`
}

// DashTimes are seconds (p50 / p90) over the window's succeeded runs.
type DashTimes struct {
	QueueP50 *float64 `json:"queue_p50_s"`
	QueueP90 *float64 `json:"queue_p90_s"`
	WorkP50  *float64 `json:"work_p50_s"`
	WorkP90  *float64 `json:"work_p90_s"`
	HumanP50 *float64 `json:"human_wait_p50_s"`
	HumanP90 *float64 `json:"human_wait_p90_s"`
}

type DashWaiting struct {
	Runs             int64    `json:"runs"`            // awaiting an answer now
	OldestRunS       *float64 `json:"oldest_run_s"`    // how long the oldest has waited
	OpenExceptions   int64    `json:"open_exceptions"` // left for a person, not yet decided
	OldestExceptionS *float64 `json:"oldest_exception_s"`
}

type DashExceptions struct {
	Open     int64       `json:"open"`
	Resolved int64       `json:"resolved"`
	Noted    int64       `json:"noted"`
	ByReason []DashCount `json:"by_reason"`
	Choices  []DashCount `json:"choices"` // what people decided
}

type DashVersion struct {
	Version string    `json:"version"`
	Runs    int64     `json:"runs"`
	Last    time.Time `json:"last_run_at"`
}

type DashSpend struct {
	Credits       float64  `json:"credits"`
	PerRun        *float64 `json:"per_succeeded_run"`
	PerDocument   *float64 `json:"per_document"`
	BudgetCredits float64  `json:"budget_credits"`
}

type DashModel struct {
	Model      string `json:"model"`
	SelfHosted bool   `json:"self_hosted"`
	Runs       int64  `json:"runs"`
}

type DashSelfTest struct {
	Status   string     `json:"status"`
	Reason   string     `json:"reason"`
	Version  string     `json:"version"`
	Finished *time.Time `json:"finished_at"`
}

// Dashboard builds the view of one install since a moment.
func (s *Store) Dashboard(ctx context.Context, in Install, since time.Time) (Dashboard, error) {
	d := Dashboard{Since: since, Version: in.DefinitionVersion, Runs: DashRuns{ByStatus: map[string]int64{}},
		Confidence: map[string]int64{}, Tiers: map[string]int64{}, Questions: []DashCount{}, Failures: []DashCount{},
		Artifacts: []DashCount{}, Versions: []DashVersion{}, Models: []DashModel{},
		Exceptions: DashExceptions{ByReason: []DashCount{}, Choices: []DashCount{}}}
	id := in.ID
	// runs by status (the person's runs; self-tests, previews and replies are not work)
	rows, err := s.Pool.Query(ctx, `SELECT status, count(*) FROM agent_market.agent_runs
		WHERE install_id = $1 AND kind = 'run' AND created_at >= $2 GROUP BY status`, id, since)
	if err != nil {
		return d, err
	}
	for rows.Next() {
		var st string
		var n int64
		if err := rows.Scan(&st, &n); err != nil {
			rows.Close()
			return d, err
		}
		d.Runs.ByStatus[st] = n
		d.Runs.Total += n
	}
	rows.Close()
	d.Runs.Succeeded = d.Runs.ByStatus[RunSucceeded]
	if err := s.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs r
		WHERE r.install_id = $1 AND r.kind = 'run' AND r.status = 'succeeded' AND r.created_at >= $2
		  AND NOT EXISTS (SELECT 1 FROM agent_market.agent_run_clarifications c WHERE c.run_id = r.id AND c.kind = 'questions')`,
		id, since).Scan(&d.Runs.FirstPass); err != nil {
		return d, err
	}
	if d.Runs.Succeeded > 0 {
		v := float64(d.Runs.FirstPass) / float64(d.Runs.Succeeded)
		d.Runs.FirstPassRate = &v
	}
	// time: queued, working (without the time waiting on a person), waiting on a person
	if err := s.Pool.QueryRow(ctx, `SELECT
		percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM started_at - created_at)),
		percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM started_at - created_at)),
		percentile_cont(0.5) WITHIN GROUP (ORDER BY greatest(extract(epoch FROM finished_at - started_at) - awaiting_ms / 1000.0, 0)),
		percentile_cont(0.9) WITHIN GROUP (ORDER BY greatest(extract(epoch FROM finished_at - started_at) - awaiting_ms / 1000.0, 0)),
		percentile_cont(0.5) WITHIN GROUP (ORDER BY awaiting_ms / 1000.0),
		percentile_cont(0.9) WITHIN GROUP (ORDER BY awaiting_ms / 1000.0)
		FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'run' AND status = 'succeeded' AND created_at >= $2
		  AND started_at IS NOT NULL AND finished_at IS NOT NULL`, id, since).Scan(&d.Time.QueueP50, &d.Time.QueueP90,
		&d.Time.WorkP50, &d.Time.WorkP90, &d.Time.HumanP50, &d.Time.HumanP90); err != nil {
		return d, err
	}
	// waiting now (not limited to the window: an old wait is exactly what to see)
	if err := s.Pool.QueryRow(ctx, `SELECT count(*), extract(epoch FROM now() - min(awaiting_since))::float8
		FROM agent_market.agent_runs WHERE install_id = $1 AND status = 'awaiting_input'`, id).
		Scan(&d.Waiting.Runs, &d.Waiting.OldestRunS); err != nil {
		return d, err
	}
	if err := s.Pool.QueryRow(ctx, `SELECT count(*), extract(epoch FROM now() - min(e.created_at))::float8
		FROM agent_market.agent_run_exceptions e JOIN agent_market.agent_runs r ON r.id = e.run_id
		WHERE r.install_id = $1 AND e.status = 'open'`, id).Scan(&d.Waiting.OpenExceptions, &d.Waiting.OldestExceptionS); err != nil {
		return d, err
	}
	// the questions it asks
	if d.Questions, err = s.counts(ctx, `SELECT q->>'code', count(*), count(*) FILTER (WHERE c.status = 'open')
		FROM agent_market.agent_run_clarifications c JOIN agent_market.agent_runs r ON r.id = c.run_id,
		     jsonb_array_elements(CASE WHEN jsonb_typeof(c.questions) = 'array' THEN c.questions ELSE '[]'::jsonb END) q
		WHERE r.install_id = $1 AND r.kind = 'run' AND c.kind = 'questions' AND c.created_at >= $2
		GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20`, id, since); err != nil {
		return d, err
	}
	// the exceptions it left, and what people decided
	if err := s.Pool.QueryRow(ctx, `SELECT count(*) FILTER (WHERE e.status = 'open'), count(*) FILTER (WHERE e.status = 'resolved'),
		count(*) FILTER (WHERE e.status = 'noted')
		FROM agent_market.agent_run_exceptions e JOIN agent_market.agent_runs r ON r.id = e.run_id
		WHERE r.install_id = $1 AND e.created_at >= $2`, id, since).Scan(&d.Exceptions.Open, &d.Exceptions.Resolved,
		&d.Exceptions.Noted); err != nil {
		return d, err
	}
	if d.Exceptions.ByReason, err = s.counts(ctx, `SELECT e.reason_code, count(*), count(*) FILTER (WHERE e.status = 'open')
		FROM agent_market.agent_run_exceptions e JOIN agent_market.agent_runs r ON r.id = e.run_id
		WHERE r.install_id = $1 AND e.created_at >= $2 GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 30`, id, since); err != nil {
		return d, err
	}
	if d.Exceptions.Choices, err = s.counts(ctx, `SELECT e.source_ref->'decision'->>'label', count(*), 0
		FROM agent_market.agent_run_exceptions e JOIN agent_market.agent_runs r ON r.id = e.run_id
		WHERE r.install_id = $1 AND e.status = 'resolved' AND e.created_at >= $2 GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20`,
		id, since); err != nil {
		return d, err
	}
	// why runs failed (the reason the runner recorded, its first line)
	if d.Failures, err = s.counts(ctx, `SELECT left(split_part(coalesce(nullif(status_reason, ''), 'unknown'), E'\n', 1), 120), count(*), 0
		FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'run' AND status = 'failed' AND created_at >= $2
		GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20`, id, since); err != nil {
		return d, err
	}
	// what the package said about each succeeded run (its compute summary)
	srows, err := s.Pool.Query(ctx, `SELECT st.output->'summary' FROM agent_market.agent_run_steps st
		JOIN agent_market.agent_runs r ON r.id = st.run_id
		WHERE r.install_id = $1 AND r.kind = 'run' AND r.status = 'succeeded' AND r.created_at >= $2
		  AND st.name = 'compute' AND st.status = 'succeeded'`, id, since)
	if err != nil {
		return d, err
	}
	for srows.Next() {
		var raw json.RawMessage
		if err := srows.Scan(&raw); err != nil {
			srows.Close()
			return d, err
		}
		var sum struct {
			Confidence map[string]int64 `json:"confidence"`
			Tiers      map[string]int64 `json:"tiers"`
			Documents  *int64           `json:"documents"`
			Claims     *int64           `json:"claims"`
		}
		if json.Unmarshal(raw, &sum) != nil {
			continue
		}
		for k, v := range sum.Confidence {
			d.Confidence[k] += v
		}
		for k, v := range sum.Tiers {
			d.Tiers[k] += v
		}
		switch {
		case sum.Documents != nil:
			d.Documents += *sum.Documents
		case sum.Claims != nil:
			d.Documents += *sum.Claims
		}
	}
	srows.Close()
	// what it delivered
	if d.Artifacts, err = s.counts(ctx, `SELECT a.format, count(*), 0 FROM agent_market.agent_output_artifacts a
		JOIN agent_market.agent_runs r ON r.id = a.run_id
		WHERE a.install_id = $1 AND r.kind = 'run' AND a.created_at >= $2 GROUP BY 1 ORDER BY 2 DESC, 1`, id, since); err != nil {
		return d, err
	}
	// which versions ran
	vrows, err := s.Pool.Query(ctx, `SELECT definition_version, count(*), max(created_at) FROM agent_market.agent_runs
		WHERE install_id = $1 AND kind = 'run' AND created_at >= $2 GROUP BY 1 ORDER BY 3 DESC`, id, since)
	if err != nil {
		return d, err
	}
	for vrows.Next() {
		var v DashVersion
		if err := vrows.Scan(&v.Version, &v.Runs, &v.Last); err != nil {
			vrows.Close()
			return d, err
		}
		d.Versions = append(d.Versions, v)
	}
	vrows.Close()
	// spend (every kind: a self-test costs too)
	if err := s.Pool.QueryRow(ctx, `SELECT coalesce(sum(credits_spent), 0)::float8 FROM agent_market.agent_runs
		WHERE install_id = $1 AND created_at >= $2`, id, since).Scan(&d.Spend.Credits); err != nil {
		return d, err
	}
	var succeededSpend float64
	if err := s.Pool.QueryRow(ctx, `SELECT coalesce(sum(credits_spent), 0)::float8 FROM agent_market.agent_runs
		WHERE install_id = $1 AND kind = 'run' AND status = 'succeeded' AND created_at >= $2`, id, since).Scan(&succeededSpend); err != nil {
		return d, err
	}
	if d.Runs.Succeeded > 0 {
		v := succeededSpend / float64(d.Runs.Succeeded)
		d.Spend.PerRun = &v
	}
	if d.Documents > 0 {
		v := succeededSpend / float64(d.Documents)
		d.Spend.PerDocument = &v
	}
	d.Spend.BudgetCredits = in.BudgetCredits
	// which models saw this agent's work
	mrows, err := s.Pool.Query(ctx, `SELECT model_used, coalesce(model_self_hosted, false), count(*) FROM agent_market.agent_runs
		WHERE install_id = $1 AND kind = 'run' AND model_used IS NOT NULL AND model_used <> '' AND created_at >= $2
		GROUP BY 1, 2 ORDER BY 3 DESC, 1`, id, since)
	if err != nil {
		return d, err
	}
	for mrows.Next() {
		var m DashModel
		if err := mrows.Scan(&m.Model, &m.SelfHosted, &m.Runs); err != nil {
			mrows.Close()
			return d, err
		}
		d.Models = append(d.Models, m)
	}
	mrows.Close()
	// the last self-test (any time)
	var st DashSelfTest
	err = s.Pool.QueryRow(ctx, `SELECT status, status_reason, definition_version, finished_at FROM agent_market.agent_runs
		WHERE install_id = $1 AND kind = 'self_test' ORDER BY created_at DESC LIMIT 1`, id).
		Scan(&st.Status, &st.Reason, &st.Version, &st.Finished)
	if err == nil {
		d.LastSelfTest = &st
	}
	return d, nil
}

func (s *Store) counts(ctx context.Context, q string, id uuid.UUID, since time.Time) ([]DashCount, error) {
	rows, err := s.Pool.Query(ctx, q, id, since)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []DashCount{}
	for rows.Next() {
		var c DashCount
		var key *string
		if err := rows.Scan(&key, &c.Count, &c.Open); err != nil {
			return nil, err
		}
		if key != nil {
			c.Key = *key
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

func (a *API) dashboard(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	days := 30
	if v, err := strconv.Atoi(r.URL.Query().Get("days")); err == nil && v >= 1 && v <= 365 {
		days = v
	}
	d, err := a.store.Dashboard(r.Context(), in, time.Now().AddDate(0, 0, -days))
	if err != nil {
		a.fail(w, err, "dashboard")
		return
	}
	d.Days = days
	writeJSON(w, http.StatusOK, d)
}
