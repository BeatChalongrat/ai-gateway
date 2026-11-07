package agentmarket

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestDashboardCountsWhatTheRunsLeft(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	exec := func(q string, args ...any) {
		t.Helper()
		if _, err := e.store.Pool.Exec(ctx, q, args...); err != nil {
			t.Fatal(err)
		}
	}
	run := func(kind, status string, credits float64, model string, selfHosted bool) uuid.UUID {
		t.Helper()
		r, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: kind,
			DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
		if err != nil {
			t.Fatal(err)
		}
		var m any
		if model != "" {
			m = model
		}
		// queued 10 s, worked 60 s of which 20 s waited on a person
		exec(`UPDATE agent_market.agent_runs SET status = $2, credits_spent = $3, model_used = $4, model_self_hosted = $5,
			created_at = now() - interval '2 hours', started_at = now() - interval '2 hours' + interval '10 seconds',
			finished_at = now() - interval '2 hours' + interval '70 seconds', awaiting_ms = 20000,
			awaiting_since = CASE WHEN $2 = 'awaiting_input' THEN now() - interval '1 hour' END,
			status_reason = CASE WHEN $2 = 'failed' THEN 'sandbox: timeout' ELSE '' END WHERE id = $1`,
			r.ID, status, credits, m, selfHosted)
		return r.ID
	}
	summary := func(runID uuid.UUID, json string) {
		exec(`INSERT INTO agent_market.agent_run_steps (run_id, seq, name, kind, status, idempotency_key, output)
			VALUES ($1, 1, 'compute', 'compute', 'succeeded', 'k', jsonb_build_object('summary', $2::jsonb))`, runID, json)
	}
	card := func(runID uuid.UUID, status string, codes ...string) {
		qs := "["
		for i, c := range codes {
			if i > 0 {
				qs += ","
			}
			qs += `{"key":"` + c + `:x","code":"` + c + `"}`
		}
		exec(`INSERT INTO agent_market.agent_run_clarifications (run_id, install_id, tenant_id, user_id, moment, seq, kind, blocking,
			questions, status) VALUES ($1, $2, $3, $4, 'preflight', 1, 'questions', true, $5::jsonb, $6)`,
			runID, in.ID, in.TenantID, in.UserID, qs+"]", status)
	}

	clean := run(KindRun, RunSucceeded, 2, "", false) // no question, no model
	summary(clean, `{"documents":3,"confidence":{"high":20,"medium":2,"low":0,"person":0,"computed":4},"tiers":{"digital":3}}`)
	exec(`INSERT INTO agent_market.agent_output_artifacts (run_id, install_id, tenant_id, user_id, format, kind, name, version,
		storage_key, size, sha256, produced_by_step) VALUES ($1, $2, $3, $4, 'xlsx', 'table', 'r.xlsx', 1, 'k', 1, 's', 'render')`,
		clean, in.ID, in.TenantID, in.UserID)
	asked := run(KindRun, RunSucceeded, 4, "qwen3.8-27b-fp8", true) // asked, a scan read by the self-hosted model
	summary(asked, `{"documents":1,"confidence":{"high":5,"medium":0,"low":1,"person":2,"computed":1},"tiers":{"scan":1}}`)
	card(asked, "answered", "e2_low_confidence", "e2_duplicate")
	waiting := run(KindRun, RunAwaitingInput, 1, "", false)
	card(waiting, "open", "e2_low_confidence")
	run(KindRun, RunFailed, 0.5, "", false)
	run(KindSelfTest, RunSucceeded, 1, "", false) // a self-test: spend, the last self-test, not work
	if _, err := e.store.ReplaceRunExceptions(ctx, asked, expenseExceptions()); err != nil {
		t.Fatal(err)
	}
	// another install's run is not counted
	other := e.install()
	if _, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: other.ID, TenantID: other.TenantID, UserID: other.UserID,
		Kind: KindRun, DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, ""); err != nil {
		t.Fatal(err)
	}

	code, d := e.do("GET", "/installs/"+in.ID.String()+"/dashboard?days=7", nil)
	if code != 200 {
		t.Fatalf("dashboard: %d %v", code, d)
	}
	runs := d["runs"].(map[string]any)
	if runs["total"] != float64(4) || runs["succeeded"] != float64(2) || runs["first_pass"] != float64(1) || runs["first_pass_rate"] != 0.5 {
		t.Fatalf("runs: %v", runs)
	}
	tm := d["time"].(map[string]any)
	if tm["queue_p50_s"] != float64(10) || tm["work_p50_s"] != float64(40) || tm["human_wait_p50_s"] != float64(20) {
		t.Fatalf("time: %v", tm)
	}
	w := d["waiting"].(map[string]any)
	if w["runs"] != float64(1) || w["oldest_run_s"].(float64) < 3500 || w["open_exceptions"] != float64(1) {
		t.Fatalf("waiting: %v", w)
	}
	qs := d["questions"].([]any)
	if len(qs) != 2 || qs[0].(map[string]any)["key"] != "e2_low_confidence" || qs[0].(map[string]any)["count"] != float64(2) ||
		qs[0].(map[string]any)["open"] != float64(1) {
		t.Fatalf("questions: %v", qs)
	}
	ex := d["exceptions"].(map[string]any)
	if ex["open"] != float64(1) || ex["noted"] != float64(1) || len(ex["by_reason"].([]any)) != 2 {
		t.Fatalf("exceptions: %v", ex)
	}
	if f := d["failures"].([]any); len(f) != 1 || f[0].(map[string]any)["key"] != "sandbox: timeout" {
		t.Fatalf("failures: %v", f)
	}
	conf := d["confidence"].(map[string]any)
	if conf["high"] != float64(25) || conf["low"] != float64(1) || conf["person"] != float64(2) {
		t.Fatalf("confidence: %v", conf)
	}
	if tiers := d["tiers"].(map[string]any); tiers["digital"] != float64(3) || tiers["scan"] != float64(1) || d["documents"] != float64(4) {
		t.Fatalf("tiers: %v %v", tiers, d["documents"])
	}
	if a := d["artifacts"].([]any); len(a) != 1 || a[0].(map[string]any)["key"] != "xlsx" {
		t.Fatalf("artifacts: %v", a)
	}
	sp := d["spend"].(map[string]any)
	if sp["credits"] != 8.5 || sp["per_succeeded_run"] != float64(3) || sp["per_document"] != 1.5 {
		t.Fatalf("spend: %v", sp)
	}
	ms := d["models"].([]any)
	if len(ms) != 1 || ms[0].(map[string]any)["model"] != "qwen3.8-27b-fp8" || ms[0].(map[string]any)["self_hosted"] != true {
		t.Fatalf("models: %v", ms)
	}
	if st, _ := d["last_self_test"].(map[string]any); st == nil || st["status"] != RunSucceeded {
		t.Fatalf("last self-test: %v", d["last_self_test"])
	}
	if code, _ := e.do("GET", "/installs/"+in.ID.String()+"/dashboard", nil, "X-Test-User", uuid.New().String()); code != 404 {
		t.Fatalf("another user's dashboard: %d", code)
	}
}
