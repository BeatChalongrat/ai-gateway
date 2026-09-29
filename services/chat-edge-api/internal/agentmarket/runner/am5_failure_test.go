package runner

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// AM5 phase C — the owner's screen showed a failed round with
// "'NoneType' object is not iterable" in it. No exception text, type name or
// step id reaches a person: not in the thread, not on the progress card. A
// failed round says what happened, what to try next, whether the files are
// safe and whether anything was charged.

// What the runner's failures really carry (sandbox.go, the converter, pgx, Go).
var rawErrors = []string{
	"engine step failed: TypeError: 'NoneType' object is not iterable",
	"engine step failed: KeyError: 'fallback_narrative'",
	"engine step failed: no result (exit 1): Traceback (most recent call last):\n  File \"/job/in/am_engine.zip/agent_market/engine/step.py\", line 152, in run\nValueError: bad input name",
	"sandbox: docker: Error response from daemon: dial tcp 172.18.0.4:2375: connect: connection refused",
	"engine step failed: the step ran past its time limit (3m0s)",
	"quality gate: pdf: แปลงเป็น PDF (converter 502: upstream connect error); docx: หัวข้อครบทุกส่วน (missing: insights)",
	"ERROR: duplicate key value violates unique constraint \"agent_run_steps_pkey\" (SQLSTATE 23505)",
	"render produced no financial-report_2569-09_report_v1.docx",
	"context deadline exceeded",
}

// Everything a caller of fail() or stepFailed() passes as the reason.
var failReasons = []string{"load", "interpret", "files", "clarify", "parse_failed", "vision_failed", "preflight_failed",
	"compute_failed", "postcompute_failed", "narrative_failed", "render_failed", "publish_failed", "index", "exceptions",
	"outputs", "self_test_seed", "quality_gate", "package_changed", "sandbox_unavailable", "engine_missing", "no_period",
	"too_many_rounds", "stamp_failed", "bad_format", "no_parent", "bad_params", "preview", "reply", "internal_error"}

var latinWord = regexp.MustCompile(`[A-Za-z_]{3,}`)

// allowedLatin: what a Thai failure message may say in Latin letters.
var allowedLatin = map[string]bool{"agent": true, "Agent": true, "Market": true, "PDF": true, "docx": true, "DPI": true, "xlsx": true, "pptx": true}

type failEnv struct {
	t       *testing.T
	store   *am.Store
	r       *Runner
	install am.Install
}

func newFailEnv(t *testing.T) *failEnv {
	t.Helper()
	pool := testdb.Fresh(t)
	if pool == nil {
		return nil
	}
	ctx := context.Background()
	if _, err := am.Migrate(ctx, pool); err != nil {
		t.Fatal(err)
	}
	files, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	store := am.NewStore(pool)
	def, err := store.UpsertDefinition(ctx, am.Definition{Slug: "sran.financial-report", Version: "1.0.0", Name: "รายงานการเงิน",
		Manifest: json.RawMessage(`{"schema_version":"agent.market.v1","id":"sran.financial-report","version":"1.0.0","name":"รายงานการเงิน","python_package":"financial_report","permissions":{"default":"read_only"}}`),
		Checksum: "pkgsum", EngineChecksum: "engsum"})
	if err != nil {
		t.Fatal(err)
	}
	tx, err := pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	in, _, err := store.CreateInstall(ctx, tx, am.Install{TenantID: uuid.New(), UserID: uuid.New(), DefinitionID: def.ID, Slug: def.Slug,
		DefinitionVersion: def.Version, Checksum: def.Checksum, Title: "ปิดงบการเงิน", BudgetCredits: 100000}, "")
	if err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	r := &Runner{Store: store, Files: files, Log: slog.Default(), ID: "runner-am5", Now: time.Now}
	return &failEnv{t: t, store: store, r: r, install: in}
}

// round is a claimed round of the person's with one file of theirs.
func (e *failEnv) round(kind string, withFile bool) am.Run {
	e.t.Helper()
	ctx := context.Background()
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: e.install.ID, TenantID: e.install.TenantID, UserID: e.install.UserID,
		Kind: kind, DefinitionVersion: "1.0.0", Checksum: "pkgsum", EngineChecksum: "engsum"}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	if withFile {
		rid := run.ID
		if _, err := e.store.CreateFile(ctx, e.store.Pool, am.File{ID: uuid.New(), TenantID: run.TenantID, UserID: run.UserID, InstallID: run.InstallID,
			RunID: &rid, Name: "GL_2569-09.xlsx", Size: 10, SHA256: "x", StorageKey: "k/" + run.ID.String(), Status: "active"}); err != nil {
			e.t.Fatal(err)
		}
	}
	rid := run.ID
	if _, _, err := e.store.AddItem(ctx, e.store.Pool, run.InstallID, run.TenantID, run.UserID, &rid, am.QueuedProgress(run), nil); err != nil {
		e.t.Fatal(err)
	}
	claimed, err := e.store.ClaimRun(ctx, e.r.ID, time.Minute)
	if err != nil {
		e.t.Fatalf("claim: %v", err)
	}
	if claimed.ID != run.ID {
		e.t.Fatalf("claimed another run")
	}
	return claimed
}

// seen is every string a person can read in the install's thread: the text
// of the agent's messages and notices, the four parts of a failure, the
// progress card's lines, the files card's checks.
func (e *failEnv) seen() []string {
	e.t.Helper()
	items, err := e.store.ThreadItemsAfter(context.Background(), e.install.ID, nil, 0, 500)
	if err != nil {
		e.t.Fatal(err)
	}
	var out []string
	for _, it := range items {
		out = append(out, visible(e.t, it.Kind, it.Payload)...)
	}
	return out
}

func visible(t *testing.T, kind string, payload json.RawMessage) []string {
	t.Helper()
	var p struct {
		Text    string `json:"text"`
		Failure *struct {
			What, Next, Files, Charged string
			Failed                     []string `json:"failed_checks"`
		} `json:"failure"`
		Steps []struct{ Label, Detail string } `json:"steps"`
	}
	if err := json.Unmarshal(payload, &p); err != nil {
		t.Fatalf("%s payload: %v", kind, err)
	}
	out := []string{p.Text}
	if p.Failure != nil {
		out = append(out, p.Failure.What, p.Failure.Next, p.Failure.Files, p.Failure.Charged)
		out = append(out, p.Failure.Failed...)
	}
	for _, s := range p.Steps {
		out = append(out, s.Label, s.Detail)
	}
	return out
}

// readable fails the test when a person would read something technical.
func readable(t *testing.T, what string, texts []string) {
	t.Helper()
	for _, s := range texts {
		for _, w := range latinWord.FindAllString(s, -1) {
			if !allowedLatin[w] && !strings.HasSuffix(s, ".xlsx") {
				t.Fatalf("%s: a person reads %q in %q", what, w, s)
			}
		}
	}
}

func lastFailure(t *testing.T, e *failEnv, run am.Run) (text string, f map[string]any) {
	t.Helper()
	items, err := e.store.ThreadItemsAfter(context.Background(), e.install.ID, nil, 0, 500)
	if err != nil {
		t.Fatal(err)
	}
	for i := len(items) - 1; i >= 0; i-- {
		it := items[i]
		if it.Kind != "agent_text" || it.RunID == nil || *it.RunID != run.ID {
			continue
		}
		var p map[string]any
		_ = json.Unmarshal(it.Payload, &p)
		if p["tone"] == "error" {
			text, _ = p["text"].(string)
			f, _ = p["failure"].(map[string]any)
			return text, f
		}
	}
	t.Fatalf("the failed round says nothing in the thread")
	return "", nil
}

func TestAFailedRoundNeverShowsTheErrorItFailedWith(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	for i, reason := range failReasons {
		raw := rawErrors[i%len(rawErrors)]
		run := e.round(am.KindRun, true)
		_ = e.r.fail(context.Background(), run, reason, errors.New(raw))
		got, err := e.store.RunByID(context.Background(), run.ID)
		if err != nil || got.Status != am.RunFailed {
			t.Fatalf("%s: the round is failed: %v %v", reason, got.Status, err)
		}
		readable(t, reason, e.seen())
		// the error itself is kept where it belongs: on the run, for the run page and a problem report
		steps, _ := e.store.ListSteps(context.Background(), run.ID)
		kept := false
		for _, s := range steps {
			kept = kept || (s.Status == "failed" && strings.Contains(s.Error, raw[:20]))
		}
		if !kept {
			t.Fatalf("%s: the run keeps its error for the run page: %v", reason, steps)
		}
		text, f := lastFailure(t, e, run)
		if f == nil {
			t.Fatalf("%s: the failure has its four parts: %q", reason, text)
		}
		for _, part := range []string{"what", "next", "files", "charged"} {
			if s, _ := f[part].(string); strings.TrimSpace(s) == "" {
				t.Fatalf("%s: the failure says %q: %v", reason, part, f)
			}
		}
		for _, part := range []string{"what", "next", "files", "charged"} {
			if !strings.Contains(text, f[part].(string)) {
				t.Fatalf("%s: the message's text carries all four parts (%s missing): %q", reason, part, text)
			}
		}
	}
}

func TestAFailedRoundSaysWhatItChargedAndThatTheFilesAreKept(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	// nothing finished before it stopped: nothing charged
	free := e.round(am.KindRun, true)
	_ = e.r.fail(ctx, free, "parse_failed", errors.New(rawErrors[0]))
	_, f := lastFailure(t, e, free)
	if f["charged"] != "รอบนี้ไม่คิดเครดิต เพราะยังไม่ได้ส่งงานให้" || f["credits"].(float64) != 0 {
		t.Fatalf("nothing was charged: %v", f)
	}
	if f["can_retry"] != true || !strings.Contains(f["files"].(string), "ยังอยู่ครบ") || !strings.Contains(f["files"].(string), "7 วัน") {
		t.Fatalf("the files are safe for 7 days and the round can be tried again with them: %v", f)
	}
	// AM6: two steps finished before the third failed — the round delivered nothing, so it is charged
	// nothing, and says so (AM5 charged the finished steps: 12.5 credits)
	paid := e.round(am.KindRun, true)
	_ = e.store.Charge(ctx, paid.InstallID, paid.ID, "k-parse", 1, "", nil)
	_ = e.store.Charge(ctx, paid.InstallID, paid.ID, "k-compute", 11.5, "", nil)
	_ = e.r.fail(ctx, paid, "render_failed", errors.New(rawErrors[0]))
	_, f = lastFailure(t, e, paid)
	if f["credits"].(float64) != 0 || f["charged"] != "รอบนี้ไม่คิดเครดิต เพราะยังไม่ได้ส่งงานให้" {
		t.Fatalf("a round that delivered nothing is not charged: %v", f)
	}
	// a round without a file of the person's (a self-test) offers no "same files"
	st := e.round(am.KindSelfTest, false)
	_ = e.r.fail(ctx, st, "compute_failed", errors.New(rawErrors[1]))
	if _, f = lastFailure(t, e, st); f["can_retry"] != false || strings.Contains(f["next"].(string), "ไฟล์เดิม") {
		t.Fatalf("nothing to retry with: %v", f)
	}
}

func TestTheProgressCardOfAFailedRenderSaysNothingTechnical(t *testing.T) {
	now := time.Now()
	run := am.Run{ID: uuid.New(), Kind: am.KindRun, Status: am.RunFailed, StatusReason: "render_failed", StartedAt: &now, FinishedAt: &now}
	for _, raw := range rawErrors {
		steps := []am.Step{
			{Name: "parse:GL_2569-09.xlsx", Status: "succeeded"},
			{Name: "preflight", Status: "succeeded", Output: json.RawMessage(`{}`)},
			{Name: "compute", Status: "succeeded", Output: json.RawMessage(`{}`)},
			{Name: "narrative", Status: "failed", Error: raw},
			{Name: "render", Status: "failed", Error: raw},
		}
		p := BuildProgress(run, steps, now)
		b, _ := json.Marshal(p)
		readable(t, "progress", visible(t, "progress", b))
		var failed bool
		for _, s := range p.Steps {
			failed = failed || s.Status == "failed"
		}
		if !failed {
			t.Fatalf("the card still shows which step failed: %v", p.Steps)
		}
	}
}

func TestACrashedRoundIsSaidInTheThread(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	run := e.round(am.KindRun, true)
	// what the claim loop's recover() does with a panic
	e.r.crashed(run, "runtime error: assignment to entry in nil map\n\ngoroutine 41 [running]:\nrunner/deliver.go:111 +0x1f")
	got, _ := e.store.RunByID(context.Background(), run.ID)
	if got.Status != am.RunFailed || got.StatusReason != "internal_error" {
		t.Fatalf("failed as an internal error: %v %v", got.Status, got.StatusReason)
	}
	text, f := lastFailure(t, e, run)
	if f == nil || !strings.Contains(text, "แจ้งปัญหา") {
		t.Fatalf("a crash is not a silent failure: %q", text)
	}
	readable(t, "crash", e.seen())
	// the progress card does not stay "working"
	items, _ := e.store.ThreadItemsAfter(context.Background(), e.install.ID, nil, 0, 500)
	for _, it := range items {
		if it.Kind == "progress" {
			var p am.Progress
			_ = json.Unmarshal(it.Payload, &p)
			if p.Status != am.RunFailed {
				t.Fatalf("the card follows the round: %v", p.Status)
			}
		}
	}
}

func TestANarrativeWithANullListIsAnEmptyList(t *testing.T) {
	// what the model sent on the owner's two failed rounds: a list left null,
	// which the report's renderer then tried to walk
	cv := computedView{}
	for _, s := range []string{
		`{"summary":"ภาพรวมของงวดนี้","movements":["ค่าที่ปรึกษาเพิ่มขึ้น"],"watch":null,"next_steps":null}`,
		`{"summary":"ภาพรวมของงวดนี้","movements":"ไม่มี","watch":[null, "ตรวจค่าที่ปรึกษา", 7, {"a":1}],"next_steps":[]}`,
		`{"summary":"ภาพรวมของงวดนี้"}`,
	} {
		v, err := checkNarrative(s, cv)
		if err != nil {
			t.Fatalf("%s: %v", s, err)
		}
		for _, k := range []string{"movements", "watch", "next_steps"} {
			arr, ok := v[k].([]any)
			if !ok {
				t.Fatalf("%s is a list, never null or a sentence: %T in %s", k, v[k], s)
			}
			for _, x := range arr {
				if str, ok := x.(string); !ok || strings.TrimSpace(str) == "" {
					t.Fatalf("%s holds sentences only: %v", k, arr)
				}
			}
		}
	}
	if _, err := checkNarrative(`{"summary":"  "}`, cv); err == nil {
		t.Fatal("a narrative without a summary is refused")
	}
}

// No call that writes to the thread, a progress line or a quality check may
// be handed an error's text. AM5 checked it in the source, line by line.
// AM6: it does not compile — every writer takes an am.PersonText
// (TestThreadWritersTakeTextBuiltForAPerson, in the package that owns the
// type) — and what is left to check in the source is the one way a string
// becomes a PersonText: a fact, or a package's or a model's sentence. None of
// them may be handed an error.
func TestNoErrorTextIsWrittenWhereAPersonReads(t *testing.T) {
	for _, dir := range []string{".", ".."} {
		files, err := filepath.Glob(filepath.Join(dir, "*.go"))
		if err != nil {
			t.Fatal(err)
		}
		makes := regexp.MustCompile(`\b(?:am\.)?(?:Text|Join|Name|Typed|Theirs|Aside|FileName|FileNames|Email|ModelName|Version|Said|FromPackage|FromModel)\(`)
		raw := regexp.MustCompile(`\.Error\(\)|\bs\.Error\b|\berr\)|\berr,`)
		for _, f := range files {
			if strings.HasSuffix(f, "_test.go") {
				continue
			}
			src, err := os.ReadFile(f)
			if err != nil {
				t.Fatal(err)
			}
			for i, line := range strings.Split(string(src), "\n") {
				code := line
				if j := strings.Index(code, "//"); j >= 0 {
					code = code[:j]
				}
				if makes.MatchString(code) && raw.MatchString(code) {
					t.Errorf("%s:%d makes a text for a person out of an error: %s", f, i+1, strings.TrimSpace(line))
				}
			}
		}
	}
}
