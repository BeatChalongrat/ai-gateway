package runner

import (
	"strings"
	"testing"
)

// AM1b §0.4: the runner's own metric prefix, with what the dashboard and the
// error budget need — not only the queue gauges.
func TestMetricsCarryTheirOwnPrefixAndTheBudgetSeries(t *testing.T) {
	out := RenderMetrics(MetricsSnapshot{
		Queued: 2, Running: 1, Awaiting: 3, OldestQueuedSeconds: 42,
		Finished:     map[string]int64{"succeeded": 17, "failed": 1, "cancelled": 2},
		StepSeconds:  map[string]float64{"parse": 12.5, "narrative": 60},
		StepCount:    map[string]int64{"parse": 5, "narrative": 4},
		CreditsSpent: 91234.5, PoolAcquired: 1, PoolMax: 4, SandboxReady: true,
	})
	for _, want := range []string{
		`agent_market_runs{status="queued"} 2`,
		`agent_market_runs{status="awaiting_input"} 3`,
		`agent_market_runs_finished_total{status="succeeded"} 17`,
		`agent_market_runs_finished_total{status="failed"} 1`,
		`agent_market_oldest_queued_seconds 42`,
		`agent_market_step_seconds_sum{step="narrative"} 60`,
		`agent_market_step_seconds_count{step="parse"} 5`,
		`agent_market_credits_spent_total 91234.5`,
		`agent_market_pool_max_conns 4`,
		`agent_market_sandbox_ready 1`,
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q in:\n%s", want, out)
		}
	}
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		if !strings.HasPrefix(line, "agent_market_") && !strings.HasPrefix(line, "# ") {
			t.Fatalf("every series carries the agent_market_ prefix: %q", line)
		}
	}
}
