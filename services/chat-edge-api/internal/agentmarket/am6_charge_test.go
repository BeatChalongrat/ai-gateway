package agentmarket

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM6 — charge for work delivered, on the control plane: a budget counts
// what people were charged (never a voided row), the cost of a round is said
// before it starts, a round that does not tie is counted apart from the
// failures, and what the platform paid without charging is shown as that.

// endedRun is a round of the install's with its steps charged, ended the way
// the runner ends it (Finish), some seconds after it started.
func (e *testEnv) endedRun(in Install, status, reason string, seconds int, credits ...float64) Run {
	e.t.Helper()
	ctx := context.Background()
	r, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for i, c := range credits {
		if err := e.store.Charge(ctx, in.ID, r.ID, fmt.Sprintf("k-%d", i), c, "", nil); err != nil {
			e.t.Fatal(err)
		}
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET started_at = now() - make_interval(secs => $2), period = '2026-09'
		WHERE id = $1`, r.ID, seconds); err != nil {
		e.t.Fatal(err)
	}
	if err := e.store.Finish(ctx, r.ID, "", status, reason); err != nil {
		e.t.Fatal(err)
	}
	r, _ = e.store.RunByID(ctx, r.ID)
	return r
}

// openRun is a round whose package gate did not pass and whose draft was
// delivered. (On a schema without the column the round is only "failed".)
func (e *testEnv) openRun(in Install, seconds int, credits ...float64) Run {
	e.t.Helper()
	ctx := context.Background()
	r := e.endedRun(in, RunSucceeded, "", seconds, credits...)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'failed', status_reason = 'not_tied', gate_open = true
		WHERE id = $1`, r.ID); err != nil {
		_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'failed', status_reason = 'not_tied' WHERE id = $1`, r.ID)
	}
	r, _ = e.store.RunByID(ctx, r.ID)
	return r
}

func TestABudgetCountsOnlyWhatWasCharged(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	e.endedRun(in, RunSucceeded, "", 40, 3, 2)              // delivered: 5 charged
	e.endedRun(in, RunFailed, "render_failed", 40, 7, 3)    // crashed after two steps: nothing charged
	e.endedRun(in, RunCancelled, "cancelled_by_user", 5, 4) // cancelled: nothing charged
	spent, err := e.store.SpentThisMonth(ctx, in.ID)
	if err != nil || spent != 5 {
		t.Fatalf("the install's month: %v %v", spent, err)
	}
	st, err := e.store.Budget(ctx, in)
	if err != nil || st.UserSpent != 5 || st.InstallSpent != 5 {
		t.Fatalf("the person's and the install's month: %+v %v", st, err)
	}
	// the department's month counts the same way
	e.mustDo(200, "PUT", "/admin/departments/บัญชี", map[string]any{"monthly_credits": 100})
	e.mustDo(200, "PUT", "/admin/members/"+e.user.Email, map[string]any{"department": "บัญชี", "role": "admin"})
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.org_members SET user_id = $2 WHERE email = $1`, e.user.Email, e.user.UserID); err != nil {
		t.Fatal(err)
	}
	if st, err = e.store.Budget(ctx, in); err != nil || st.DeptLimit == nil || st.DeptSpent != 5 {
		t.Fatalf("the department's month: %+v %v", st, err)
	}
	// the workspace's banner and the install page say the same
	out := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/budget", nil)
	if out["budget"].(map[string]any)["install_spent"].(float64) != 5 {
		t.Fatalf("banner: %v", out)
	}
	out = e.mustDo(200, "GET", "/installs/"+in.ID.String(), nil)
	if out["budget"].(map[string]any)["spent_this_month"].(float64) != 5 {
		t.Fatalf("install: %v", out)
	}
	// a limit that the voided rows alone would have reached does not stop the next round
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET budget_credits = 10 WHERE id = $1`, in.ID); err != nil {
		t.Fatal(err)
	}
	in, _ = e.store.InstallByID(ctx, in.ID)
	if st, _ = e.store.Budget(ctx, in); st.Exhausted != "" {
		t.Fatalf("19 credits of cost, 5 of them charged, limit 10: %+v", st)
	}
	// the admin sees what people were charged, and apart from it what the platform paid and did not charge
	spend := e.mustDo(200, "GET", "/admin/spend", nil)
	if spend["total_credits"].(float64) != 5 || spend["total_cost_not_charged"].(float64) != 14 {
		t.Fatalf("spend: charged %v, not charged %v", spend["total_credits"], spend["total_cost_not_charged"])
	}
	row := spend["by_agent"].([]any)[0].(map[string]any)
	if row["credits"].(float64) != 5 || row["cost_not_charged"].(float64) != 14 || row["month_spent"].(float64) != 5 {
		t.Fatalf("by agent: %v", row)
	}
}

func TestARoundThatDoesNotTieIsCountedApartFromFailures(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.installBank()
	e.endedRun(in, RunSucceeded, "", 40, 10)
	open := e.openRun(in, 40, 12)
	e.endedRun(in, RunFailed, "render_failed", 40, 6)
	// every JSON that carries a run's status carries its outcome
	one := e.mustDo(200, "GET", "/runs/"+open.ID.String(), nil)["run"].(map[string]any)
	t.Logf("a run: %s", jsonOf(one))
	if one["status"] != "failed" || one["status_reason"] != "not_tied" || one["outcome"] != "completed_open" {
		t.Fatalf("run: %v %v %v", one["status"], one["status_reason"], one["outcome"])
	}
	outcomes := map[string]int{}
	for _, r := range e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/runs", nil)["runs"].([]any) {
		outcomes[fmt.Sprint(r.(map[string]any)["outcome"])]++
	}
	if outcomes["completed"] != 1 || outcomes["completed_open"] != 1 || outcomes["failed"] != 1 {
		t.Fatalf("round list: %v", outcomes)
	}
	last := e.mustDo(200, "GET", "/installs", nil)["installs"].([]any)[0].(map[string]any)["last_run"].(map[string]any)
	if last["outcome"] != "failed" {
		t.Fatalf("the installs list's last round: %v", last)
	}
	// the agent's dashboard
	d := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/dashboard", nil)
	runs := d["runs"].(map[string]any)
	if runs["total"].(float64) != 3 || runs["succeeded"].(float64) != 1 || runs["completed_open"].(float64) != 1 || runs["failed"].(float64) != 1 {
		t.Fatalf("dashboard runs: %v", runs)
	}
	for _, f := range d["failures"].([]any) {
		if f.(map[string]any)["key"] == "not_tied" {
			t.Fatalf("a difference the agent found is not the agent failing: %v", d["failures"])
		}
	}
	if o := d["open"].([]any); len(o) != 1 || o[0].(map[string]any)["key"] != "not_tied" {
		t.Fatalf("what stayed open, by code: %v", d["open"])
	}
	spend := d["spend"].(map[string]any)
	if spend["credits"].(float64) != 22 || spend["cost_not_charged"].(float64) != 6 {
		t.Fatalf("dashboard spend: %v", spend)
	}
	// the admin's pages
	sp := e.mustDo(200, "GET", "/admin/spend", nil)["by_agent"].([]any)[0].(map[string]any)
	if sp["runs"].(float64) != 3 || sp["succeeded"].(float64) != 1 || sp["completed_open"].(float64) != 1 || sp["failed"].(float64) != 1 {
		t.Fatalf("admin spend: %v", sp)
	}
	seen := map[string]float64{}
	for _, r := range e.mustDo(200, "GET", "/admin/runs", nil)["runs"].([]any) {
		m := r.(map[string]any)
		seen[fmt.Sprint(m["outcome"])] = m["credits"].(float64)
		if m["outcome"] == "failed" && m["cost"].(float64) != 6 {
			t.Fatalf("an admin sees what a free round cost: %v", m)
		}
	}
	if len(seen) != 3 || seen["failed"] != 0 || seen["completed_open"] != 12 {
		t.Fatalf("admin runs: %v", seen)
	}
}

func TestSigningARoundThatDoesNotTieSaysWhatItIs(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.installBank()
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"domains": []string{"example.test"}})
	open := e.openRun(in, 40, 12)
	code, body := e.do("POST", "/runs/"+open.ID.String()+"/review", map[string]any{})
	c, m := errMsg(body)
	if code != 409 || c != "NOT_REVIEWABLE" {
		t.Fatalf("a draft is never signed: %d %v", code, body)
	}
	if !strings.Contains(m, "เสร็จแล้ว") || !strings.Contains(m, "ยังไม่ลงตัว") || !strings.Contains(m, "ร่าง") || strings.Contains(m, "ไม่สำเร็จ") ||
		strings.Contains(m, "ยังทำไม่เสร็จ") {
		t.Fatalf("finished, does not tie, its files are a draft — not a failure, not still running: %q", m)
	}
	if !strings.Contains(m, "รอบที่ลงตัว") {
		t.Fatalf("the round to sign is the next one that ties: %q", m)
	}
	var n int
	_ = e.store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM agent_market.run_reviews WHERE run_id = $1`, open.ID).Scan(&n)
	if n != 0 {
		t.Fatalf("nothing was recorded: %d", n)
	}
	// the same files give the same difference: trying again with them is not a round to start
	file := e.stagedFile(in, "statement.xlsx")
	rid := open.ID
	_, _ = e.store.Pool.Exec(context.Background(), `UPDATE agent_market.agent_files SET run_id = $2, status = 'active' WHERE id = $1`, file.ID, rid)
	if code, body := e.do("POST", "/runs/"+open.ID.String()+"/retry", nil); code != 409 {
		t.Fatalf("retry of a round that finished: %d %v", code, body)
	}
	// a round shared with a colleague says the same
	out := e.mustDo(200, "POST", "/runs/"+open.ID.String()+"/shares", map[string]any{"email": "reviewer@example.test", "can_review": true})
	sid := out["share"].(map[string]any)["id"].(string)
	shared := e.mustDo(200, "GET", "/shared/"+sid, nil, as("reviewer@example.test")...)
	if shared["run"].(map[string]any)["outcome"] != "completed_open" {
		t.Fatalf("shared run: %v", shared["run"])
	}
	list := e.mustDo(200, "GET", "/shared", nil, as("reviewer@example.test")...)["shared"].([]any)
	if len(list) != 1 || list[0].(map[string]any)["run_outcome"] != "completed_open" {
		t.Fatalf("shared with me: %v", list)
	}
}

func TestTheCostIsSaidBeforeTheRoundStarts(t *testing.T) {
	e := newEnvCfg(t, func(c *Config) { c.CreditsPerUSD, c.THBPerUSD = 1000, 35 })
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.installBank()
	path := "/installs/" + in.ID.String() + "/estimate"
	always := "คิดเครดิตเมื่องานเสร็จและส่งไฟล์ให้แล้ว"
	free := "รอบที่ระบบขัดข้องไม่คิดเครดิต"
	// nothing is known yet
	out := e.mustDo(200, "GET", path, nil)
	t.Logf("nothing known: %s", jsonOf(out))
	if out["credits"] != nil || out["thb"] != nil || out["seconds"] != nil || out["basis"] != "none" || out["basis_runs"].(float64) != 0 {
		t.Fatalf("nothing known: %v", out)
	}
	line, _ := out["line"].(string)
	if !strings.HasPrefix(line, "ยังไม่มีข้อมูลค่าใช้จ่ายของ agent นี้") || !strings.Contains(line, always) || !strings.Contains(line, free) {
		t.Fatalf("the line when nothing is known: %q", line)
	}
	// the catalog's sample run
	d, _ := e.store.DefinitionBySlug(ctx, "sran.bank-reconciliation")
	sample, err := e.store.CreateCatalogSample(ctx, d, nil, 1)
	if err != nil {
		t.Fatal(err)
	}
	if err := e.store.Charge(ctx, sample.InstallID, sample.ID, "k-sample", 800, "", nil); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET started_at = now() - interval '25 seconds' WHERE id = $1`, sample.ID); err != nil {
		t.Fatal(err)
	}
	if err := e.store.Finish(ctx, sample.ID, "", RunSucceeded, ""); err != nil {
		t.Fatal(err)
	}
	// two rounds of the organisation's own are not enough to go by
	e.endedRun(in, RunSucceeded, "", 30, 1000)
	e.openRun(in, 40, 1134.58)
	out = e.mustDo(200, "GET", path, nil)
	t.Logf("the catalog's sample: %s", jsonOf(out))
	if out["basis"] != "sample" || out["credits"].(float64) != 800 || out["thb"].(float64) != 28 || out["basis_runs"].(float64) != 1 {
		t.Fatalf("the catalog's sample: %v", out)
	}
	if s := out["seconds"].(float64); s < 24 || s > 27 {
		t.Fatalf("the sample's seconds: %v", s)
	}
	line, _ = out["line"].(string)
	if !strings.HasPrefix(line, "รอบหนึ่งใช้ประมาณ 800 เครดิต (≈ 28.00 บาท) ราว 25 วินาที") || !strings.Contains(line, always) ||
		!strings.Contains(line, "ไม่ว่ายอดจะลงตัวหรือไม่") || !strings.Contains(line, free) {
		t.Fatalf("the line from the sample: %q", line)
	}
	// three delivered rounds — one of them did not tie — and rounds that delivered nothing are not counted
	e.endedRun(in, RunSucceeded, "", 50, 1200)
	e.endedRun(in, RunFailed, "render_failed", 300, 90000)
	e.endedRun(in, RunCancelled, "cancelled_by_user", 300, 90000)
	out = e.mustDo(200, "GET", path, nil)
	t.Logf("the organisation's own rounds: %s", jsonOf(out))
	if out["basis"] != "own_runs" || out["basis_runs"].(float64) != 3 || out["credits"].(float64) != 1134.58 {
		t.Fatalf("the organisation's own rounds: %v", out)
	}
	if s := out["seconds"].(float64); s < 39 || s > 42 {
		t.Fatalf("the median round's seconds: %v", s)
	}
	if thb := out["thb"].(float64); thb < 39.70 || thb > 39.72 {
		t.Fatalf("in baht: %v", thb)
	}
	line, _ = out["line"].(string)
	if !strings.HasPrefix(line, "รอบหนึ่งใช้ประมาณ 1,130 เครดิต (≈ 39.71 บาท) ราว 40 วินาที") || !strings.Contains(line, always) || !strings.Contains(line, free) {
		t.Fatalf("the line from the organisation's rounds: %q", line)
	}
	if technicalText(line) {
		t.Fatalf("the line reads technical: %q", line)
	}
	// another organisation's rounds, and rounds older than 90 days, are not this organisation's estimate
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET created_at = now() - interval '120 days'
		WHERE install_id = $1 AND kind = 'run'`, in.ID); err != nil {
		t.Fatal(err)
	}
	if out = e.mustDo(200, "GET", path, nil); out["basis"] != "sample" {
		t.Fatalf("rounds older than 90 days: %v", out)
	}
	// someone else's install is not found
	if code, _ := e.do("GET", path, nil, "X-Test-User", uuid.NewString()); code != 404 {
		t.Fatalf("someone else's install: %d", code)
	}
	b, _ := json.Marshal(out)
	if strings.Contains(string(b), "null,") && out["credits"] == nil {
		t.Fatalf("a sample is known: %s", b)
	}
}
