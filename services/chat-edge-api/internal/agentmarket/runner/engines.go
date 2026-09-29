package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"sync"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// ── Pinned versions (AM2 Phase G) ────────────────────────────────────────
//
// A run executes with the engine its definition was released with, not
// with whatever engine this runner was built with: each runner stores its
// engine zip by its build hash (EngineKey) when it starts, and loads an older
// one from there for a run pinned to it. Registration never overwrites a
// released version — a package whose files changed must carry a new
// version, or it is not offered (and the old version keeps running).

type engineCache struct {
	mu   sync.Mutex
	byCk map[string]*Engine
}

// storeEngine keeps this runner's engine zip where later runners find it.
func (r *Runner) storeEngine(ctx context.Context) error {
	key := am.EngineKey(r.Engine.Build)
	if ok, err := r.Files.Exists(ctx, key); err == nil && ok {
		return nil
	}
	data, err := os.ReadFile(r.Engine.ZipPath)
	if err != nil {
		return err
	}
	return r.Files.Upload(ctx, key, bytes.NewReader(data), "application/zip", int64(len(data)))
}

// errEngineMissing: the version a run is pinned to has no engine this runner can load.
var errEngineMissing = errors.New("engine not available")

// engineFor is the engine a run executes with.
func (r *Runner) engineFor(ctx context.Context, run am.Run, slug string) (*Engine, error) {
	ck := run.EngineChecksum
	if ck == "" || ck == r.Engine.Build {
		return r.Engine, nil
	}
	r.engines.mu.Lock()
	defer r.engines.mu.Unlock()
	if r.engines.byCk == nil {
		r.engines.byCk = map[string]*Engine{}
	}
	if e := r.engines.byCk[ck]; e != nil {
		return e, nil
	}
	body, _, _, err := r.Files.Download(ctx, am.EngineKey(ck))
	if err != nil {
		// Not stored (an engine from before AM2 kept engines): the current one
		// may still hold exactly the package the run is pinned to.
		if p := r.Engine.Packages[slug]; p != nil && p.Checksum == run.Checksum {
			return r.Engine, nil
		}
		return nil, fmt.Errorf("%w: %s", errEngineMissing, short(ck))
	}
	data, err := io.ReadAll(body)
	body.Close()
	if err != nil {
		return nil, err
	}
	f, err := os.CreateTemp("", "am_engine-"+short(ck)+"-*.zip")
	if err != nil {
		return nil, err
	}
	if _, err := f.Write(data); err != nil {
		f.Close()
		return nil, err
	}
	f.Close()
	e, err := LoadEngine(f.Name(), "")
	if err != nil {
		return nil, err
	}
	if e.Build != ck && !legacyMatch(e, ck, slug, run.Checksum) {
		return nil, fmt.Errorf("stored engine %s is build %s", short(ck), short(e.Build))
	}
	r.engines.byCk[ck] = e
	return e, nil
}

// legacyMatch: a version released before AM2 recorded its engine by
// Engine.Checksum (engine files only), not by the whole zip. Its engine, stored
// under that checksum when AM2 was deployed, is the right one only if it also
// holds exactly the package the run is pinned to.
func legacyMatch(e *Engine, ck, slug, pkgChecksum string) bool {
	p := e.Packages[slug]
	return e.Checksum == ck && p != nil && p.Checksum == pkgChecksum
}

// StoreLegacyEngine keeps an engine from before AM2 (the zip a pre-AM2 runner
// image carries) where engineFor finds it: under the checksum its released
// definitions recorded, and only when a released definition of one of its
// packages — same slug, version and package checksum — was recorded with it.
// The deploy runs it once (agent-runner store-engine <zip>) so the installs
// pinned to those versions keep running their own engine. It returns one line
// per package.
func StoreLegacyEngine(ctx context.Context, st *am.Store, files storage.Backend, e *Engine) ([]string, error) {
	var out []string
	stored := map[string]bool{}
	slugs := make([]string, 0, len(e.Packages))
	for s := range e.Packages {
		slugs = append(slugs, s)
	}
	sort.Strings(slugs)
	for _, slug := range slugs {
		p := e.Packages[slug]
		d, err := st.DefinitionBySlugVersion(ctx, p.Slug, p.Version)
		switch {
		case errors.Is(err, am.ErrNotFound):
			out = append(out, fmt.Sprintf("%s %s: not released — nothing to keep", p.Slug, p.Version))
			continue
		case err != nil:
			return out, err
		case d.Checksum != p.Checksum || (d.EngineChecksum != e.Checksum && d.EngineChecksum != e.Build):
			out = append(out, fmt.Sprintf("%s %s: released with another engine (%s) — not this one", p.Slug, p.Version, short(d.EngineChecksum)))
			continue
		}
		key := am.EngineKey(d.EngineChecksum)
		if !stored[key] {
			if ok, err := files.Exists(ctx, key); err != nil {
				return out, err
			} else if !ok {
				if err := files.Upload(ctx, key, bytes.NewReader(e.zipBytes), "application/zip", int64(len(e.zipBytes))); err != nil {
					return out, err
				}
			}
			stored[key] = true
		}
		out = append(out, fmt.Sprintf("%s %s: its engine is kept at %s", p.Slug, p.Version, key))
	}
	return out, nil
}

// register writes each package as a new version, or finds it released;
// a changed package under a released version is refused, loudly.
func (r *Runner) register(ctx context.Context) error {
	for _, p := range r.Engine.Packages {
		d := am.Definition{Slug: p.Slug, Version: p.Version, Name: p.Manifest.Name, Manifest: p.RawManifest,
			Instructions: p.Instructions, Checksum: p.Checksum, EngineChecksum: r.Engine.Build}
		cur, err := r.Store.DefinitionBySlugVersion(ctx, p.Slug, p.Version)
		switch {
		case errors.Is(err, am.ErrNotFound):
			if _, err := r.Store.InsertDefinition(ctx, d); err != nil {
				return err
			}
			r.Log.Info("agent package registered", "slug", p.Slug, "version", p.Version, "checksum", p.Checksum[:12])
		case err != nil:
			return err
		case cur.Checksum != p.Checksum:
			r.Log.Error("agent package changed without a new version: not registered — bump its version",
				"slug", p.Slug, "version", p.Version, "released", short(cur.Checksum), "this_engine", short(p.Checksum))
		default:
			r.Log.Info("agent package already released", "slug", p.Slug, "version", p.Version, "engine", short(cur.EngineChecksum))
		}
	}
	return nil
}

// ── the upgrade self-test ────────────────────────────────────────────────

type upgradeParams struct {
	UpgradeTo uuid.UUID       `json:"upgrade_to"`
	Bindings  json.RawMessage `json:"bindings"`
}

func (rc *runCtx) upgradeTarget() (upgradeParams, bool) {
	var p upgradeParams
	if rc.run.Kind != am.KindSelfTest || json.Unmarshal(rc.run.Params, &p) != nil || p.UpgradeTo == uuid.Nil {
		return p, false
	}
	return p, true
}

// finishUpgrade switches the install to the version its self-test ran —
// only when the run passed and its package's own expectations hold.
func (rc *runCtx) finishUpgrade(ctx context.Context, computed blobRef, passed bool, why am.PersonText) {
	p, ok := rc.upgradeTarget()
	if !ok {
		return
	}
	st := rc.r.Store
	to, err := st.DefinitionByID(ctx, p.UpgradeTo)
	if err != nil {
		return
	}
	kept := am.Words(am.Text(upgradeKept))
	if !passed {
		// why is the round's own sentence (am.Failure.What, am.Completion.What), already said in the thread
		_ = st.PostAgentText(ctx, rc.run, am.AgentText{Tone: "error", Text: am.Text(
			"ยังไม่อัปเกรดเป็นรุ่น %s ครับ การทดสอบกับข้อมูลตัวอย่างไม่ผ่าน: %s %s", am.Version(to.Version), am.Words(why), kept)})
		return
	}
	if problems := rc.selfTestExpectations(ctx, computed); len(problems) > 0 {
		// the person reads why, in words; names and values are kept for the run page
		rc.r.logger().Warn("upgrade self-test differs", "run", rc.run.ID, "to", to.Version, "problems", expectDetail(problems))
		rc.r.noteStep(ctx, rc.run, "self_test_expect", expectDetail(problems))
		_ = st.PostAgentText(ctx, rc.run, am.AgentText{Tone: "error", Text: upgradeRefused(to.Version, problems)})
		return
	}
	if cur, err := st.InstallByID(ctx, rc.install.ID); err == nil && cur.DefinitionID == to.ID {
		return // already switched (a resumed run): switching again would lose the version to roll back to
	}
	if _, err := st.SwitchInstall(ctx, rc.install.ID, to, p.Bindings); err != nil {
		rc.r.logger().Warn("upgrade switch failed", "run", rc.run.ID, "to", to.Version, "err", err)
		rc.r.noteStep(ctx, rc.run, "upgrade_switch", err.Error())
		_ = st.PostAgentText(ctx, rc.run, am.AgentText{Tone: "error", Text: am.Text(
			"ทดสอบรุ่น %s ผ่านแล้ว แต่สลับไปใช้รุ่นใหม่ไม่สำเร็จ — กดอัปเกรดอีกครั้งในหน้าตั้งค่า %s", am.Version(to.Version), kept)})
		return
	}
	_ = st.PostText(ctx, rc.run, am.Text("อัปเกรดเป็นรุ่น %s แล้วครับ ผ่านการทดสอบกับข้อมูลตัวอย่างตรงตามที่รุ่นนี้กำหนด "+
		"รอบที่ทำไปแล้วยังเป็นของรุ่นเดิม และย้อนกลับได้ในหน้าตั้งค่า", am.Version(to.Version)))
}

// noteStep keeps a technical detail on the run (the run page and a problem
// report show a run's steps), for something that is not a step's own failure.
func (r *Runner) noteStep(ctx context.Context, run am.Run, name, detail string) {
	st, done, err := r.Store.BeginStep(ctx, run.ID, name, "check", am.StepKey(run.ID, name, detail), json.RawMessage(`{}`))
	if err != nil || done {
		return
	}
	_ = r.Store.FinishStep(ctx, st.ID, "failed", nil, am.TrimText(detail, 1500), "", "", 0)
}

func joinLines(xs []string) string {
	out := ""
	for i, x := range xs {
		if i > 0 {
			out += " · "
		}
		out += x
	}
	return out
}

const upgradeKept = "ยังใช้รุ่นเดิมอยู่ตามปกติ ไฟล์และผลงานเดิมยังอยู่ครบ ถ้าต้องการให้ผู้ดูแลตรวจ กด “แจ้งปัญหา”"

// expectProblem is one way a self-test missed what its version expects.
// Words is what a person reads (the period, a question that should not have
// been asked). A figure has none: its name is the engine's, so figures are
// counted for the person. Detail is the whole of it, for the run page.
type expectProblem struct {
	Words  am.PersonText
	Figure bool
	Detail string
}

// upgradeRefused is what a person reads when an upgrade's self-test differs
// from what the new version expects: why, in words.
func upgradeRefused(version string, problems []expectProblem) am.PersonText {
	var said []am.PersonText
	figures := 0
	for _, p := range problems {
		if p.Figure {
			figures++
		} else if !p.Words.Empty() {
			said = append(said, p.Words)
		}
	}
	if figures > 0 {
		said = append(said, am.Text("ตัวเลขสรุป %s ค่าไม่ตรงกับที่รุ่นนี้กำหนด", am.Count(figures)))
	}
	if len(said) == 0 {
		said = append(said, am.Text("ผลทดสอบต่างจากที่รุ่นนี้กำหนดไว้"))
	}
	return am.Text("ยังไม่อัปเกรดเป็นรุ่น %s ครับ ผลทดสอบกับข้อมูลตัวอย่างไม่ตรงตามที่รุ่นนี้กำหนด: %s %s", am.Version(version),
		am.Words(am.Join(" · ", said...)), am.Words(am.Text(upgradeKept)))
}

// expectDetail is everything that differed, names and values, for the run.
func expectDetail(problems []expectProblem) string {
	var out []string
	for _, p := range problems {
		out = append(out, p.Detail)
	}
	return joinLines(out)
}

// selfTestExpectations compares a self-test run with its package's
// self-test.json "expect": no questions asked, the period, and the numbers
// it names (P&L lines by key, or totals).
func (rc *runCtx) selfTestExpectations(ctx context.Context, computed blobRef) []expectProblem {
	var st struct {
		Expect struct {
			Findings *[]json.RawMessage         `json:"findings"`
			Period   string                     `json:"period"`
			Lines    map[string]string          `json:"lines"`
			Totals   map[string]json.RawMessage `json:"totals"`
		} `json:"expect"`
	}
	if json.Unmarshal(rc.pkg.SelfTest, &st) != nil {
		return []expectProblem{{Words: am.Text("อ่านสิ่งที่รุ่นนี้กำหนดไว้ไม่ได้"), Detail: "self-test.json อ่านไม่ได้"}}
	}
	var out []expectProblem
	exp := st.Expect
	if exp.Findings != nil && len(*exp.Findings) == 0 {
		if cards, _ := rc.r.Store.RunCards(ctx, rc.run.ID); len(cards) > 0 {
			out = append(out, expectProblem{Words: am.Text("ถาม %s คำถาม ทั้งที่ข้อมูลตัวอย่างไม่ควรมีคำถาม", am.Count(len(cards))),
				Detail: fmt.Sprintf("ถาม %d คำถาม (คาดว่าไม่ถาม)", len(cards))})
		}
	}
	run, _ := rc.r.Store.RunByID(ctx, rc.run.ID)
	if exp.Period != "" && (run.Period == nil || *run.Period != exp.Period) {
		got := ""
		if run.Period != nil {
			got = *run.Period
		}
		out = append(out, expectProblem{Words: am.Text("งวดของผลทดสอบไม่ตรงกับที่รุ่นนี้กำหนด (%s)", am.Name(exp.Period)),
			Detail: "งวดไม่ตรง: ได้ " + got + " คาด " + exp.Period})
	}
	if len(exp.Lines) == 0 && len(exp.Totals) == 0 {
		return out
	}
	_, data, err := rc.readComputed(ctx, computed)
	if err != nil {
		return append(out, expectProblem{Words: am.Text("อ่านผลคำนวณของการทดสอบไม่ได้"), Detail: "อ่านผลคำนวณไม่ได้"})
	}
	var c struct {
		Lines []struct {
			Key     string  `json:"key"`
			Current *string `json:"current"`
		} `json:"lines"`
		Totals map[string]json.RawMessage `json:"totals"`
	}
	_ = json.Unmarshal(data, &c)
	for k, want := range exp.Lines {
		found := false
		for _, l := range c.Lines {
			if l.Key == k && l.Current != nil {
				found = true
				if *l.Current != want {
					out = append(out, expectProblem{Figure: true, Detail: fmt.Sprintf("%s = %s (คาด %s)", k, *l.Current, want)})
				}
			}
		}
		if !found {
			out = append(out, expectProblem{Figure: true, Detail: k + " ไม่มีในผลคำนวณ"})
		}
	}
	for k, want := range exp.Totals {
		if got, ok := c.Totals[k]; !ok || !am.JSONEqual(got, want) {
			out = append(out, expectProblem{Figure: true, Detail: fmt.Sprintf("%s = %s (คาด %s)", k, string(got), string(want))})
		}
	}
	// a map is walked in no order: the run's record and the person's count are the same every time
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Figure != out[j].Figure {
			return !out[i].Figure
		}
		return out[i].Figure && out[i].Detail < out[j].Detail
	})
	return out
}
