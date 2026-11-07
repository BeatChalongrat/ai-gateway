package agentmarket

import (
	"context"
	"strings"
	"testing"
)

// AM21 (part 4): a pages job is a run of kind 'pages'. Migration 012 lets the database take one; down
// takes the jobs and the kind away and the earlier state is back. On 15 Oct 2026 the first request on
// live was refused by the check constraint while every test passed — none had asked the database.
func TestAM21Migration012LetsAPagesJobInAndDownTakesItOut(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	kinds := func() string {
		var def string
		if err := e.store.Pool.QueryRow(ctx, `SELECT pg_get_constraintdef(oid) FROM pg_constraint
			WHERE conrelid = 'agent_market.agent_runs'::regclass AND conname = 'agent_runs_kind_check'`).Scan(&def); err != nil {
			t.Fatal(err)
		}
		return def
	}
	if !strings.Contains(kinds(), "'pages'") {
		t.Fatalf("after 012 a run may be of kind pages: %s", kinds())
	}
	down, err := migrationFS.ReadFile("migrations/012_am21.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(kinds(), "'pages'") || e.countRows(`SELECT count(*) FROM agent_market.schema_migrations WHERE version = '012_am21'`) != 0 {
		t.Fatalf("down left the kind: %s", kinds())
	}
	if !strings.Contains(kinds(), "'review'") {
		t.Fatalf("down took more than its own: %s", kinds())
	}
	did, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(did) != 1 || did[0] != "012_am21" || !strings.Contains(kinds(), "'pages'") {
		t.Fatalf("up again: %v %v, %s", did, err, kinds())
	}
}
