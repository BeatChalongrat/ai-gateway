package runner

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM6 — charge for work delivered, not for whether the numbers agreed.
//
// A round that ends having delivered nothing (a crash, a cancel) costs the
// person nothing: its ledger rows are kept and voided, and the run keeps what
// the platform paid. A round whose package gate did not pass delivered its
// work — the draft, and how much is unexplained — and is charged like any
// other; it reads "เสร็จแล้ว · ยังไม่ลงตัว", not as an error.

// runJSON is a run as the API sends it.
func runJSON(t *testing.T, e *failEnv, id uuid.UUID) map[string]any {
	t.Helper()
	run, err := e.store.RunByID(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(run)
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	return m
}

type ledgerRow struct {
	key     string
	credits float64
	voided  *time.Time
	reason  string
}

func (e *failEnv) ledger(run uuid.UUID) []ledgerRow {
	e.t.Helper()
	rows, err := e.store.Pool.Query(context.Background(), `SELECT step_key, credits::float8, voided_at, void_reason
		FROM agent_market.agent_budget_ledger WHERE run_id = $1 ORDER BY id`, run)
	if err != nil {
		e.t.Fatal(err)
	}
	defer rows.Close()
	var out []ledgerRow
	for rows.Next() {
		var r ledgerRow
		if err := rows.Scan(&r.key, &r.credits, &r.voided, &r.reason); err != nil {
			e.t.Fatal(err)
		}
		out = append(out, r)
	}
	return out
}

func lastAgentText(t *testing.T, e *failEnv, run am.Run) map[string]any {
	t.Helper()
	items, err := e.store.ThreadItemsAfter(context.Background(), e.install.ID, nil, 0, 500)
	if err != nil {
		t.Fatal(err)
	}
	for i := len(items) - 1; i >= 0; i-- {
		if it := items[i]; it.Kind == "agent_text" && it.RunID != nil && *it.RunID == run.ID {
			var p map[string]any
			_ = json.Unmarshal(it.Payload, &p)
			return p
		}
	}
	t.Fatal("the round says nothing in the thread")
	return nil
}

func progressOf(t *testing.T, e *failEnv, run am.Run) map[string]any {
	t.Helper()
	it, err := e.store.ItemFor(context.Background(), e.install.ID, "progress", "run_id", run.ID.String())
	if err != nil {
		t.Fatal(err)
	}
	var p map[string]any
	_ = json.Unmarshal(it.Payload, &p)
	return p
}

func TestACrashedRoundCostsThePersonNothing(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	run := e.round(am.KindRun, true)
	// two steps finished and were charged before the third crashed
	_ = e.store.Charge(ctx, run.InstallID, run.ID, "k-parse", 1, "", nil)
	_ = e.store.Charge(ctx, run.InstallID, run.ID, "k-compute", 11.5, "", nil)
	_ = e.r.fail(ctx, run, "render_failed", errors.New(rawErrors[0]))
	got := runJSON(t, e, run.ID)
	if got["status"] != am.RunFailed || got["credits_spent"].(float64) != 0 {
		t.Fatalf("a round that delivered nothing is charged nothing: status %v, charged %v", got["status"], got["credits_spent"])
	}
	if got["outcome"] != "failed" {
		t.Fatalf("outcome: %v", got["outcome"])
	}
	crashed := lastAgentText(t, e, run)
	sent, _ := json.Marshal(crashed)
	t.Logf("the thread item of a crashed round: %s", sent)
	f := crashed["failure"].(map[string]any)
	if f["credits"].(float64) != 0 || !strings.Contains(f["charged"].(string), "ไม่คิดเครดิต") || strings.Contains(f["charged"].(string), "12.5") {
		t.Fatalf("the round says it was not charged: %v", f)
	}
	// the rows are kept for the admin, marked; the run keeps what the platform paid
	rows := e.ledger(run.ID)
	if len(rows) != 2 {
		t.Fatalf("the ledger keeps both rows: %+v", rows)
	}
	for _, r := range rows {
		if r.voided == nil || !strings.Contains(r.reason, "render_failed") {
			t.Fatalf("voided, with why: %+v", r)
		}
	}
	if got["credits_cost"].(float64) != 12.5 {
		t.Fatalf("the real cost is kept: %v", got["credits_cost"])
	}
	if spent, _ := e.store.SpentThisMonth(ctx, run.InstallID); spent != 0 {
		t.Fatalf("the install's month counts nothing of it: %v", spent)
	}
	// ending twice voids once
	first := *rows[0].voided
	_ = e.store.Finish(ctx, run.ID, "", am.RunFailed, "render_failed")
	_ = e.r.fail(ctx, run, "render_failed", errors.New(rawErrors[0]))
	again := e.ledger(run.ID)
	if len(again) != 2 || !again[0].voided.Equal(first) || runJSON(t, e, run.ID)["credits_cost"].(float64) != 12.5 {
		t.Fatalf("idempotent: %+v", again)
	}
	// a step that finishes after the round has ended (the runner was mid-step) is a cost, never a charge
	_ = e.store.Charge(ctx, run.InstallID, run.ID, "k-late", 2, "", nil)
	late := runJSON(t, e, run.ID)
	if late["credits_spent"].(float64) != 0 || late["credits_cost"].(float64) != 14.5 {
		t.Fatalf("a late step: charged %v, cost %v", late["credits_spent"], late["credits_cost"])
	}
	// a cancelled round delivered nothing either
	stopped := e.round(am.KindRun, true)
	_ = e.store.Charge(ctx, stopped.InstallID, stopped.ID, "k-parse", 3, "", nil)
	if _, err := e.store.RequestCancel(ctx, stopped.TenantID, stopped.UserID, stopped.ID); err != nil {
		t.Fatal(err)
	}
	_ = e.store.Finish(ctx, stopped.ID, e.r.ID, am.RunCancelled, "cancelled_by_user")
	c := runJSON(t, e, stopped.ID)
	if c["outcome"] != "cancelled" || c["credits_spent"].(float64) != 0 || c["credits_cost"].(float64) != 3 {
		t.Fatalf("a cancelled round: %v", c)
	}
}

func TestARoundThatDoesNotTieIsCompletedAndCharged(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	run := e.round(am.KindRun, true)
	_ = e.store.Charge(ctx, run.InstallID, run.ID, "k-compute", 1134.58, "", nil)
	g, _ := gateOf([]byte(notTied))
	_ = e.r.fail(ctx, run, g.Reason(), &gateErr{gate: *g, draft: true})
	got := runJSON(t, e, run.ID)
	// the database's status is what it was: sign-off, the index and retry depend on it
	if got["status"] != am.RunFailed || got["status_reason"] != "not_tied" {
		t.Fatalf("status: %v %v", got["status"], got["status_reason"])
	}
	if got["outcome"] != "completed_open" {
		t.Fatalf("a round that does not tie is completed, not failed: outcome %v", got["outcome"])
	}
	if got["credits_spent"].(float64) != 1134.58 || got["credits_cost"].(float64) != 1134.58 {
		t.Fatalf("the work was delivered and is charged: %v / %v", got["credits_spent"], got["credits_cost"])
	}
	for _, r := range e.ledger(run.ID) {
		if r.voided != nil {
			t.Fatalf("nothing is voided: %+v", r)
		}
	}
	p := lastAgentText(t, e, run)
	sent, _ := json.Marshal(p)
	t.Logf("the thread item: %s", sent)
	if p["tone"] != "open" || p["failure"] != nil {
		t.Fatalf("not an error: tone %v, failure %v", p["tone"], p["failure"])
	}
	c, _ := p["completion"].(map[string]any)
	if c == nil || c["code"] != "not_tied" || c["title"] != "เสร็จแล้ว · ยังไม่ลงตัว" {
		t.Fatalf("completion: %v", p)
	}
	if c["what"] != "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท" || c["next"] != "ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร" {
		t.Fatalf("the package's own sentences: %v", c)
	}
	if s, _ := c["files"].(string); !strings.Contains(s, "ร่าง") {
		t.Fatalf("the draft is there and says so: %v", c["files"])
	}
	if s, _ := c["charged"].(string); c["credits"].(float64) != 1134.58 || !strings.Contains(s, "1,134.58 เครดิต") || !strings.Contains(s, "ส่งไฟล์ให้แล้ว") {
		t.Fatalf("what was charged, and why: %v", c)
	}
	text, _ := p["text"].(string)
	for _, part := range []string{"title", "what", "next", "files", "charged"} {
		if !strings.Contains(text, c[part].(string)) {
			t.Fatalf("the message's text carries %s: %q", part, text)
		}
	}
	readable(t, "a round that does not tie", e.seen())
	// the progress card ends as finished, not as failed
	pr := progressOf(t, e, run)
	sent, _ = json.Marshal(pr)
	t.Logf("the progress card: %s", sent)
	if pr["outcome"] != "completed_open" {
		t.Fatalf("the card: %v", pr)
	}
	for _, s := range pr["steps"].([]any) {
		if s.(map[string]any)["status"] == "failed" {
			t.Fatalf("no line of the card reads as failed: %v", pr["steps"])
		}
	}
	// a gate whose draft was never published delivered nothing: that is a failure, and free
	bare := e.round(am.KindRun, true)
	_ = e.store.Charge(ctx, bare.InstallID, bare.ID, "k-compute", 9, "", nil)
	_ = e.r.fail(ctx, bare, g.Reason(), &gateErr{gate: *g, draft: false})
	b := runJSON(t, e, bare.ID)
	if b["outcome"] != "failed" || b["credits_spent"].(float64) != 0 {
		t.Fatalf("nothing delivered: %v %v", b["outcome"], b["credits_spent"])
	}
	// a reason that merely looks like a gate's is not one
	fake := e.round(am.KindRun, true)
	_ = e.r.fail(ctx, fake, "not_tied", errors.New(rawErrors[0]))
	if o := runJSON(t, e, fake.ID)["outcome"]; o != "failed" {
		t.Fatalf("recorded at the time, never guessed from the code: %v", o)
	}
}

func TestAReplyThatGaveNoUsableAnswerIsNotCharged(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	parent := e.round(am.KindRun, true)
	// the round's computed table, as its compute step left it
	computed := []byte(`{"quotable":{"revenue":"12,480,300.00"},"citations":[]}`)
	stepKey := am.StepKey(parent.ID, "compute", "am6")
	key := am.BlobKey(parent.TenantID, parent.ID, stepKey, "computed.json")
	if err := e.r.Files.Upload(ctx, key, strings.NewReader(string(computed)), "application/json", int64(len(computed))); err != nil {
		t.Fatal(err)
	}
	st, _, err := e.store.BeginStep(ctx, parent.ID, "compute", "compute", stepKey, json.RawMessage(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	out, _ := json.Marshal(map[string]any{"computed": map[string]any{"key": key}, "computed_key": key, "summary": map[string]any{}})
	if err := e.store.FinishStep(ctx, st.ID, "succeeded", out, "", "scripts/metrics.py", "", 1); err != nil {
		t.Fatal(err)
	}
	type rp = struct {
		content string
		cost    float64
		cached  bool
	}
	ask := func(replies []rp) (am.Run, string) {
		t.Helper()
		pid := parent.ID
		job, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: parent.InstallID, ParentRunID: &pid, TenantID: parent.TenantID,
			UserID: parent.UserID, Kind: am.KindReply, Message: "รายได้รวมเป็นเท่าไร", DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
		if err != nil {
			t.Fatal(err)
		}
		s := costGateway(t, replies)
		r := &Runner{Store: e.store, Files: e.r.Files, Log: slog.Default(), ID: e.r.ID, Now: time.Now, CreditsPerUSD: 1000,
			Model: &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "primary", SelfHosted: map[string]bool{"primary": true},
				Timeout: 5 * time.Second, HTTP: http.DefaultClient}}
		var m am.Manifest
		_ = json.Unmarshal([]byte(`{"capabilities":{"required":["model.text"]}}`), &m)
		rc := &runCtx{ctx: ctx, r: r, run: job, install: e.install, pkg: &Package{Manifest: m}, log: slog.Default()}
		return job, r.answerQuestion(ctx, rc, parent, "รายได้รวมเป็นเท่าไร").String()
	}
	// every answer refused (a number the table does not have): the canned sentence is what the person gets
	job, said := ask([]rp{{`{"answer":"รายได้รวม 99,999.00 บาท"}`, 0.002, false}, {`{"answer":"รายได้รวม 88,888.00 บาท"}`, 0.003, false}})
	if !strings.Contains(said, "ยังตอบคำถามนี้") {
		t.Fatalf("no usable answer: %q", said)
	}
	got := runJSON(t, e, job.ID)
	if got["credits_spent"].(float64) != 0 {
		t.Fatalf("an answer the person did not get is not charged: %v", got["credits_spent"])
	}
	if got["credits_cost"].(float64) != 5 {
		t.Fatalf("what the platform paid is kept: %v", got["credits_cost"])
	}
	rows := e.ledger(job.ID)
	if len(rows) != 1 || rows[0].voided == nil || rows[0].credits != 5 {
		t.Fatalf("the row is kept, voided: %+v", rows)
	}
	// an answer the person got is charged
	job, said = ask([]rp{{`{"answer":"รายได้รวม 12,480,300.00 บาท"}`, 0.002, false}})
	if !strings.Contains(said, "12,480,300.00") {
		t.Fatalf("the answer: %q", said)
	}
	if got := runJSON(t, e, job.ID); got["credits_spent"].(float64) != 2 || got["credits_cost"].(float64) != 2 {
		t.Fatalf("a usable answer is charged: %v / %v", got["credits_spent"], got["credits_cost"])
	}
}
