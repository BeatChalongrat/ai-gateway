package runner

// Integration tests: a private Postgres database (testdb), local object
// storage in a temp dir, the REAL sandbox image through Docker, and a fake
// model gateway. They need Docker and ai-gateway-sandbox, so they run only
// with AGENT_MARKET_IT=1 (scripts/agent-market/go-it-lane.sh sets it).

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func repoRoot(t *testing.T) string {
	dir, _ := os.Getwd()
	for i := 0; i < 8; i++ {
		if _, err := os.Stat(filepath.Join(dir, ".git")); err == nil {
			return dir
		}
		dir = filepath.Dir(dir)
	}
	t.Fatal("repo root not found")
	return ""
}

type itEnv struct {
	t       *testing.T
	store   *am.Store
	files   storage.Backend
	engine  *Engine
	sb      *sandbox.Runner
	model   *httptest.Server
	calls   atomic.Int32
	lying   atomic.Bool
	root    string
	install am.Install
}

func newIT(t *testing.T) *itEnv {
	t.Helper()
	if os.Getenv("AGENT_MARKET_IT") != "1" {
		t.Skip("set AGENT_MARKET_IT=1 (needs Docker + ai-gateway-sandbox)")
	}
	pool := testdb.Fresh(t)
	if pool == nil {
		return nil
	}
	ctx := context.Background()
	if _, err := am.Migrate(ctx, pool); err != nil {
		t.Fatal(err)
	}
	root := repoRoot(t)
	files, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	engine, err := LoadEngine("", filepath.Join(root, "backend", "agent_market"))
	if err != nil {
		t.Fatal(err)
	}
	work := t.TempDir()
	sb := sandbox.New(sandbox.Config{Image: envOr("AM_SANDBOX_IMAGE", "ai-gateway-sandbox:latest"),
		SeccompPath: filepath.Join(root, "services", "chat-edge-api", "sandbox", "seccomp.json"), WorkDir: work})
	e := &itEnv{t: t, store: am.NewStore(pool), files: files, engine: engine, sb: sb, root: root}
	e.model = httptest.NewServer(http.HandlerFunc(e.fakeModel))
	t.Cleanup(e.model.Close)
	return e
}

func envOr(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

// fakeModel answers like the gateway. The narrative quotes no numbers (so
// the guard passes) unless the test asks it to lie.
func (e *itEnv) fakeModel(w http.ResponseWriter, r *http.Request) {
	e.calls.Add(1)
	body, _ := io.ReadAll(r.Body)
	content := `{"summary":"ภาพรวมของงวดนี้ รายได้และค่าใช้จ่ายเปลี่ยนไปตามตาราง","movements":["ค่าที่ปรึกษาเพิ่มขึ้นมาก"],"watch":["ตรวจค่าที่ปรึกษา"],"next_steps":["ทบทวนสัญญาที่ปรึกษา"]}`
	if e.lying.Load() {
		content = `{"summary":"รายได้ 99,999,999 บาท","movements":["x"],"watch":["x"],"next_steps":["x"]}`
	}
	if strings.Contains(string(body), "choice") {
		content = `{"choice": null}`
	}
	model := "fake-self-hosted"
	if strings.Contains(string(body), "image_url") {
		// AM2: a vision reading of a scan (the Phase A truth of s01)
		model = "fake-vision"
		content = `{"seller_name": "บริษัท ไทยออฟฟิศ ซัพพลาย จำกัด", "seller_tax_id": "0105545009875", "document_number": "IV6909-0015",` +
			` "document_date": "2026-09-15", "total": 12850.70, "vat_amount": 840.70, "line_items": []}`
	}
	b, _ := json.Marshal(map[string]any{"model": model, "choices": []any{map[string]any{
		"message": map[string]any{"content": content}, "finish_reason": "stop"}},
		"usage": map[string]any{"total_tokens": 1000, "estimated_cost_usd": 0.0002}})
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(b)
}

func (e *itEnv) runner(id string) *Runner {
	return &Runner{Store: e.store, Files: e.files, Engine: e.engine, Log: slog.Default(),
		Exec: &Executor{SB: e.sb, Engine: e.engine, Limits: sandbox.Limits{Wall: 180 * time.Second, MemoryMB: 1536, CPUs: "1.0",
			PidsLimit: 256, MaxOutputB: 8 << 20, ScratchMB: 512}},
		Model: &ModelClient{BaseURL: e.model.URL + "/v1", APIKey: "test", Primary: "fake-self-hosted",
			SelfHosted: map[string]bool{"fake-self-hosted": true}, Timeout: 30 * time.Second, MaxTokens: 2000, HTTP: http.DefaultClient},
		ID: id, Version: "it", SandboxImage: "ai-gateway-sandbox:latest", SandboxReady: true, Slots: 1,
		Lease: 3 * time.Second, Poll: 100 * time.Millisecond, CreditsPerUSD: 3000000, SandboxCreditsPerSecond: 100}
}

func (e *itEnv) setupInstall(budget float64) am.Install {
	e.t.Helper()
	ctx := context.Background()
	if err := e.runner("reg").Register(ctx); err != nil {
		e.t.Fatal(err)
	}
	def, err := e.store.DefinitionBySlug(ctx, "sran.financial-report")
	if err != nil {
		e.t.Fatal(err)
	}
	tx, _ := e.store.Pool.Begin(ctx)
	in, _, err := e.store.CreateInstall(ctx, tx, am.Install{TenantID: uuid.New(), UserID: uuid.New(), DefinitionID: def.ID,
		Slug: def.Slug, DefinitionVersion: def.Version, Checksum: def.Checksum, Title: "ปิดงบ", BindingVersion: 1,
		BudgetCredits: budget}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	_ = e.store.SetDefault(ctx, tx, in.ID, am.Default{Key: "formats", Value: json.RawMessage(`["docx","pptx"]`), Source: "install"})
	_ = e.store.SetDefault(ctx, tx, in.ID, am.Default{Key: "company", Value: json.RawMessage(`"บริษัท ทดสอบ จำกัด"`), Source: "install"})
	if err := tx.Commit(ctx); err != nil {
		e.t.Fatal(err)
	}
	e.install = in
	return in
}

func (e *itEnv) addFile(run am.Run, rel string) am.File {
	e.t.Helper()
	ctx := context.Background()
	path := filepath.Join(e.root, "backend", "agent_market", "packages", "financial_report", filepath.FromSlash(rel))
	data, err := os.ReadFile(path)
	if err != nil {
		e.t.Fatal(err)
	}
	id := uuid.New()
	name := filepath.Base(rel)
	key := am.UploadKey(run.TenantID, run.InstallID, id, name)
	if err := e.files.Upload(ctx, key, strings.NewReader(string(data)), "", int64(len(data))); err != nil {
		e.t.Fatal(err)
	}
	rid := run.ID
	f, err := e.store.CreateFile(ctx, e.store.Pool, am.File{ID: id, TenantID: run.TenantID, UserID: run.UserID, InstallID: run.InstallID,
		RunID: &rid, Name: name, Size: int64(len(data)), SHA256: fmt.Sprintf("%x", sha(data)), StorageKey: key, Status: "active"})
	if err != nil {
		e.t.Fatal(err)
	}
	return f
}

func (e *itEnv) newRun(message string, files ...string) am.Run {
	e.t.Helper()
	ctx := context.Background()
	in := e.install
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Kind: am.KindRun, Message: message, DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum,
		Formats: json.RawMessage(`["docx","pptx"]`)}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for _, f := range files {
		e.addFile(run, f)
	}
	rid := run.ID
	_, _, _ = e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, &rid, am.Progress{RunID: run.ID.String()}, nil)
	return run
}

// drive runs a runner until cond holds for the run (or fails the test).
func (e *itEnv) drive(r *Runner, runID uuid.UUID, timeout time.Duration, cond func(am.Run) bool) am.Run {
	e.t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { _ = r.Start(ctx); close(done) }()
	defer func() { cancel(); <-done }()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		run, err := e.store.RunByID(context.Background(), runID)
		if err == nil && cond(run) {
			return run
		}
		time.Sleep(200 * time.Millisecond)
	}
	run, _ := e.store.RunByID(context.Background(), runID)
	steps, _ := e.store.ListSteps(context.Background(), runID)
	for _, s := range steps {
		e.t.Logf("step %d %s %s %s", s.Seq, s.Name, s.Status, trim(s.Error, 300))
	}
	e.t.Fatalf("run %s did not reach the condition; status=%s reason=%s", runID, run.Status, run.StatusReason)
	return run
}

func sha(b []byte) []byte {
	s := sha256.Sum256(b)
	return s[:]
}

func terminal(r am.Run) bool { return am.IsTerminal(r.Status) }
func parked(r am.Run) bool   { return r.Status == am.RunAwaitingInput }

func (e *itEnv) steps(runID uuid.UUID) []am.Step {
	s, err := e.store.ListSteps(context.Background(), runID)
	if err != nil {
		e.t.Fatal(err)
	}
	return s
}

func countSteps(steps []am.Step, prefix string) int {
	n := 0
	for _, s := range steps {
		if strings.HasPrefix(s.Name, prefix) {
			n++
		}
	}
	return n
}

func TestCleanRunEndToEnd(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx", "sample-data/งบประมาณ_2569.xlsx")
	got := e.drive(e.runner("r1"), run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("clean run must succeed: %s %s", got.Status, got.StatusReason)
	}
	cards, _ := e.store.RunCards(context.Background(), run.ID)
	if len(cards) != 0 {
		t.Fatalf("clean input asks zero questions, got %d cards", len(cards))
	}
	steps := e.steps(run.ID)
	for _, want := range []string{"parse:", "preflight", "compute", "postcompute", "narrative", "render", "publish"} {
		if countSteps(steps, want) == 0 {
			t.Fatalf("every step writes a row: %s missing", want)
		}
	}
	for _, s := range steps {
		if s.Status != "succeeded" || s.Attempt != 1 {
			t.Fatalf("step %s: %s attempt %d", s.Name, s.Status, s.Attempt)
		}
	}
	arts, _ := e.store.RunArtifacts(context.Background(), run.ID)
	if len(arts) != 2 {
		t.Fatalf("docx + pptx, got %d", len(arts))
	}
	for _, a := range arts {
		if !strings.HasSuffix(a.Name, "_v1."+a.Format) || !strings.Contains(a.Name, "2026-09") {
			t.Fatalf("artifact naming {agent}_{period}_{kind}_v{n}: %s", a.Name)
		}
		var cites []any
		_ = json.Unmarshal(a.Citations, &cites)
		if len(cites) < 20 {
			t.Fatalf("citations stored with the artifact: %d", len(cites))
		}
	}
	if got.ModelUsed == nil || *got.ModelUsed != "fake-self-hosted" || got.ModelSelfHosted == nil || !*got.ModelSelfHosted {
		t.Fatal("the run records which model saw the numbers and whether it was self-hosted")
	}
	// Added format afterwards: no compute step re-runs, same numbers.
	pid := run.ID
	params, _ := json.Marshal(map[string]any{"formats": []string{"xlsx"}})
	job, _, _ := e.store.CreateRun(context.Background(), e.store.Pool, am.Run{InstallID: run.InstallID, ParentRunID: &pid,
		TenantID: run.TenantID, UserID: run.UserID, Kind: am.KindRender, Params: params, DefinitionVersion: run.DefinitionVersion,
		Checksum: run.Checksum}, "")
	jr := e.drive(e.runner("r1"), job.ID, 3*time.Minute, terminal)
	if jr.Status != am.RunSucceeded {
		t.Fatalf("render job: %s %s", jr.Status, jr.StatusReason)
	}
	if n := countSteps(e.steps(run.ID), "compute"); n != 1 {
		t.Fatalf("adding a format must not recompute: %d compute steps", n)
	}
	if n := countSteps(e.steps(job.ID), "compute"); n != 0 {
		t.Fatalf("the render job has no compute step: %d", n)
	}
	arts, _ = e.store.RunArtifacts(context.Background(), run.ID)
	if len(arts) != 3 {
		t.Fatalf("xlsx added to the run: %d artifacts", len(arts))
	}
}

func TestParkRestartResumeWithoutReplay(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบเดือนกันยายน", "evals/fixtures/period_mixed_sep.xlsx")
	got := e.drive(e.runner("r-before-restart"), run.ID, 3*time.Minute, parked)
	cards, _ := e.store.RunCards(context.Background(), run.ID)
	if len(cards) != 1 || cards[0].Status != "open" {
		t.Fatalf("one open card: %+v", cards)
	}
	_ = got
	// The runner process is gone (drive stopped it). The run waits in the DB.
	// Answer the card as the control plane does.
	ctx := context.Background()
	var qs []am.Question
	_ = json.Unmarshal(cards[0].Questions, &qs)
	ans := map[string]am.Answer{"period_mixed": {Value: json.RawMessage(`"exclude"`), Moment: "preflight", CardID: cards[0].ID.String(), Via: "card", Code: "period_mixed"}}
	ab, _ := json.Marshal(ans)
	if _, _, err := e.store.CloseCard(ctx, e.store.Pool, cards[0].ID, "answered", ab, "card", nil); err != nil {
		t.Fatal(err)
	}
	_ = e.store.MergeAnswers(ctx, e.store.Pool, run.ID, ans)
	if ok, _ := e.store.Resume(ctx, e.store.Pool, run.ID); !ok {
		t.Fatal("resume")
	}
	got = e.drive(e.runner("r-after-restart"), run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("resumed run: %s %s", got.Status, got.StatusReason)
	}
	steps := e.steps(run.ID)
	if n := countSteps(steps, "parse:"); n != 1 {
		t.Fatalf("the file is parsed once across the restart: %d", n)
	}
	for _, s := range steps {
		if s.Attempt != 1 {
			t.Fatalf("nothing completed is replayed: %s attempt %d", s.Name, s.Attempt)
		}
	}
	var clar *am.Step
	for i := range steps {
		if strings.HasPrefix(steps[i].Name, "clarify:") {
			clar = &steps[i]
		}
	}
	if clar == nil || clar.Status != "succeeded" || clar.DurationMS == nil {
		t.Fatalf("the pause is a step with its duration: %+v", clar)
	}
	if got.AwaitingMS <= 0 {
		t.Fatal("awaiting time is recorded on the run")
	}
}

func TestKillMidStepResumesWithNoDuplicateArtifact(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx")
	ctx := context.Background()
	// Kill the runner while the render step is running.
	e.drive(e.runner("r-killed"), run.ID, 4*time.Minute, func(r am.Run) bool {
		var n int
		_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_run_steps WHERE run_id=$1 AND name='render' AND status='running'`, r.ID).Scan(&n)
		return n == 1
	})
	// drive cancelled the runner mid-step (like a container kill): the lease is left behind.
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET lease_until = now() - interval '1 second' WHERE id = $1`, run.ID)
	got := e.drive(e.runner("r-new"), run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("resumed after kill: %s %s", got.Status, got.StatusReason)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 2 {
		t.Fatalf("no duplicate artifact after the kill: %d", len(arts))
	}
	for _, s := range e.steps(run.ID) {
		if s.Name == "render" {
			if s.Attempt != 2 {
				t.Fatalf("the interrupted step re-runs once: attempt %d", s.Attempt)
			}
		} else if s.Attempt != 1 {
			t.Fatalf("completed steps are not replayed: %s attempt %d", s.Name, s.Attempt)
		}
	}
}

func TestLateUploadResumesWithoutReparse(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบกันยายน เทียบกับงบประมาณด้วย", "sample-data/GL_2569-09.xlsx")
	e.drive(e.runner("r1"), run.ID, 3*time.Minute, parked)
	cards, _ := e.store.RunCards(context.Background(), run.ID)
	if len(cards) != 1 || cards[0].Kind != "request_files" {
		t.Fatalf("asks for the budget file: %+v", cards)
	}
	// The user comes back later and drops the budget into the same thread.
	e.addFile(run, "sample-data/งบประมาณ_2569.xlsx")
	_, _ = e.store.Resume(context.Background(), e.store.Pool, run.ID)
	got := e.drive(e.runner("r2"), run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("%s %s", got.Status, got.StatusReason)
	}
	steps := e.steps(run.ID)
	gl := 0
	for _, s := range steps {
		if s.Name == "parse:GL_2569-09.xlsx" {
			gl++
			if s.Attempt != 1 {
				t.Fatal("the GL is not parsed again")
			}
		}
	}
	if gl != 1 || countSteps(steps, "compute") != 1 {
		t.Fatalf("GL parsed once (%d) and computed once (%d)", gl, countSteps(steps, "compute"))
	}
	cards, _ = e.store.RunCards(context.Background(), run.ID)
	if cards[0].Status != "answered" {
		t.Fatalf("the request-files card closes when the file arrives: %s", cards[0].Status)
	}
}

// AM3: a budget that runs out is a wait, never a failure: the run parks with
// a notice that names who to ask, and goes on from where it stopped once
// there is budget again (the hourly pass, or an admin's change).
func TestBudgetParksTheRunAndItGoesOnWhenRaised(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.setupInstall(1) // one credit: the first costly step spends it
	run := e.newRun("ปิดงบกันยายน", "sample-data/GL_2569-09.xlsx")
	r := e.runner("r1")
	got := e.drive(r, run.ID, 3*time.Minute, func(x am.Run) bool { return parked(x) || terminal(x) })
	if got.Status != am.RunAwaitingInput || got.StatusReason != "budget_install" {
		t.Fatalf("budget wait: %s %s", got.Status, got.StatusReason)
	}
	items, _ := e.store.ItemsAfter(ctx, in.ID, 0, 200)
	var notice map[string]any
	for _, it := range items {
		var p map[string]any
		_ = json.Unmarshal(it.Payload, &p)
		if it.Kind == "notice" && p["action"] == "resume_budget" {
			notice = p
		}
	}
	if notice == nil || !strings.Contains(notice["text"].(string), "หยุดรอ") || !strings.Contains(notice["text"].(string), "ยังอยู่ครบ") {
		t.Fatalf("the notice names the budget and who to ask: %v", notice)
	}
	parsed := countSteps(e.steps(run.ID), "parse:")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET budget_credits = 3000000 WHERE id = $1`, in.ID); err != nil {
		t.Fatal(err)
	}
	if n, err := e.store.ResumeBudgetParked(ctx, ""); err != nil || n != 1 {
		t.Fatalf("resumed: %d %v", n, err)
	}
	got = e.drive(r, run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("after the budget was raised: %s %s", got.Status, got.StatusReason)
	}
	if countSteps(e.steps(run.ID), "parse:") != parsed {
		t.Fatal("the run went on from where it stopped: the file was not parsed again")
	}
}

func TestLyingModelFallsBackToRules(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.lying.Store(true)
	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบกันยายน", "sample-data/GL_2569-09.xlsx")
	got := e.drive(e.runner("r1"), run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("%s %s", got.Status, got.StatusReason)
	}
	for _, s := range e.steps(run.ID) {
		if s.Name == "narrative" {
			var o narrativeOut
			_ = json.Unmarshal(s.Output, &o)
			var n map[string]any
			_ = json.Unmarshal(o.Narrative, &n)
			if n["written_by"] != "rules" || len(o.Tried) == 0 {
				t.Fatalf("a narrative with a made-up number must be refused: %+v", n)
			}
		}
	}
}

func TestRepromptLimitThenDefault(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบเดือนกันยายน", "evals/fixtures/period_mixed_sep.xlsx")
	ctx := context.Background()
	e.drive(e.runner("r1"), run.ID, 3*time.Minute, parked)
	for round := 1; round <= 3; round++ {
		cards, _ := e.store.RunCards(ctx, run.ID)
		c := cards[len(cards)-1]
		if _, changed, err := e.store.MarkInterpreting(ctx, e.store.Pool, c.ID, "อืม ไม่รู้สิ", nil); err != nil || !changed {
			t.Fatalf("round %d: %v %v (status %s)", round, err, changed, c.Status)
		}
		_, _ = e.store.Resume(ctx, e.store.Pool, run.ID)
		if round < 3 {
			e.drive(e.runner("r1"), run.ID, 2*time.Minute, parked)
			cards, _ = e.store.RunCards(ctx, run.ID)
			if cards[len(cards)-1].Attempts != round || cards[len(cards)-1].Status != "open" {
				t.Fatalf("round %d: re-asked with attempts=%d status=%s", round, cards[len(cards)-1].Attempts, cards[len(cards)-1].Status)
			}
		}
	}
	got := e.drive(e.runner("r1"), run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("%s %s", got.Status, got.StatusReason)
	}
	cards, _ := e.store.RunCards(ctx, run.ID)
	if cards[0].Status != "defaulted" {
		t.Fatalf("after 2 re-asks the default is used: %s", cards[0].Status)
	}
	var as []am.Assumption
	_ = json.Unmarshal(got.Assumptions, &as)
	found := false
	for _, a := range as {
		if a.Source == "reprompt_default" && a.Code == "period_mixed" {
			found = true
		}
	}
	if !found {
		t.Fatalf("the defaulted answer is written as an assumption: %+v", as)
	}
}

func TestSandboxHasNoNetworkAndNoOtherWorkspace(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	code := `import socket, os, glob
try:
    socket.create_connection(("1.1.1.1", 53), timeout=3); print("NET_OPEN")
except Exception as exc:
    print("NET_BLOCKED", type(exc).__name__)
print("SEES", sorted(glob.glob("/job/*")), len(glob.glob("/sandbox-runs/*")) + len(glob.glob("/agent-sandbox-runs/*")))
`
	res, err := e.sb.Execute(context.Background(), sandbox.Spec{Mode: sandbox.ModeCode, Code: code, Limits: sandbox.Limits{Wall: 30 * time.Second}})
	if err != nil {
		t.Fatal(err)
	}
	defer res.Cleanup()
	out := string(res.Stdout)
	if !strings.Contains(out, "NET_BLOCKED") || strings.Contains(out, "NET_OPEN") {
		t.Fatalf("a run's sandbox must not reach the network: %s", out)
	}
	if !strings.Contains(out, "SEES ['/job/in', '/job/out'] 0") {
		t.Fatalf("a run sees only its own workspace: %s", out)
	}
}
