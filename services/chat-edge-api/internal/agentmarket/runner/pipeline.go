package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// errParked ends a pass without being a failure.
var errParked = errors.New("parked for input")

// runCtx is one pass over a run: everything the steps need.
type runCtx struct {
	ctx        context.Context // the run's context: stopped on shutdown, lost lease or cancel
	r          *Runner
	run        am.Run
	install    am.Install
	engine     *Engine // the engine this run is pinned to (AM2)
	pkg        *Package
	defaults   map[string]any
	remembered map[string]json.RawMessage
	answers    map[string]am.Answer
	log        *slog.Logger
	// docsMayLeave (AM4): the organisation lets this round's data go to a
	// model outside it. Off by default: only in-house models see anything.
	docsMayLeave bool
}

// blobRef is a large step output kept in object storage.
type blobRef struct {
	Key    string `json:"key"`
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
}

// ── the pipeline ─────────────────────────────────────────────────────────
//
// parse (per file) → preflight → clarify → compute → postcompute → clarify
// → narrative → render (+ quality gate) → publish.
//
// Every step has an idempotency key made of everything that decides its
// output. A resumed run walks the same list: steps whose key is already
// done are read back, never re-run; only a step whose inputs changed (a new
// file, a new answer) runs again — as a new step with a new key.

func (r *Runner) pipeline(ctx context.Context, run am.Run, log *slog.Logger) error {
	rc, err := r.load(ctx, run, log)
	if errors.Is(err, errEngineMissing) {
		return r.fail(ctx, run, "engine_missing", err)
	}
	if err != nil {
		return r.fail(ctx, run, "load", err)
	}
	if rc.pkg.Checksum != run.Checksum {
		return r.fail(ctx, run, "package_changed", fmt.Errorf("agent package changed since this run started (%s → %s); start a new round",
			short(run.Checksum), short(rc.pkg.Checksum)))
	}
	if !r.Sandbox() {
		return r.fail(ctx, run, "sandbox_unavailable", errors.New("the compute sandbox is not available"))
	}
	if run.Kind == am.KindSelfTest {
		if err := rc.seedSelfTest(ctx); err != nil {
			return r.fail(ctx, run, "self_test_seed", err)
		}
	}
	var computedRef blobRef
	var computeKey, period string
	var summary map[string]any
	for pass := 0; ; pass++ {
		if pass > 12 {
			return r.fail(ctx, run, "too_many_rounds", errors.New("the run did not settle after 12 rounds"))
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		if parked, err := rc.interpretCards(ctx); err != nil {
			return r.fail(ctx, run, "interpret", err)
		} else if parked {
			return r.park(rc, "reask")
		}
		if err := rc.reloadAnswers(ctx); err != nil {
			return r.fail(ctx, run, "load", err)
		}
		if done, err := rc.consumeReplacements(ctx); err != nil {
			return r.fail(ctx, run, "files", err)
		} else if done {
			continue
		}
		files, err := r.Store.RunFiles(ctx, run.ID, "active")
		if err != nil {
			return r.fail(ctx, run, "files", err)
		}
		var parsed []parsedFile
		for _, f := range files {
			p, err := rc.parse(ctx, f)
			if err != nil {
				return rc.stepFailed("parse", err)
			}
			parsed = append(parsed, p)
		}
		// AM2: the install's duplicate index and a vision reading of each scan
		// or photo, for packages that use them (empty for the others).
		extras, err := rc.extras(ctx, parsed)
		if err != nil {
			return rc.stepFailed("vision", err)
		}
		pre, preKey, err := rc.preflight(ctx, parsed, extras)
		if err != nil {
			return rc.stepFailed("preflight", err)
		}
		if act, err := rc.clarify(ctx, "preflight", pre.Findings, pre.Assumptions, preKey, pre); err != nil {
			return r.fail(ctx, run, "clarify", err)
		} else if act == actPark {
			return r.park(rc, "waiting_preflight")
		} else if act == actCancel {
			return r.finish(rc, am.RunCancelled, "cancelled_by_answer")
		} else if act == actAgain {
			continue
		}
		if pre.Period == nil {
			return r.fail(ctx, run, "no_period", errors.New("the period could not be determined"))
		}
		period = *pre.Period
		_ = r.Store.SetRunFields(ctx, run.ID, &period, nil, nil)
		computedRef, computeKey, summary, err = rc.compute(ctx, parsed, pre, extras)
		if err != nil {
			return rc.stepFailed("compute", err)
		}
		post, postKey, err := rc.postcompute(ctx, parsed, pre, computedRef, computeKey)
		if err != nil {
			return rc.stepFailed("postcompute", err)
		}
		act, err := rc.clarify(ctx, "postcompute", post.Findings, nil, postKey, pre)
		if err != nil {
			return r.fail(ctx, run, "clarify", err)
		}
		if act == actPark {
			return r.park(rc, "waiting_postcompute")
		}
		if act == actCancel {
			return r.finish(rc, am.RunCancelled, "cancelled_by_answer")
		}
		if act == actAgain {
			continue // new answers change the compute key: recompute with them
		}
		break
	}
	narrative, narrKey, err := rc.narrative(ctx, computedRef, computeKey)
	if err != nil {
		return rc.stepFailed("narrative", err)
	}
	formats := rc.formats()
	rendered, renderKey, err := rc.render(ctx, rc.run.ID, computedRef, computeKey, narrative, narrKey, formats, "render")
	if err != nil {
		var qe *qualityErr
		if errors.As(err, &qe) {
			return r.fail(ctx, run, "quality_gate", err)
		}
		return rc.stepFailed("render", err)
	}
	arts, err := rc.publish(ctx, rc.run.ID, rendered, renderKey, computedRef)
	if err != nil {
		return rc.stepFailed("publish", err)
	}
	// The documents this run recorded join the install's duplicate index.
	if err := rc.recordIndex(ctx, computedRef); err != nil {
		return r.fail(ctx, run, "index", err)
	}
	// What the package left for a person to decide (the expense audit).
	if err := rc.recordExceptions(ctx, computedRef); err != nil {
		return r.fail(ctx, run, "exceptions", err)
	}
	if err := rc.postOutputs(ctx, rc.run.ID, arts, summary, narrative, false); err != nil {
		return r.fail(ctx, run, "outputs", err)
	}
	// An upgrade's self-test switches the install before its run reads as
	// succeeded: whoever sees "succeeded" finds the install already switched.
	rc.finishUpgrade(context.Background(), computedRef, true, "")
	if err := r.finish(rc, am.RunSucceeded, ""); err != nil {
		return err
	}
	rc.startNextRound(context.Background())
	return nil
}

// exec runs one step op with the run's own engine.
func (rc *runCtx) exec(ctx context.Context, request any, inputs []Input) (*StepResult, error) {
	return rc.r.Exec.RunEngine(ctx, rc.engine, request, inputs)
}

func (r *Runner) load(ctx context.Context, run am.Run, log *slog.Logger) (*runCtx, error) {
	in, err := r.Store.InstallByID(ctx, run.InstallID)
	if err != nil {
		return nil, err
	}
	if run.EngineChecksum == "" {
		// a job created without one (a reply, a preview) runs with its install's pinned version
		if def, err := r.Store.DefinitionByID(ctx, in.DefinitionID); err == nil {
			run.EngineChecksum = def.EngineChecksum
		}
	}
	eng, err := r.engineFor(ctx, run, in.Slug)
	if err != nil {
		return nil, err
	}
	pkg := eng.Packages[in.Slug]
	if pkg == nil {
		return nil, fmt.Errorf("%w: package %s is not in engine %s", errEngineMissing, in.Slug, short(eng.Checksum))
	}
	defs, err := r.Store.ListDefaults(ctx, in.ID)
	if err != nil {
		return nil, err
	}
	engineDefaults, remembered := am.DefaultsForEngine(defs)
	rc := &runCtx{ctx: ctx, r: r, run: run, install: in, engine: eng, pkg: pkg, defaults: engineDefaults, remembered: remembered, log: log,
		docsMayLeave: r.docsMayLeave(ctx, in.OrgID)}
	rc.selfTestSettings()
	return rc, rc.reloadAnswers(ctx)
}

func (rc *runCtx) reloadAnswers(ctx context.Context) error {
	run, err := rc.r.Store.RunByID(ctx, rc.run.ID)
	if err != nil {
		return err
	}
	rc.run = run
	rc.answers = map[string]am.Answer{}
	if len(run.Answers) > 0 {
		if err := json.Unmarshal(run.Answers, &rc.answers); err != nil {
			return err
		}
	}
	return nil
}

// answerValues is what the package code reads: finding key → value.
// Cosmetic answers stay out of anything that decides a number.
func (rc *runCtx) answerValues(moments ...string) map[string]json.RawMessage {
	out := map[string]json.RawMessage{}
	for k, a := range rc.answers {
		if a.Cosmetic {
			continue
		}
		if len(moments) > 0 && !contains(moments, a.Moment) {
			continue
		}
		out[k] = a.Value
	}
	return out
}

func (rc *runCtx) presentation() map[string]json.RawMessage {
	out := map[string]json.RawMessage{}
	for _, a := range rc.answers {
		if a.Cosmetic && a.Code != "" {
			out[a.Code] = a.Value
		}
	}
	return out
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

func short(s string) string {
	if len(s) > 12 {
		return s[:12]
	}
	return s
}

// ── step plumbing ────────────────────────────────────────────────────────

// step runs fn once per idempotency key. A key already done returns its
// stored output without running anything.
func (rc *runCtx) step(ctx context.Context, name, kind, key string, input any, costly bool,
	fn func(ctx context.Context) (out any, credits float64, script, model string, err error)) (json.RawMessage, error) {
	in, _ := json.Marshal(input)
	st, done, err := rc.r.Store.BeginStep(ctx, rc.run.ID, name, kind, key, in)
	if err != nil {
		return nil, err
	}
	if done {
		return st.Output, nil
	}
	rc.r.syncProgress(ctx, rc.run.ID)
	if costly {
		// AM3: the person's, their department's and the install's month.
		if err := rc.budgetCheck(ctx); err != nil {
			_ = rc.r.Store.FinishStep(ctx, st.ID, "failed", nil, "budget_paused: "+err.Error(), "", "", 0)
			return nil, err
		}
	}
	out, credits, script, model, err := fn(ctx)
	if err != nil {
		msg := err.Error()
		if ctx.Err() != nil {
			msg = "stopped: " + msg
		}
		_ = rc.r.Store.FinishStep(context.Background(), st.ID, "failed", nil, trim(msg, 1500), script, model, credits)
		rc.r.syncProgress(context.Background(), rc.run.ID)
		return nil, err
	}
	b, _ := json.Marshal(out)
	if err := rc.r.Store.FinishStep(ctx, st.ID, "succeeded", b, "", script, model, credits); err != nil {
		return nil, err
	}
	if credits > 0 {
		_ = rc.r.Store.Charge(ctx, rc.install.ID, rc.run.ID, key, credits, model, map[string]any{"step": name})
	}
	rc.r.syncProgress(ctx, rc.run.ID)
	return b, nil
}

func (rc *runCtx) sandboxCredits(d time.Duration) float64 {
	c := d.Seconds() * rc.r.SandboxCreditsPerSecond
	if c < 1 {
		c = 1
	}
	return c
}

func (rc *runCtx) putBlob(ctx context.Context, stepKey, name string, data []byte) (blobRef, error) {
	key := am.BlobKey(rc.run.TenantID, rc.run.ID, stepKey, name)
	if err := rc.r.Files.Upload(ctx, key, strings.NewReader(string(data)), "application/json", int64(len(data))); err != nil {
		return blobRef{}, err
	}
	sum := sha256.Sum256(data)
	return blobRef{Key: key, SHA256: hex.EncodeToString(sum[:]), Size: int64(len(data))}, nil
}

// localBlob downloads a blob to a temp file for a sandbox input.
func (rc *runCtx) localBlob(ctx context.Context, key string) (string, func(), error) {
	return rc.r.Files.OpenLocalCopy(ctx, key)
}

// ── parse ────────────────────────────────────────────────────────────────

type parsedFile struct {
	File    am.File
	Ref     blobRef        `json:"ref"`
	Summary map[string]any `json:"summary"`
	Key     string         `json:"key"`
	// Vision: page images of a scan or photo for the vision step (AM2).
	Vision []blobRef `json:"vision"`
}

func (rc *runCtx) mapping() map[string]any {
	if m, ok := rc.defaults["mapping"].(map[string]any); ok {
		return m
	}
	return map[string]any{}
}

func (rc *runCtx) parse(ctx context.Context, f am.File) (parsedFile, error) {
	mapping := rc.mapping()
	key := am.StepKey(rc.run.ID, "parse", f.SHA256, f.Name, mapping)
	out, err := rc.step(ctx, "parse:"+f.Name, "ingest", key, map[string]any{"file": f.Name, "sha256": f.SHA256, "size": f.Size}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			path, cleanup, err := rc.r.Files.OpenLocalCopy(ctx, f.StorageKey)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			input := "in_" + f.ID.String()[:8] + filepath.Ext(f.Name)
			res, err := rc.exec(ctx, map[string]any{"op": "parse", "package": rc.pkg.PyPackage,
				"file": map[string]any{"name": f.Name, "input": input}, "mapping": mapping},
				[]Input{{Name: input, LocalPath: path}})
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			data, err := readArtifact(res, "parsed.json")
			if err != nil {
				return nil, 0, "", "", err
			}
			ref, err := rc.putBlob(ctx, key, "parsed.json", data)
			if err != nil {
				return nil, 0, "", "", err
			}
			var summary map[string]any
			_ = res.Decode("summary", &summary)
			vision, err := rc.visionPages(ctx, res, key, summary)
			if err != nil {
				return nil, 0, "", "", err
			}
			return map[string]any{"ref": ref, "summary": summary, "vision": vision}, rc.sandboxCredits(res.Elapsed),
				"engine/" + rc.pkg.PyPackage + ".parse", "", nil
		})
	if err != nil {
		return parsedFile{}, err
	}
	var p parsedFile
	if err := json.Unmarshal(out, &p); err != nil {
		return p, err
	}
	p.File, p.Key = f, key
	return p, nil
}

func (rc *runCtx) parsedInputs(ctx context.Context, parsed []parsedFile) ([]Input, []string, func(), error) {
	var inputs []Input
	var names []string
	var cleanups []func()
	done := func() {
		for _, c := range cleanups {
			c()
		}
	}
	for i, p := range parsed {
		path, cleanup, err := rc.localBlob(ctx, p.Ref.Key)
		if err != nil {
			done()
			return nil, nil, func() {}, err
		}
		cleanups = append(cleanups, cleanup)
		name := fmt.Sprintf("parsed_%02d.json", i)
		inputs = append(inputs, Input{Name: name, LocalPath: path})
		names = append(names, name)
	}
	return inputs, names, done, nil
}

func parsedKeys(parsed []parsedFile) []string {
	var keys []string
	for _, p := range parsed {
		keys = append(keys, p.Key)
	}
	sort.Strings(keys)
	return keys
}

// ── preflight ────────────────────────────────────────────────────────────

type preflightOut struct {
	Roles       map[string]string `json:"roles"`
	Period      *string           `json:"period"`
	PeriodFrom  *string           `json:"period_source"`
	Findings    []am.Finding      `json:"findings"`
	Assumptions []am.Assumption   `json:"assumptions"`
	Stats       map[string]any    `json:"stats"`
	Files       []map[string]any  `json:"files"`
	// Today: the date the package judged with (AM2), handed to compute so
	// both judge a document the same way.
	Today string `json:"today"`
}

// thaiTime is the person's clock (Thailand keeps UTC+7 all year, so no tz
// database is needed in the image). The runner itself runs on UTC.
var thaiTime = time.FixedZone("Asia/Bangkok", 7*60*60)

// today is the run's date as the person sees it: the period guess and the
// "prepared on" line of the files use it.
func (rc *runCtx) today() string { return rc.r.Now().In(thaiTime).Format("2006-01-02") }

func (rc *runCtx) preflight(ctx context.Context, parsed []parsedFile, x runExtras) (preflightOut, string, error) {
	answers := rc.answerValues("preflight", "run")
	key := am.StepKey(rc.run.ID, "preflight", append([]any{parsedKeys(parsed), answers, rc.run.Message, rc.defaults, rc.today()[:7]},
		x.keyParts()...)...)
	out, err := rc.step(ctx, "preflight", "check", key, map[string]any{"files": len(parsed), "answers": answers}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			inputs, names, cleanup, err := rc.parsedInputs(ctx, parsed)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			req := map[string]any{"op": "preflight", "package": rc.pkg.PyPackage, "parsed": names,
				"message": rc.run.Message, "defaults": rc.defaults, "answers": answers, "today": rc.today()}
			res, err := rc.exec(ctx, req, x.attach(req, inputs))
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			return res.Result, rc.sandboxCredits(res.Elapsed), "checks/preflight.py", "", nil
		})
	if err != nil {
		return preflightOut{}, key, err
	}
	var p preflightOut
	return p, key, json.Unmarshal(out, &p)
}

// ── compute / postcompute ────────────────────────────────────────────────

func (rc *runCtx) compute(ctx context.Context, parsed []parsedFile, pre preflightOut, x runExtras) (blobRef, string, map[string]any, error) {
	answers := rc.answerValues()
	parts := []any{parsedKeys(parsed), *pre.Period, pre.Roles, answers, rc.defaults}
	if pre.Today != "" {
		parts = append(parts, pre.Today)
	}
	key := am.StepKey(rc.run.ID, "compute", append(parts, x.keyParts()...)...)
	out, err := rc.step(ctx, "compute", "compute", key, map[string]any{"period": *pre.Period, "roles": pre.Roles, "answers": answers}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			inputs, names, cleanup, err := rc.parsedInputs(ctx, parsed)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			req := map[string]any{"op": "compute", "package": rc.pkg.PyPackage, "parsed": names,
				"roles": pre.Roles, "period": *pre.Period, "answers": answers, "defaults": rc.defaults}
			if pre.Today != "" {
				req["today"] = pre.Today
			}
			res, err := rc.exec(ctx, req, x.attach(req, inputs))
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			data, err := readArtifact(res, "computed.json")
			if err != nil {
				return nil, 0, "", "", err
			}
			ref, err := rc.putBlob(ctx, key, "computed.json", data)
			if err != nil {
				return nil, 0, "", "", err
			}
			var summary map[string]any
			_ = res.Decode("summary", &summary)
			return map[string]any{"computed": ref, "computed_key": ref.Key, "summary": summary},
				rc.sandboxCredits(res.Elapsed), "scripts/metrics.py", "", nil
		})
	if err != nil {
		return blobRef{}, key, nil, err
	}
	var o struct {
		Computed blobRef        `json:"computed"`
		Summary  map[string]any `json:"summary"`
	}
	err = json.Unmarshal(out, &o)
	return o.Computed, key, o.Summary, err
}

type postOut struct {
	Findings []am.Finding `json:"findings"`
}

func (rc *runCtx) postcompute(ctx context.Context, parsed []parsedFile, pre preflightOut, computed blobRef, computeKey string) (postOut, string, error) {
	answers := rc.answerValues()
	key := am.StepKey(rc.run.ID, "postcompute", computeKey, answers)
	out, err := rc.step(ctx, "postcompute", "check", key, map[string]any{"answers": answers}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			inputs, names, cleanup, err := rc.parsedInputs(ctx, parsed)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			cpath, ccleanup, err := rc.localBlob(ctx, computed.Key)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer ccleanup()
			inputs = append(inputs, Input{Name: "computed.json", LocalPath: cpath})
			res, err := rc.exec(ctx, map[string]any{"op": "postcompute", "package": rc.pkg.PyPackage, "parsed": names,
				"computed": "computed.json", "roles": pre.Roles, "period": *pre.Period, "answers": answers,
				"defaults": rc.defaults}, inputs)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			return res.Result, rc.sandboxCredits(res.Elapsed), "checks/postcompute.py", "", nil
		})
	if err != nil {
		return postOut{}, key, err
	}
	var p postOut
	return p, key, json.Unmarshal(out, &p)
}

// ── outcomes ─────────────────────────────────────────────────────────────

func (r *Runner) park(rc *runCtx, reason string) error {
	if err := r.Store.Park(context.Background(), rc.run.ID, r.ID, reason); err != nil {
		return err
	}
	r.syncProgress(context.Background(), rc.run.ID)
	rc.log.Info("run parked", "reason", reason)
	return nil
}

func (r *Runner) finish(rc *runCtx, status, reason string) error {
	if err := r.Store.Finish(context.Background(), rc.run.ID, r.ID, status, reason); err != nil {
		return err
	}
	r.syncProgress(context.Background(), rc.run.ID)
	rc.log.Info("run finished", "status", status, "reason", reason)
	return nil
}

// fail ends a run as failed — unless the run's own context was stopped (a
// runner shutdown or a lost lease): then the run is left exactly as it is,
// its lease expires, and the next runner resumes it from the first
// unfinished step. A graceful stop must never turn into a failed run.
func (r *Runner) fail(ctx context.Context, run am.Run, reason string, err error) error {
	if ctx != nil && ctx.Err() != nil {
		return ctx.Err()
	}
	bg := context.Background()
	_ = r.Store.Finish(bg, run.ID, r.ID, am.RunFailed, reason)
	_ = r.Store.PostText(bg, run, failureText(reason, err), map[string]any{"tone": "error"})
	if run.Kind == am.KindSelfTest {
		// an upgrade's self-test that fails leaves the install on its version
		(&runCtx{r: r, run: run}).finishUpgrade(bg, blobRef{}, false, failureText(reason, err))
	}
	r.syncProgress(bg, run.ID)
	return err
}

func (rc *runCtx) stepFailed(step string, err error) error {
	// A budget that ran out is a wait, never a failure (AM3).
	var bp *errBudgetPark
	if errors.As(err, &bp) {
		return rc.r.parkForBudget(rc, bp)
	}
	if rc.runCtxCancelled(err) {
		return err
	}
	return rc.r.fail(rc.ctx, rc.run, step+"_failed", err)
}

func (rc *runCtx) runCtxCancelled(err error) bool {
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) && strings.Contains(err.Error(), "context")
}

// failureText is what the person reads when a run stops (AM3): what
// happened, what to do next, and that their data is safe — in words, not a
// stack trace. The technical detail stays in the run's steps for whoever
// the "แจ้งปัญหา" button reaches.
func failureText(reason string, err error) string {
	const safe = " ไฟล์ที่ส่งมาและผลงานของรอบก่อน ๆ ยังอยู่ครบ"
	const report = " ถ้าไม่แน่ใจว่าเกิดจากอะไร กด “แจ้งปัญหา” ผู้ดูแลจะเห็นรอบนี้พร้อมขั้นตอนที่หยุด"
	switch reason {
	case "budget_exhausted":
		return "หยุดรอบนี้ไว้ครับ งบเครดิตของ agent นี้สำหรับเดือนนี้หมดแล้ว — ขอเพิ่มงบจากผู้ดูแล Agent Market แล้วพิมพ์ “ทำรอบใหม่”" +
			safe + " แชทปกติยังใช้ได้ตามเดิม"
	case "quality_gate":
		return "สร้างไฟล์ได้ แต่ไม่ผ่านด่านตรวจคุณภาพ จึงยังไม่ส่งให้ครับ (" + trim(err.Error(), 300) + ") — " +
			"ลองขอไฟล์ชนิดอื่น (เช่น .docx แทน PDF) หรือพิมพ์ “ทำรอบใหม่”" + safe + report
	case "package_changed":
		return "agent ถูกอัปเดตระหว่างรอบนี้ ตัวเลขของรอบนี้จึงไม่ถูกส่ง — ส่งไฟล์เดิมอีกครั้งหรือพิมพ์ “ทำรอบใหม่”" + safe
	case "sandbox_unavailable":
		return "ส่วนคำนวณของ agent ยังไม่พร้อมใช้งานในตอนนี้ — ลองใหม่ในอีกไม่กี่นาที (พิมพ์ “ทำรอบใหม่”)" + safe + report
	case "engine_missing":
		return "รุ่นของ agent ที่ติดตั้งไว้ไม่มีในระบบแล้ว — อัปเกรดเป็นรุ่นใหม่ในหน้าตั้งค่า แล้วส่งไฟล์อีกครั้ง" + safe
	case "no_period":
		return "หางวด (เดือน) ของข้อมูลในไฟล์ไม่เจอ — ส่งไฟล์ที่มีคอลัมน์วันที่ หรือพิมพ์บอกงวด เช่น “ปิดงบเดือนกันยายน 2569”" + safe
	case "too_many_rounds":
		return "คำตอบกับไฟล์ในรอบนี้ยังขัดกันอยู่หลายครั้ง จึงหยุดไว้ก่อน — ตรวจคำตอบในการ์ดคำถามแล้วพิมพ์ “ทำรอบใหม่”" + safe + report
	}
	msg := "รอบนี้ทำไม่สำเร็จครับ"
	if err != nil {
		msg += " (" + trim(err.Error(), 240) + ")"
	}
	return msg + " — ส่งไฟล์ใหม่หรือพิมพ์ “ทำรอบใหม่”" + safe + report
}

// ── self-test seed ───────────────────────────────────────────────────────

func (rc *runCtx) seedSelfTest(ctx context.Context) error {
	files, err := rc.r.Store.RunFiles(ctx, rc.run.ID)
	if err != nil || len(files) > 0 {
		return err
	}
	var st struct {
		Files   []string `json:"files"`
		Message string   `json:"message"`
		Formats []string `json:"formats"`
	}
	if err := json.Unmarshal(rc.pkg.SelfTest, &st); err != nil {
		return fmt.Errorf("self-test.json: %w", err)
	}
	for _, rel := range st.Files {
		data, err := rc.engine.PackageFile(rc.pkg, rel)
		if err != nil {
			return err
		}
		id := uuid.New()
		name := filepath.Base(rel)
		key := am.UploadKey(rc.run.TenantID, rc.run.InstallID, id, name)
		if err := rc.r.Files.Upload(ctx, key, strings.NewReader(string(data)), "application/octet-stream", int64(len(data))); err != nil {
			return err
		}
		sum := sha256.Sum256(data)
		rid := rc.run.ID
		if _, err := rc.r.Store.CreateFile(ctx, rc.r.Store.Pool, am.File{ID: id, TenantID: rc.run.TenantID, UserID: rc.run.UserID,
			InstallID: rc.run.InstallID, RunID: &rid, Name: name, Size: int64(len(data)), SHA256: hex.EncodeToString(sum[:]),
			StorageKey: key, Status: "active"}); err != nil {
			return err
		}
	}
	if len(st.Formats) > 0 {
		if err := rc.r.Store.SetFormats(ctx, rc.r.Store.Pool, rc.run.ID, st.Formats); err != nil {
			return err
		}
	}
	if st.Message != "" && rc.run.Message != st.Message {
		if _, err := rc.r.Store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET message = $2 WHERE id = $1`, rc.run.ID, st.Message); err != nil {
			return err
		}
	}
	return rc.reloadAnswers(ctx)
}

// formats is the run's output set (install default unless the run says otherwise).
func (rc *runCtx) formats() []string {
	var f []string
	_ = json.Unmarshal(rc.run.Formats, &f)
	if len(f) == 0 {
		f = rc.pkg.Manifest.DefaultFormats()
	}
	return f
}

func tempDir() string { return os.TempDir() }
