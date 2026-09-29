package runner

import (
	"context"
	"strings"
	"testing"
)

// AM6 — the SLO counts what was delivered. A round that ended on its
// package's gate with its draft delivered (a reconciliation that does not
// tie) finished open: agent_market_runs_finished_total labels it
// status="completed_open", which the rules count as a run that ended and
// never as a failure (infra/prometheus/agent_market.yml). A round that
// delivered nothing is still status="failed".
func TestARoundThatFinishedOpenIsNotAFailureOfTheSLO(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	open := e.round("run", true)
	crashed := e.round("run", true)
	fine := e.round("run", true)
	for _, q := range []struct {
		sql string
		id  any
	}{
		{`UPDATE agent_market.agent_runs SET status = 'failed', status_reason = 'not_tied', gate_open = true, delivered_at = now(), finished_at = now() WHERE id = $1`, open.ID},
		{`UPDATE agent_market.agent_runs SET status = 'failed', status_reason = 'engine_failed', finished_at = now() WHERE id = $1`, crashed.ID},
		{`UPDATE agent_market.agent_runs SET status = 'succeeded', delivered_at = now(), finished_at = now() WHERE id = $1`, fine.ID},
	} {
		if _, err := e.store.Pool.Exec(ctx, q.sql, q.id); err != nil {
			t.Fatal(err)
		}
	}
	s := CollectMetrics(ctx, e.store.Pool, true)
	if s.Finished["completed_open"] != 1 || s.Finished["failed"] != 1 || s.Finished["succeeded"] != 1 {
		t.Fatalf("finished runs by status: %v — the round that finished open is counted apart from the one that crashed", s.Finished)
	}
	out := RenderMetrics(s)
	if !strings.Contains(out, `agent_market_runs_finished_total{status="completed_open"} 1`) ||
		!strings.Contains(out, `agent_market_runs_finished_total{status="failed"} 1`) {
		t.Fatalf("rendered:\n%s", out)
	}
}
