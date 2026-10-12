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
	// masked (AM16): the admin's own switch for this agent — personal data in what the round sends to a
	// model is masked first. Off by default; it no longer follows docsMayLeave (masking.go). A page
	// image cannot be masked, so while this is on pages are read by an in-house model only (choice.go).
	masked bool
	// recipients (AM16): who outside the organisation this agent's documents may reach — the admin's
	// ticks for the agent that the organisation's own list also holds. Never nil; empty is nobody, also
	// while docsMayLeave is on (choice.go mayUse). Every model call of the round carries it (masking.go).
	recipients []string
	// modelAway (AM15): this job has just found no model reachable to read a person's typed words
	// (jobs.go replyToAsked) — a second question of the same model in the same job is not put.
	modelAway bool
	// formOnly (AM10): this render job fills the round's own form and makes
	// no other file.
	formOnly bool
	// instr (AM11b): the directives this round's documents are made with — read out of what the person
	// typed (asked.go), ids of the package's own list.
	instr []string
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
		if parked, err := rc.interpretCards(ctx); errors.Is(err, ErrModelUnreachable) && ctx.Err() == nil {
			// AM15: an answer typed in words that no model could be reached to read is not a
			// question asked again, and not the default — the run waits for the model
			return r.parkForModel(rc)
		} else if err != nil {
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
		// AM12: a round on the web begins with what a model understood of the person's words
		if rc.pkg.Manifest.UsesWeb() {
			brief, err := rc.webBrief(ctx)
			if err != nil {
				return rc.stepFailed("web", err)
			}
			if brief.stop != "" {
				return r.fail(ctx, run, brief.stop, errors.New("the round on the web could not start: "+brief.stop))
			}
			extras.Web = brief
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
		// AM12: nothing is left to ask — the search, the pages, their reading (every part a step: a round
		// that comes back here after a question reads them back and does nothing twice)
		if extras.Web != nil {
			found, err := rc.research(ctx, extras.Web, pre)
			if err != nil {
				return rc.stepFailed("web", err)
			}
			if found.stop != "" {
				return r.fail(ctx, run, found.stop, errors.New("the round could not go on: "+found.stop))
			}
			extras.Web = found
		}
		computedRef, computeKey, summary, err = rc.compute(ctx, parsed, pre, extras)
		if err != nil {
			return rc.stepFailed("compute", err)
		}
		post, postKey, err := rc.postcompute(ctx, parsed, pre, computedRef, computeKey)
		if err != nil {
			return rc.stepFailed("postcompute", err)
		}
		// AM10: which files, asked here — about to produce them — only when the
		// install does not already say (outputset.go)
		if f := rc.outputSetFinding(ctx); f != nil {
			post.Findings = append(post.Findings, *f)
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
	// AM11b: what the person typed with the files, read into what the package says can be asked of its
	// documents. It changes how they are made, never a figure: the table is computed already.
	asked, err := rc.asked(ctx, rc.run.Message, rc.pkg.Manifest.FromRequest())
	if err != nil {
		return rc.stepFailed("instructions", err)
	}
	rc.instr = asked.Apply
	narrative, narrKey, err := rc.narrative(ctx, computedRef, computeKey)
	if err != nil {
		return rc.stepFailed("narrative", err)
	}
	formats := rc.formats()
	read := rc.answers // what the files are made with (reloadAnswers replaces the map, it never edits it)
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
	if err := rc.recordFormOutcome(ctx, rc.run.ID, rendered.form, arts); err != nil {
		return rc.stepFailed("publish", err)
	}
	// AM5: the package's own verdict on the round (a reconciliation that does
	// not tie). Not passed: the files go out as a draft and what a person must
	// decide is recorded. AM6: the round delivered its work — it ends
	// completed and open, not failed (endOpen); only a gate that did not pass
	// with nothing delivered is a failure.
	gate, err := rc.completionGate(ctx, computedRef, computeKey)
	if err != nil {
		return r.fail(ctx, run, "gate_not_passed", err)
	}
	if gate != nil && !gate.Passed {
		if err := rc.recordExceptions(ctx, computedRef); err != nil {
			return r.fail(ctx, run, "exceptions", err)
		}
		if err := rc.postOutputs(ctx, rc.run.ID, arts, summary, narrative, false, gate); err != nil {
			return r.fail(ctx, run, "outputs", err)
		}
		rc.sayAsked(ctx, rc.run, asked, rendered.Asked)
		if len(arts) > 0 {
			return r.endOpen(ctx, run, *gate)
		}
		return r.fail(ctx, run, gate.Reason(), &gateErr{gate: *gate})
	}
	// The documents this run recorded join the install's duplicate index.
	if err := rc.recordIndex(ctx, computedRef); err != nil {
		return r.fail(ctx, run, "index", err)
	}
	// What the package left for a person to decide (the expense audit).
	if err := rc.recordExceptions(ctx, computedRef); err != nil {
		return r.fail(ctx, run, "exceptions", err)
	}
	if err := rc.postOutputs(ctx, rc.run.ID, arts, summary, narrative, false, gate); err != nil {
		return r.fail(ctx, run, "outputs", err)
	}
	rc.sayAsked(ctx, rc.run, asked, rendered.Asked)
	// An upgrade's self-test switches the install before its run reads as
	// succeeded: whoever sees "succeeded" finds the install already switched.
	rc.finishUpgrade(context.Background(), computedRef, true, am.NoText)
	if err := r.finish(rc, am.RunSucceeded, ""); err != nil {
		return err
	}
	// AM10: a form whose mapping became ready while these files were being made (after the render
	// step looked for it) is filled now, from the same figures
	// AM11: what follows a finished run is still the run's — its form, its late answers, the next run
	// it was asked to start with the files kept for it — and is in the run's language
	after := am.InRunLang(context.Background(), rc.run)
	r.fillFormLeftBehind(after, rc.run.ID)
	// AM10: so is an answer given on a card that asks without waiting, when it landed after the
	// render step had read the answers
	r.answersLeftBehind(after, rc.run.ID, read, formats)
	rc.startNextRound(after)
	return nil
}

// exec runs one step op with the run's own engine.
func (rc *runCtx) exec(ctx context.Context, request any, inputs []Input) (*StepResult, error) {
	// AM11: an engine step is told the run's language ("lang"); a Thai run's request is as it always was
	if m, ok := request.(map[string]any); ok && am.LangOf(ctx) == am.English {
		if _, said := m["lang"]; !said {
			m["lang"] = string(am.English)
		}
	}
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
	// AM6: the install's settings — the ones its agent declares included — over
	// the defaults of the manifest of the version the run is pinned to
	engineDefaults, remembered := am.EngineDefaults(pkg.Manifest, defs)
	rc := &runCtx{ctx: ctx, r: r, run: run, install: in, engine: eng, pkg: pkg, defaults: engineDefaults, remembered: remembered, log: log}
	rc.docsMayLeave, rc.masked, rc.recipients = r.switchesOf(ctx, in.OrgID, in.Slug, pkg.Manifest)
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

// answeredBy is who answered each card of the run, by the answer's key, for
// a package whose manifest asks for it (nil otherwise): the name a document
// prints, read from the card — never from what the answer says. An answer
// nobody gave (a default, a remembered value) has no name. AM6: an answer
// given with an action on the round's files (a receipt confirmed in this
// system) carries no card; it names the signed-in person who gave it.
func (rc *runCtx) answeredBy(ctx context.Context) map[string]map[string]string {
	if rc.pkg == nil || !rc.pkg.Manifest.RecordWhoAnswered {
		return nil
	}
	out := map[string]map[string]string{}
	by, err := rc.r.Store.CardAnswerers(ctx, rc.run.ID)
	if err != nil {
		return out
	}
	names := map[uuid.UUID]string{}
	for k, a := range rc.answers {
		var user uuid.UUID
		switch {
		case a.Via == "action" && a.By != nil:
			user = *a.By
		case a.Via == "card" || a.Via == "text":
			card, err := uuid.Parse(a.CardID)
			if err != nil {
				continue
			}
			u, ok := by[card]
			if !ok {
				continue
			}
			user = u
		default:
			continue
		}
		name, seen := names[user]
		if !seen {
			name = rc.r.Store.DisplayName(ctx, rc.install.OrgID, user, "")
			names[user] = name
		}
		out[k] = map[string]string{"name": name, "via": a.Via}
	}
	return out
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
			_ = rc.r.Store.FinishStep(ctx, st.ID, "failed", stampSentOut(nil, sentOutStamped(st.Output)), "budget_paused: "+err.Error(), "", "", 0)
			return nil, err
		}
	}
	// AM16 (masking.go): every model call a step makes carries what the admin's two switches say, and
	// what happened to each is written beside the step's output. What left the organisation as typed is
	// written beside a FAILED step too — the page left, whatever became of the step — and what an
	// earlier attempt of the same step sent is kept when the step is run again.
	sentBefore := sentOutStamped(st.Output)
	stepCtx, calls := withModelCalls(withRecipients(ctx, rc.recipients), !rc.docsMayLeave, rc.masked)
	out, credits, script, model, err := fn(stepCtx)
	if err != nil {
		msg := err.Error()
		if ctx.Err() != nil {
			msg = "stopped: " + msg
		}
		_ = rc.r.Store.FinishStep(context.Background(), st.ID, "failed", stampSentOut(nil, sentBefore || calls.wentOut()), trim(msg, 1500), script, model, credits)
		rc.r.syncProgress(context.Background(), rc.run.ID)
		return nil, err
	}
	b, _ := json.Marshal(out)
	b = stampSentOut(stampMasking(b, calls.masking()), sentBefore || calls.wentOut())
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
	// Web (AM12): what the package settled of a request, for the search that follows (web.go).
	Web json.RawMessage `json:"web"`
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
	// AM5: who answered each card, for a package that records it
	who := rc.answeredBy(ctx)
	if who != nil {
		parts = append(parts, map[string]any{"answered_by": who})
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
			if who != nil {
				req["answered_by"] = who
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
			if x.Web != nil {
				// AM12: the pictures cut from the stored pages are kept with the run, and the pages that
				// did not become candidates are marked for deletion
				if err := rc.keepEvidence(ctx, res, data); err != nil {
					return nil, 0, "", "", err
				}
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
//
// AM5: what the person reads is built from the reason's code and facts about
// the round (am.FailureFor) — never from the error. The error goes to the log
// and to the run's steps, which the run page and a problem report show.
//
// AM6: a round that fails delivered nothing, and is charged nothing: ending
// it voids what its steps were charged (Store.Finish), and the round says so.
func (r *Runner) fail(ctx context.Context, run am.Run, reason string, err error) error {
	if ctx != nil && ctx.Err() != nil {
		return ctx.Err()
	}
	// a gate that did not pass with its draft delivered is not a failure
	var ge *gateErr
	if errors.As(err, &ge) && ge.draft {
		return r.endOpen(ctx, run, ge.gate)
	}
	bg := context.Background()
	_ = r.Store.Finish(bg, run.ID, r.ID, am.RunFailed, reason)
	r.recordFailure(bg, run, reason, err)
	f := r.failure(bg, run, reason, err)
	_ = r.Store.PostAgentText(bg, run, am.AgentText{Text: f.Text(), Tone: "error", Failure: &f})
	if run.Kind == am.KindSelfTest {
		// an upgrade's self-test that fails leaves the install on its version
		(&runCtx{r: r, run: run}).finishUpgrade(bg, blobRef{}, false, f.What)
	}
	r.syncProgress(bg, run.ID)
	return err
}

// endOpen ends a round whose package's gate did not pass and whose draft was
// delivered (AM6). The round did its work: it read the files, matched what
// matched, made the draft and said how much is unexplained. It is charged
// like any round that delivers, it is not an error in the thread, and its
// progress card ends finished. What stays as it was: the run's status in the
// database (failed, with the gate's code), so nobody signs a draft and
// nothing of it enters the duplicate index.
func (r *Runner) endOpen(ctx context.Context, run am.Run, gate gateView) error {
	if ctx != nil && ctx.Err() != nil {
		return ctx.Err()
	}
	bg := context.Background()
	_ = r.Store.FinishOpen(bg, run.ID, r.ID, gate.Reason())
	// the verdict stays on the run's steps, for the run page
	r.keepOnRun(bg, run, gate.Reason(), (&gateErr{gate: gate, draft: true}).Error())
	r.logger().Info("run finished with its gate open", "run", run.ID, "gate", gate.Reason())
	facts := am.CompletionFacts{KeepDays: int(orDefault(r.ScratchTTL, 7*24*time.Hour).Hours() / 24),
		GateWhat: gate.What, GateNext: gate.Next}
	c := am.CompletionFor(gate.Reason(), facts)
	_ = r.Store.PostAgentText(bg, run, am.AgentText{Text: c.Text(), Tone: "open", Completion: &c})
	if run.Kind == am.KindSelfTest {
		// an upgrade's self-test whose gate does not pass leaves the install on its version
		(&runCtx{r: r, run: run}).finishUpgrade(bg, blobRef{}, false, c.What)
	}
	r.syncProgress(bg, run.ID)
	return nil
}

// failure is the failed round as a person reads it: the reason's own
// sentences and whether its files can be tried again. (What it was charged is
// the admin's to read, on the Spend page — AM11b.)
func (r *Runner) failure(ctx context.Context, run am.Run, reason string, err error) am.Failure {
	facts := am.FailureFacts{Kind: run.Kind, KeepDays: int(orDefault(r.ScratchTTL, 7*24*time.Hour).Hours() / 24), Lang: run.Lang()}
	if run.Kind == am.KindRun {
		if files, e := r.Store.RunFiles(ctx, run.ID, "active"); e == nil && len(files) > 0 {
			facts.HasFiles = true
		}
	}
	var qe *qualityErr
	if errors.As(err, &qe) {
		facts.FailedChecks = qe.failed
	}
	var ge *gateErr
	if errors.As(err, &ge) {
		facts.GateWhat, facts.GateNext = ge.gate.What, ge.gate.Next
	}
	return am.FailureFor(reason, facts)
}

// recordFailure keeps the error itself for the run page and the problem
// report when no step of the run holds it (the run stopped between steps).
func (r *Runner) recordFailure(ctx context.Context, run am.Run, reason string, err error) {
	if err == nil {
		return
	}
	r.logger().Warn("run failed", "run", run.ID, "reason", reason, "err", err)
	r.keepOnRun(ctx, run, reason, reason+": "+err.Error())
}

// keepOnRun writes why a run ended onto its steps, unless a step of the run
// says so already.
func (r *Runner) keepOnRun(ctx context.Context, run am.Run, reason, why string) {
	steps, e := r.Store.ListSteps(ctx, run.ID)
	if e != nil {
		return
	}
	for _, s := range steps {
		if s.Status == "failed" {
			return // the step that failed already says why
		}
	}
	st, done, e := r.Store.BeginStep(ctx, run.ID, "stopped", "check", am.StepKey(run.ID, "stopped", reason), json.RawMessage(`{}`))
	if e != nil || done {
		return
	}
	_ = r.Store.FinishStep(ctx, st.ID, "failed", nil, am.TrimText(why, 1500), "", "", 0)
}

func (r *Runner) logger() *slog.Logger {
	if r.Log != nil {
		return r.Log
	}
	return slog.Default()
}

func (rc *runCtx) stepFailed(step string, err error) error {
	// A budget that ran out is a wait, never a failure (AM3).
	var bp *errBudgetPark
	if errors.As(err, &bp) {
		return rc.r.parkForBudget(rc, bp)
	}
	// So is a model that cannot be reached (AM15): the run waits for it.
	if errors.Is(err, ErrModelUnreachable) && rc.ctx.Err() == nil {
		return rc.r.parkForModel(rc)
	}
	if rc.runCtxCancelled(err) {
		return err
	}
	return rc.r.fail(rc.ctx, rc.run, step+"_failed", err)
}

func (rc *runCtx) runCtxCancelled(err error) bool {
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) && strings.Contains(err.Error(), "context")
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

// formats is the run's output set (install default unless the run says
// otherwise; AM10: an answered "which files" wins, and becomes the run's).
func (rc *runCtx) formats() []string {
	if fs := am.OutputSetOf(rc.answers); fs != nil {
		_ = rc.r.Store.SetFormats(rc.ctx, rc.r.Store.Pool, rc.run.ID, fs)
		return fs
	}
	var f []string
	_ = json.Unmarshal(rc.run.Formats, &f)
	if len(f) == 0 {
		f = rc.pkg.Manifest.DefaultFormats()
	}
	return f
}

// outputSetFinding is the "which files" question for this round, or nil
// (one shape only, the install says, a self-test, or already answered).
func (rc *runCtx) outputSetFinding(ctx context.Context) *am.Finding {
	if rc.run.Kind != am.KindRun {
		return nil
	}
	if _, done := rc.answers[am.OutputSetCode]; done {
		return nil
	}
	defs, err := rc.r.Store.ListDefaults(ctx, rc.install.ID)
	if err != nil {
		return nil
	}
	return am.OutputSetFinding(am.LangOf(ctx), rc.pkg.Manifest, defs, rc.formats())
}

func tempDir() string { return os.TempDir() }
