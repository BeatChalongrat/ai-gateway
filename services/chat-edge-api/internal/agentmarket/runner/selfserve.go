package runner

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── AM3 on the execution plane ───────────────────────────────────────────
//
//   - a budget that runs out parks the run with a notice naming who to ask
//     (never a failure), and the hourly pass resumes it once there is room;
//   - a disabled agent's run that was already claimed ends cancelled, with a
//     notice, before any step;
//   - the catalog's samples: every released version's own self-test, run
//     once under an install nobody owns, so a stranger can open real outputs;
//   - sign-off: a review job stamps the run's files with the reviewer's name.

// errBudgetPark carries the limits of the budget that ran out.
type errBudgetPark struct{ st am.BudgetState }

func (e *errBudgetPark) Error() string { return "budget exhausted: " + e.st.Exhausted }

// budgetCheck is the costly-step check: nil, or an errBudgetPark.
func (rc *runCtx) budgetCheck(ctx context.Context) error {
	st, err := rc.r.Store.Budget(ctx, rc.install)
	if err != nil {
		return nil // an unreadable budget never blocks work (AM1 checked the install's alone)
	}
	if st.Exhausted != "" {
		return &errBudgetPark{st: st}
	}
	return nil
}

// parkForBudget: the run waits (awaiting_input, "budget_<which>") with a
// notice that names who to ask and offers "ลองต่อ".
func (r *Runner) parkForBudget(rc *runCtx, e *errBudgetPark) error {
	bg := context.Background()
	if err := r.Store.Park(bg, rc.run.ID, r.ID, "budget_"+e.st.Exhausted); err != nil {
		return err
	}
	contact := e.st.Contact
	_ = r.Store.AddNotice(bg, r.Store.Pool, rc.run, am.Notice{Text: am.BudgetNotice(e.st), Tone: "warning", Action: "resume_budget",
		Reason: "budget_" + e.st.Exhausted, Contact: &contact})
	r.syncProgress(bg, rc.run.ID)
	rc.log.Info("run waits for budget", "which", e.st.Exhausted)
	return nil
}

// disabledStop ends a claimed run of an agent the organisation switched off
// (the control plane already refuses new ones; this closes the race).
func (r *Runner) disabledStop(ctx context.Context, run am.Run) bool {
	in, err := r.Store.InstallByID(ctx, run.InstallID)
	if err != nil || in.Status == "catalog" {
		return false
	}
	st, err := r.Store.CatalogStateFor(ctx, in.OrgID, in.Slug)
	if err != nil || st.Status != am.StatusDisabled {
		return false
	}
	bg := context.Background()
	_ = r.Store.Finish(bg, run.ID, r.ID, am.RunCancelled, "agent_disabled")
	text := am.Text("ผู้ดูแลปิด agent นี้ไว้ชั่วคราว งานนี้จึงหยุดก่อนเริ่ม%s — ไฟล์และประวัติของคุณยังอยู่ครบ", am.Aside(st.Reason))
	_ = r.Store.PostAgentText(bg, run, am.AgentText{Text: text, Tone: "warning"})
	r.syncProgress(bg, run.ID)
	return true
}

// selfTestSettings: the identity the package's sample data was made for
// (self-test.json "settings", AM3) overlays the install's own for a self-test
// — a stranger who typed their real company tax ID must not be asked why the
// sample invoices are addressed to the sample company.
func (rc *runCtx) selfTestSettings() {
	if rc.run.Kind != am.KindSelfTest || len(rc.pkg.SelfTest) == 0 {
		return
	}
	var st struct {
		Settings map[string]any `json:"settings"`
	}
	if json.Unmarshal(rc.pkg.SelfTest, &st) != nil || len(st.Settings) == 0 {
		return
	}
	if rc.defaults == nil {
		rc.defaults = map[string]any{}
	}
	for k, v := range st.Settings {
		rc.defaults[k] = v
	}
}

// textModels are the models a person's typed words (a question about the
// numbers, a card answered in a sentence) may be sent to, with that run's
// table or card (AM3). An agent that declares a text model and has one write
// its report (the financial report) uses its primary and fallback, as its
// data sheet says; an extraction agent (narrative "rules": invoices, expense
// claims) sends nothing to a model outside the organisation — only the
// self-hosted ones, or none (a keyword reading and the defaults then).
//
// AM4: both follow the round's model choice and the organisation's rule — a
// model outside the organisation only while documents may leave it.
func (rc *runCtx) textModels() []string {
	m := rc.pkg.Manifest
	declared := false
	for _, c := range append(append([]string{}, m.Capabilities.Required...), m.Capabilities.Optional...) {
		declared = declared || c == "model.text"
	}
	all := rc.writingModels()
	if declared && m.Narrative != "rules" {
		return all
	}
	var inside []string
	for _, x := range all {
		if rc.r.Model.IsSelfHosted(x) {
			inside = append(inside, x)
		}
	}
	return inside
}

// ── catalog samples ──────────────────────────────────────────────────────

// ensureSamples queues a sample for every released version that has none,
// once the sandbox is ready. The runner makes them with its own lane like any
// other run; the catalog reads them.
func (r *Runner) ensureSamples(ctx context.Context) {
	for i := 0; i < 120 && !r.Sandbox(); i++ {
		select {
		case <-ctx.Done():
			return
		case <-time.After(5 * time.Second):
		}
	}
	if !r.Sandbox() {
		return
	}
	missing, err := r.Store.MissingSamples(ctx)
	if err != nil {
		r.Log.Warn("catalog samples: list failed", "err", err)
		return
	}
	caps := r.Capabilities()
	for _, d := range missing {
		m, err := am.ParseManifest(d.Manifest)
		if err != nil {
			continue
		}
		res, err := am.Resolve(m, caps, 1)
		if err != nil {
			r.Log.Warn("catalog sample skipped: a capability is missing", "slug", d.Slug, "version", d.Version, "err", err)
			continue
		}
		b, _ := json.Marshal(res)
		if run, err := r.Store.CreateCatalogSample(ctx, d, b, res.Version); err != nil {
			r.Log.Warn("catalog sample not queued", "slug", d.Slug, "err", err)
		} else {
			r.Log.Info("catalog sample queued", "slug", d.Slug, "version", d.Version, "run", run.ID)
		}
	}
}

// ── sign-off ─────────────────────────────────────────────────────────────

type reviewParams struct {
	ReviewID      uuid.UUID `json:"review_id"`
	Reviewer      string    `json:"reviewer"`
	ReviewerEmail string    `json:"reviewer_email"`
	AtLabel       string    `json:"at_label"`
	Note          string    `json:"note"`
	Preparer      string    `json:"preparer"`
	Agent         string    `json:"agent"`
	RunRef        string    `json:"run_ref"`
}

// stampable are the formats the stamp op writes a name into.
var stampable = map[string]bool{"docx": true, "pptx": true, "xlsx": true}

// reviewJob re-issues the parent run's newest files with the reviewer's name
// on their face: a new version of each file (the unsigned ones stay), the PDF
// converted again from the stamped report and gated like any PDF. The stamp
// is a platform op of this runner's own engine — never the run's pinned
// package — because it changes no number: it only adds the sign-off.
func (r *Runner) reviewJob(ctx context.Context, job am.Run, log *slog.Logger) error {
	if job.ParentRunID == nil {
		return r.fail(ctx, job, "no_parent", errors.New("review job without a run"))
	}
	var p reviewParams
	if err := json.Unmarshal(job.Params, &p); err != nil || p.ReviewID == uuid.Nil {
		return r.fail(ctx, job, "bad_params", errors.New("review job without its review"))
	}
	ps, err := r.parentState(ctx, *job.ParentRunID)
	if err != nil {
		return r.fail(ctx, job, "review_failed", err)
	}
	rc, err := r.load(ctx, job, log)
	if err != nil {
		return r.fail(ctx, job, "load", err)
	}
	all, err := r.Store.RunArtifacts(ctx, ps.run.ID)
	if err != nil {
		return r.fail(ctx, job, "review_failed", err)
	}
	latest := map[string]am.Artifact{}
	for _, a := range all {
		if cur, ok := latest[a.Format]; !ok || a.Version > cur.Version {
			latest[a.Format] = a
		}
	}
	period := ""
	if ps.run.Period != nil {
		period = *ps.run.Period
	}
	ids := []string{}
	for f, a := range latest {
		ids = append(ids, f+":"+a.ID.String())
	}
	key := am.StepKey(job.ID, "stamp", p.ReviewID, ids)
	var produced []renderedArt
	var skipped []string
	out, err := rc.step(ctx, "stamp", "deliverable", key, map[string]any{"review_id": p.ReviewID, "files": len(latest)}, false,
		func(ctx context.Context) (any, float64, string, string, error) {
			var inputs []Input
			var files []map[string]string
			var cleanups []func()
			defer func() {
				for _, c := range cleanups {
					c()
				}
			}()
			for f, a := range latest {
				if !stampable[f] {
					if f != "pdf" {
						skipped = append(skipped, a.Name)
					}
					continue
				}
				path, cleanup, err := r.Files.OpenLocalCopy(ctx, a.StorageKey)
				if err != nil {
					return nil, 0, "", "", err
				}
				cleanups = append(cleanups, cleanup)
				in := "stamp_" + f + filepath.Ext(a.Name)
				inputs = append(inputs, Input{Name: in, LocalPath: path})
				files = append(files, map[string]string{"name": a.Name, "input": in, "format": f})
			}
			if len(files) == 0 {
				return map[string]any{"artifacts": []any{}, "skipped": skipped}, 0, "", "", nil
			}
			res, err := r.Exec.Run(ctx, map[string]any{"op": "stamp_review", "files": files, "review": map[string]string{
				"reviewer": p.Reviewer, "reviewer_email": p.ReviewerEmail, "at_label": p.AtLabel, "note": p.Note,
				"preparer": p.Preparer, "agent": p.Agent, "run_ref": p.RunRef}}, inputs)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			var arts []struct {
				Name    string `json:"name"`
				Format  string `json:"format"`
				Stamped bool   `json:"stamped"`
			}
			if err := res.Decode("artifacts", &arts); err != nil {
				return nil, 0, "", "", err
			}
			var stampedDocx []byte
			for _, x := range arts {
				local, ok := res.Artifacts[x.Name]
				if !ok || !x.Stamped {
					skipped = append(skipped, x.Name)
					continue
				}
				data, err := os.ReadFile(local)
				if err != nil {
					return nil, 0, "", "", err
				}
				ra, err := r.storeStamped(ctx, ps.run, rc.install.Slug, period, x.Format, latest[x.Format].Kind, data)
				if err != nil {
					return nil, 0, "", "", err
				}
				produced = append(produced, ra)
				if x.Format == "docx" {
					stampedDocx = data
				}
			}
			// The PDF is the stamped report converted again (and gated like any PDF).
			if _, hadPDF := latest["pdf"]; hadPDF && stampedDocx != nil {
				pdf, checks := rc.pdfFromDocx(ctx, "report.docx", stampedDocx)
				okPDF := pdf != nil
				for _, c := range checks {
					okPDF = okPDF && c.OK
				}
				if okPDF {
					ra, err := r.storeStamped(ctx, ps.run, rc.install.Slug, period, "pdf", latest["pdf"].Kind, pdf)
					if err != nil {
						return nil, 0, "", "", err
					}
					q, _ := am.MarshalIn(am.LangOf(ctx), map[string]any{"passed": true, "checks": checks})
					ra.Quality = q
					produced = append(produced, ra)
				} else {
					skipped = append(skipped, latest["pdf"].Name+am.T(am.LangOf(ctx), " (แปลง PDF ไม่ผ่าน ใช้ .docx ที่มีชื่อผู้ตรวจทานแทน)"))
				}
			} else if hadPDF {
				skipped = append(skipped, latest["pdf"].Name)
			}
			return map[string]any{"artifacts": produced, "skipped": skipped}, rc.sandboxCredits(res.Elapsed), "deliverables/signoff.py", "", nil
		})
	if err != nil {
		return rc.stepFailed("stamp", err)
	}
	var so struct {
		Artifacts []renderedArt `json:"artifacts"`
		Skipped   []string      `json:"skipped"`
	}
	if err := json.Unmarshal(out, &so); err != nil {
		return r.fail(ctx, job, "review_failed", err)
	}
	review := map[string]any{"review_id": p.ReviewID, "reviewer": p.Reviewer, "at_label": p.AtLabel, "note": p.Note}
	versions := map[string]int{}
	var arts []am.Artifact
	for _, ra := range so.Artifacts {
		orig := latest[ra.Format]
		v := map[string]any{"passed": true, "review": review}
		if len(ra.Quality) > 0 {
			var q map[string]any
			if json.Unmarshal(ra.Quality, &q) == nil {
				for k, x := range q {
					v[k] = x
				}
				v["review"] = review
			}
		}
		vb, _ := json.Marshal(v)
		row, _, err := r.Store.InsertArtifact(ctx, am.Artifact{RunID: ps.run.ID, InstallID: ps.run.InstallID, Format: ra.Format,
			Kind: orig.Kind, Name: ra.Name, Version: ra.Version, StorageKey: ra.Key, Size: ra.Size, SHA256: ra.SHA256,
			ThemeID: orig.ThemeID, ThemeVersion: orig.ThemeVersion, Citations: orig.Citations, Validation: vb,
			ChartBindings: orig.ChartBindings, ProducedByStep: key}, ps.run.TenantID, ps.run.UserID)
		if err != nil {
			return r.fail(ctx, job, "review_failed", err)
		}
		arts = append(arts, row)
		versions[ra.Format] = ra.Version
	}
	vb, _ := json.Marshal(versions)
	_, _ = r.Store.Pool.Exec(ctx, `UPDATE agent_market.run_reviews SET artifact_versions = $2 WHERE id = $1`, p.ReviewID, vb)
	// the files of the round are its own: their names are readable in what is said of them
	var names []string
	for _, a := range latest {
		names = append(names, a.Name)
	}
	var notSigned []am.PersonText
	for _, s := range so.Skipped {
		if t := am.LabelFromPackage(s, am.NoText, names...); !t.EmptyIn(am.LangOf(ctx)) {
			notSigned = append(notSigned, t)
		}
	}
	payload := am.OutputsPayload{RunID: ps.run.ID.String(), Summary: ps.summary, Rendered: true,
		Model: &am.OutputsModel{Model: ps.narrative.Model, SelfHosted: ps.narrative.SelfHosted}, Credits: ps.run.CreditsSpent,
		Review: &am.OutputsReview{ID: p.ReviewID.String(), Reviewer: p.Reviewer, AtLabel: am.LabelFromPackage(p.AtLabel, am.NoText), Note: p.Note,
			Skipped: notSigned}}
	if ps.run.Period != nil {
		payload.PeriodLabel = am.Text("%s", am.Period(*ps.run.Period))
	}
	if v, ok := ps.summary["period_label"].(string); ok {
		payload.PeriodLabel = am.LabelFromPackage(v, payload.PeriodLabel)
	}
	for _, a := range arts {
		payload.Artifacts = append(payload.Artifacts, am.OutputArtifact{ID: a.ID.String(), Name: a.Name, Format: a.Format, Size: a.Size,
			Version: a.Version, Quality: a.Validation})
	}
	rid := ps.run.ID
	bg := am.InRunLang(context.Background(), ps.run) // AM11: the card is the run's, in the run's language
	if len(arts) > 0 {
		if _, _, err := r.Store.AddItem(bg, r.Store.Pool, ps.run.InstallID, ps.run.TenantID, ps.run.UserID, &rid, payload, nil); err != nil {
			return r.fail(ctx, job, "outputs", err)
		}
	}
	text := am.Text("ไฟล์ที่มีชื่อผู้ตรวจทาน (%s) พร้อมแล้วครับ ตัวเลขเหมือนฉบับเดิมทุกตัว ฉบับก่อนตรวจทานยังเก็บไว้ในประวัติ", am.Name(p.Reviewer))
	if len(notSigned) > 0 {
		text = am.Text("%s · ไฟล์ที่ใส่ชื่อไม่ได้: %s", am.Words(text), am.Words(am.Join(", ", notSigned...)))
	}
	_ = r.Store.PostAgentText(bg, ps.run, am.AgentText{Text: text, ReviewID: p.ReviewID.String()})
	return r.finish(rc, am.RunSucceeded, "")
}

// storeStamped uploads one stamped file as the next version of its format on the run.
func (r *Runner) storeStamped(ctx context.Context, run am.Run, slug, period, format, kind string, data []byte) (renderedArt, error) {
	v, err := r.Store.NextVersion(ctx, run.ID, format)
	if err != nil {
		return renderedArt{}, err
	}
	name := am.ArtifactName(slug, period, format, v)
	name = strings.TrimSuffix(name, "."+format) + "_reviewed." + format
	key := am.ArtifactKey(run.TenantID, run.ID, name)
	mime := map[string]string{"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
		"pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
		"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pdf": "application/pdf"}[format]
	if err := r.Files.Upload(ctx, key, bytes.NewReader(data), mime, int64(len(data))); err != nil {
		return renderedArt{}, err
	}
	sum := sha256.Sum256(data)
	return renderedArt{Name: name, Format: format, Kind: kind, Key: key, Size: int64(len(data)), SHA256: hex.EncodeToString(sum[:]),
		Version: v}, nil
}

// storageCapability says where files live and how long (the data sheet's facts).
func (r *Runner) storageCapability(backend string) am.Binding {
	staged, scratch, removed := orDefault(r.StagedTTL, 30*24*time.Hour), orDefault(r.ScratchTTL, 7*24*time.Hour),
		orDefault(r.RemovedTTL, 30*24*time.Hour)
	d, _ := json.Marshal(map[string]any{"backend": backend, "prefix": "agent-market/",
		"staged_hours": staged.Hours(), "scratch_days": scratch.Hours() / 24, "removed_days": removed.Hours() / 24})
	return am.Binding{Capability: "platform.storage", Impl: "storage:" + backend, Version: fmt.Sprintf("%.0fh/%.0fd/%.0fd",
		staged.Hours(), scratch.Hours()/24, removed.Hours()/24), Detail: d}
}
