package runner

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5/pgxpool"
)

// MetricsSnapshot is what the runner's /metrics reports (AM1b §0.4). The run
// counts come from the database, so they survive a runner restart and work
// as Prometheus counters for the error budget (docs/agent-market/OPERATIONS.md).
type MetricsSnapshot struct {
	Queued, Running, Awaiting int64
	OldestQueuedSeconds       float64
	Finished                  map[string]int64   // succeeded | completed_open | failed | cancelled (kinds run + self_test)
	StepSeconds               map[string]float64 // succeeded steps, by step name without its subject
	StepCount                 map[string]int64
	CreditsSpent              float64
	CreditsCost               float64
	PoolAcquired, PoolMax     int32
	SandboxReady              bool
	// AM3: reports people filed that nobody has closed yet (the pager's
	// "someone reported a problem"), and runs waiting for budget.
	ProblemsOpen, BudgetWaiting int64
	// ModelWaiting (AM15): runs that stopped because no model they may use could be reached
	ModelWaiting int64
}

// CollectMetrics reads the snapshot with a handful of aggregate queries.
func CollectMetrics(ctx context.Context, pool *pgxpool.Pool, sandboxReady bool) MetricsSnapshot {
	s := MetricsSnapshot{Finished: map[string]int64{}, StepSeconds: map[string]float64{}, StepCount: map[string]int64{},
		SandboxReady: sandboxReady}
	_ = pool.QueryRow(ctx, `SELECT count(*) FILTER (WHERE status='queued'), count(*) FILTER (WHERE status='running'),
		count(*) FILTER (WHERE status='awaiting_input'),
		COALESCE(EXTRACT(EPOCH FROM now() - min(created_at) FILTER (WHERE status='queued')), 0)
		FROM agent_market.agent_runs`).Scan(&s.Queued, &s.Running, &s.Awaiting, &s.OldestQueuedSeconds)
	// AM6: a run that ended on its package's gate with its draft delivered finished open — work
	// delivered, which the SLO counts as a run that ended and never as a failure
	if rows, err := pool.Query(ctx, `SELECT CASE WHEN status = 'failed' AND gate_open THEN 'completed_open' ELSE status END, count(*)
		FROM agent_market.agent_runs
		WHERE kind IN ('run', 'self_test') AND status IN ('succeeded', 'failed', 'cancelled') GROUP BY 1`); err == nil {
		for rows.Next() {
			var st string
			var n int64
			if rows.Scan(&st, &n) == nil {
				s.Finished[st] = n
			}
		}
		rows.Close()
	}
	if rows, err := pool.Query(ctx, `SELECT split_part(name, ':', 1), COALESCE(sum(duration_ms), 0) / 1000.0, count(*)
		FROM agent_market.agent_run_steps WHERE status = 'succeeded' AND duration_ms IS NOT NULL GROUP BY 1`); err == nil {
		for rows.Next() {
			var name string
			var secs float64
			var n int64
			if rows.Scan(&name, &secs, &n) == nil {
				s.StepSeconds[name], s.StepCount[name] = secs, n
			}
		}
		rows.Close()
	}
	// AM6: what people were charged, and what the platform paid (a voided row is a cost, not a charge)
	_ = pool.QueryRow(ctx, `SELECT COALESCE(sum(credits) FILTER (WHERE voided_at IS NULL), 0), COALESCE(sum(credits), 0)
		FROM agent_market.agent_budget_ledger`).Scan(&s.CreditsSpent, &s.CreditsCost)
	_ = pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.problem_reports WHERE status = 'open'`).Scan(&s.ProblemsOpen)
	_ = pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs WHERE status = 'awaiting_input'
		AND status_reason LIKE 'budget_%'`).Scan(&s.BudgetWaiting)
	_ = pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs WHERE status = 'awaiting_input'
		AND status_reason = 'model_unreachable'`).Scan(&s.ModelWaiting)
	st := pool.Stat()
	s.PoolAcquired, s.PoolMax = st.AcquiredConns(), st.MaxConns()
	return s
}

// RenderMetrics writes the snapshot in the Prometheus text format; every
// series carries the agent_market_ prefix.
func RenderMetrics(s MetricsSnapshot) string {
	var b strings.Builder
	w := func(format string, a ...any) { fmt.Fprintf(&b, format+"\n", a...) }
	w("# TYPE agent_market_runs gauge")
	w(`agent_market_runs{status="queued"} %d`, s.Queued)
	w(`agent_market_runs{status="running"} %d`, s.Running)
	w(`agent_market_runs{status="awaiting_input"} %d`, s.Awaiting)
	w("# TYPE agent_market_oldest_queued_seconds gauge")
	w("agent_market_oldest_queued_seconds %s", num(s.OldestQueuedSeconds))
	w("# TYPE agent_market_runs_finished_total counter")
	for _, st := range []string{"succeeded", "completed_open", "failed", "cancelled"} {
		w(`agent_market_runs_finished_total{status="%s"} %d`, st, s.Finished[st])
	}
	w("# TYPE agent_market_step_seconds summary")
	names := make([]string, 0, len(s.StepCount))
	for n := range s.StepCount {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		w(`agent_market_step_seconds_sum{step="%s"} %s`, n, num(s.StepSeconds[n]))
		w(`agent_market_step_seconds_count{step="%s"} %d`, n, s.StepCount[n])
	}
	w("# TYPE agent_market_credits_spent_total counter")
	w("agent_market_credits_spent_total %s", num(s.CreditsSpent))
	w("# TYPE agent_market_credits_cost_total counter")
	w("agent_market_credits_cost_total %s", num(s.CreditsCost))
	w("# TYPE agent_market_pool_acquired_conns gauge")
	w("agent_market_pool_acquired_conns %d", s.PoolAcquired)
	w("agent_market_pool_max_conns %d", s.PoolMax)
	w("# TYPE agent_market_problem_reports_open gauge")
	w("agent_market_problem_reports_open %d", s.ProblemsOpen)
	w("# TYPE agent_market_runs_waiting_budget gauge")
	w("agent_market_runs_waiting_budget %d", s.BudgetWaiting)
	w("# TYPE agent_market_runs_waiting_model gauge")
	w("agent_market_runs_waiting_model %d", s.ModelWaiting)
	w("# TYPE agent_market_sandbox_ready gauge")
	w("agent_market_sandbox_ready %d", map[bool]int{true: 1, false: 0}[s.SandboxReady])
	return b.String()
}

func num(f float64) string {
	return strings.TrimRight(strings.TrimRight(fmt.Sprintf("%.3f", f), "0"), ".")
}
