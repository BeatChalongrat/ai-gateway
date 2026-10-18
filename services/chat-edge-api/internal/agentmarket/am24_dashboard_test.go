package agentmarket

import (
	"context"
	"testing"
	"time"
)

// AM24 Part 5 — the dashboard's "waiting for an answer" counts the runs that wait for a person; the runs that wait for
// their model or for budget are counted apart (AM15 §10: a person reading the count thought people were being asked
// things). The defect made on purpose (CARD-RULES §6d): one wait of each kind; only one is a wait for an answer.
func TestTheDashboardCountsTheWaitsForAPersonApartFromTheWaitsForAModelOrForBudget(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	for _, reason := range []string{"waiting_preflight", "model_unreachable", "budget_install", "budget_org"} {
		r, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
			DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input', status_reason = $2,
			awaiting_since = now() - interval '1 hour' WHERE id = $1`, r.ID, reason); err != nil {
			t.Fatal(err)
		}
	}
	d, err := e.store.Dashboard(ctx, in, time.Now().Add(-24*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if d.Waiting.Runs != 1 || d.Waiting.ForModel != 1 || d.Waiting.ForBudget != 2 {
		t.Fatalf("one wait for an answer, one for the model, two for budget: %+v", d.Waiting)
	}
	if d.Waiting.OldestRunS == nil || *d.Waiting.OldestRunS < 3500 {
		t.Fatalf("the oldest wait for a person is the one that waits for a person: %v", d.Waiting.OldestRunS)
	}
}
