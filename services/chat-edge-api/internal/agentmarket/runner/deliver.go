package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// computedView is the part of computed.json the runner reads.
type computedView struct {
	Period       string            `json:"period"`
	PeriodLabel  string            `json:"period_label"`
	PriorLabel   *string           `json:"prior_label"`
	HasPrior     bool              `json:"has_prior"`
	HasBudget    bool              `json:"has_budget"`
	Lines        []json.RawMessage `json:"lines"`
	Ratios       []json.RawMessage `json:"ratios"`
	TopMovements []json.RawMessage `json:"top_movements"`
	Exceptions   []json.RawMessage `json:"exceptions"`
	Quotable     map[string]string `json:"quotable"`
	QuotableIDs  []string          `json:"quotable_ids"`
	Fallback     json.RawMessage   `json:"fallback_narrative"`
	Source       json.RawMessage   `json:"source"`
	Excluded     json.RawMessage   `json:"excluded"`
	Citations    []json.RawMessage `json:"citations"`
	Materiality  string            `json:"materiality"`
	Script       string            `json:"script"`
}

func (rc *runCtx) readComputed(ctx context.Context, ref blobRef) (computedView, []byte, error) {
	body, _, _, err := rc.r.Files.Download(ctx, ref.Key)
	if err != nil {
		return computedView{}, nil, err
	}
	defer body.Close()
	data, err := io.ReadAll(body)
	if err != nil {
		return computedView{}, nil, err
	}
	var v computedView
	return v, data, json.Unmarshal(data, &v)
}

// ── narrative (the only AI step) ─────────────────────────────────────────

type narrativeOut struct {
	Narrative  json.RawMessage `json:"narrative"`
	Model      string          `json:"model"`
	SelfHosted bool            `json:"self_hosted"`
	Tried      []string        `json:"tried"`
	Tokens     int             `json:"tokens"`
	CostUSD    float64         `json:"cost_usd"`
}

func (rc *runCtx) narrative(ctx context.Context, computed blobRef, computeKey string) (narrativeOut, string, error) {
	if rc.pkg.Manifest.Narrative == "rules" {
		return rc.rulesNarrative(ctx, computed, computeKey)
	}
	// AM4: the round's choice first, then the runner's models — only those the
	// organisation lets see the computed table (choice.go).
	models := rc.writingModels()
	key := am.StepKey(rc.run.ID, "narrative", computeKey, rc.pkg.Checksum, strings.Join(models, "|"))
	out, err := rc.step(ctx, "narrative", "ai", key, map[string]any{"models": models}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			cv, _, err := rc.readComputed(ctx, computed)
			if err != nil {
				return nil, 0, "", "", err
			}
			user := narrativePrompt(cv)
			var parsed map[string]any
			c, tried, err := rc.r.Model.CompleteWith(ctx, models, rc.pkg.Instructions, user, func(s string) error {
				v, err := checkNarrative(s, cv)
				if err != nil {
					return err
				}
				parsed = v
				return nil
			})
			if err != nil {
				// No model answered: the rules write it. The report says so.
				fb := map[string]any{} // a package without a fallback narrative: never a nil map
				_ = json.Unmarshal(cv.Fallback, &fb)
				if fb == nil {
					fb = map[string]any{}
				}
				fb["written_by"] = "rules"
				nb, _ := json.Marshal(fb)
				// Refused answers were still paid for: the budget counts them.
				return narrativeOut{Narrative: nb, Tried: tried, Tokens: c.Tokens, CostUSD: c.CostUSD},
					c.CostUSD * rc.r.CreditsPerUSD, "metrics.py:fallback_narrative", "", nil
			}
			parsed["written_by"] = "model"
			parsed["model"] = c.Model
			nb, _ := json.Marshal(parsed)
			self := rc.r.Model.IsSelfHosted(c.Model)
			model := c.Model
			_ = rc.r.Store.SetRunFields(ctx, rc.run.ID, nil, &model, &self)
			return narrativeOut{Narrative: nb, Model: c.Model, SelfHosted: self, Tried: tried, Tokens: c.Tokens, CostUSD: c.CostUSD},
				c.CostUSD * rc.r.CreditsPerUSD, "", c.Model, nil
		})
	if err != nil {
		return narrativeOut{}, key, err
	}
	var n narrativeOut
	return n, key, json.Unmarshal(out, &n)
}

// checkNarrative reads a model's answer as the report's words, or says why
// it cannot be used (the model is asked again, then the next model).
func checkNarrative(s string, cv computedView) (map[string]any, error) {
	var v map[string]any
	if err := json.Unmarshal([]byte(extractJSON(s)), &v); err != nil {
		return nil, fmt.Errorf("ไม่ใช่ JSON")
	}
	sum, _ := v["summary"].(string)
	if strings.TrimSpace(sum) == "" {
		return nil, fmt.Errorf("ไม่มี summary")
	}
	var all []string
	all = append(all, sum)
	// AM5: each list is a list of sentences, whatever the model sent — a list
	// left null (or a sentence, or a number among the sentences) reached the
	// report's renderer as it was, and the round failed on it.
	for _, k := range []string{"movements", "watch", "next_steps"} {
		arr, _ := v[k].([]any)
		lines := []any{}
		for _, x := range arr {
			if s, ok := x.(string); ok && strings.TrimSpace(s) != "" && len(lines) < 6 {
				lines = append(lines, s)
				all = append(all, s)
			}
		}
		v[k] = lines
	}
	if err := GuardNumbers(strings.Join(all, "\n"), cv.Quotable, cv.QuotableIDs); err != nil {
		return nil, err
	}
	return v, nil
}

// rulesNarrative is the package's own summary (computed.json
// fallback_narrative) with no model step: an extraction agent has nothing
// for a model to write, and its documents are never sent to a text model.
func (rc *runCtx) rulesNarrative(ctx context.Context, computed blobRef, computeKey string) (narrativeOut, string, error) {
	key := am.StepKey(rc.run.ID, "narrative", computeKey, rc.pkg.Checksum, "rules")
	out, err := rc.step(ctx, "narrative", "compute", key, map[string]any{"written_by": "rules"}, false,
		func(ctx context.Context) (any, float64, string, string, error) {
			cv, _, err := rc.readComputed(ctx, computed)
			if err != nil {
				return nil, 0, "", "", err
			}
			fb := map[string]any{}
			_ = json.Unmarshal(cv.Fallback, &fb)
			fb["written_by"] = "rules"
			nb, _ := json.Marshal(fb)
			return narrativeOut{Narrative: nb}, 0, "fallback_narrative", "", nil
		})
	if err != nil {
		return narrativeOut{}, key, err
	}
	var n narrativeOut
	return n, key, json.Unmarshal(out, &n)
}

func narrativePrompt(cv computedView) string {
	var b strings.Builder
	fmt.Fprintf(&b, "งวด: %s\n", cv.PeriodLabel)
	if cv.PriorLabel != nil {
		fmt.Fprintf(&b, "เทียบกับ: %s\n", *cv.PriorLabel)
	}
	fmt.Fprintf(&b, "มีงบประมาณ: %v\n\n", cv.HasBudget)
	b.WriteString("ตารางงบกำไรขาดทุน (คำนวณแล้ว):\n")
	for _, l := range cv.Lines {
		b.Write(l)
		b.WriteByte('\n')
	}
	b.WriteString("\nอัตราส่วน:\n")
	for _, r := range cv.Ratios {
		b.Write(r)
		b.WriteByte('\n')
	}
	b.WriteString("\nรายการเคลื่อนไหวสำคัญ:\n")
	for _, m := range cv.TopMovements {
		b.Write(m)
		b.WriteByte('\n')
	}
	b.WriteString("\nรายการที่กฎตั้งข้อสังเกต (exceptions):\n")
	for _, e := range cv.Exceptions {
		b.Write(e)
		b.WriteByte('\n')
	}
	b.WriteString("\nตัวเลขที่อ้างได้ (คัดลอกตรงตัวอักษรเท่านั้น):\n")
	keys := make([]string, 0, len(cv.Quotable))
	for k := range cv.Quotable {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		fmt.Fprintf(&b, "- %s: %s\n", k, cv.Quotable[k])
	}
	b.WriteString("\nเขียนคำอธิบายตามรูปแบบ JSON ใน instructions ภาษาไทย ใช้ตัวเลขจากรายการด้านบนเท่านั้น")
	return b.String()
}

// ── render (+ quality gate) ──────────────────────────────────────────────

// SamplePrefix starts the name of every file a self-test makes (AM3).
const SamplePrefix = "sample_"

type renderedArt struct {
	Name          string          `json:"name"`
	Format        string          `json:"format"`
	Kind          string          `json:"kind"`
	Key           string          `json:"key"`
	Size          int64           `json:"size"`
	SHA256        string          `json:"sha256"`
	Version       int             `json:"version"`
	Quality       json.RawMessage `json:"quality"`
	Meta          json.RawMessage `json:"meta"`
	ChartBindings json.RawMessage `json:"chart_bindings"`
}

type renderOut struct {
	Artifacts []renderedArt   `json:"artifacts"`
	Theme     json.RawMessage `json:"theme"`
	Passed    bool            `json:"passed"`
}

func (rc *runCtx) theme(ctx context.Context) (map[string]any, int, *string) {
	theme := map[string]any{}
	raw, version, logo, err := rc.r.Store.LatestTheme(ctx, rc.run.TenantID)
	if err == nil && len(raw) > 0 {
		_ = json.Unmarshal(raw, &theme)
	} else if t, ok := rc.defaults["theme"].(map[string]any); ok {
		theme = t
	}
	if c, ok := rc.defaults["company"].(string); ok && c != "" {
		theme["company"] = c
	}
	if version == 0 {
		version = 1
	}
	theme["version"] = version
	return theme, version, logo
}

func (rc *runCtx) render(ctx context.Context, artifactRun uuid.UUID, computed blobRef, computeKey string, narrative narrativeOut,
	narrKey string, formats []string, name string) (renderOut, string, error) {
	st := rc.r.Store
	theme, themeVersion, logo := rc.theme(ctx)
	period := ""
	if rc.run.Period != nil {
		period = *rc.run.Period
	}
	if period == "" {
		if parent, err := st.RunByID(ctx, artifactRun); err == nil && parent.Period != nil {
			period = *parent.Period
		}
	}
	parentRun := rc.run
	if artifactRun != rc.run.ID {
		if p, err := st.RunByID(ctx, artifactRun); err == nil {
			parentRun = p
		}
	}
	// AM3: a self-test's files (and the catalog's samples) say so in their
	// name — a stranger's own register once had the sample's exact name.
	prefix := ""
	if parentRun.Kind == am.KindSelfTest {
		prefix = SamplePrefix
	}
	names := map[string]string{}
	versions := map[string]int{}
	for _, f := range formats {
		v, err := st.NextVersion(ctx, artifactRun, f)
		if err != nil {
			return renderOut{}, "", err
		}
		versions[f] = v
		names[f] = prefix + am.ArtifactName(rc.install.Slug, period, f, v)
	}
	var assumptions []am.Assumption
	_ = json.Unmarshal(parentRun.Assumptions, &assumptions)
	pres := rc.presentation()
	// PDF (AM2): the finished .docx converted by LibreOffice and gated; the
	// .docx is rendered for it even when it was not asked for (and then not
	// delivered).
	wantPDF := contains(formats, "pdf")
	var pyFormats []string
	for _, f := range formats {
		if f != "pdf" {
			pyFormats = append(pyFormats, f)
		}
	}
	hiddenDocx := wantPDF && !contains(pyFormats, "docx")
	if hiddenDocx {
		pyFormats = append(pyFormats, "docx")
		names["docx"] = strings.TrimSuffix(names["pdf"], ".pdf") + ".docx"
	}
	// The customer's own template, filled on every run once one is saved (AM2).
	tpl, tplFile, err := rc.templateInput(ctx)
	if err != nil {
		return renderOut{}, "", err
	}
	var tplKey any
	if tpl != nil {
		v, err := st.NextVersion(ctx, artifactRun, "template")
		if err != nil {
			return renderOut{}, "", err
		}
		versions["template"] = v
		names["template"] = prefix + am.TemplateArtifactName(rc.install.Slug, period, tpl.Kind, v)
		tplKey = map[string]any{"sha256": tpl.SHA256, "version": tpl.Version, "mapping": tpl.Mapping}
	}
	keyParts := []any{computeKey, narrKey, formats, names, themeVersion, theme, pres, assumptions}
	if tplKey != nil {
		keyParts = append(keyParts, tplKey)
	}
	key := am.StepKey(rc.run.ID, name, keyParts...)
	out, err := rc.step(ctx, name, "deliverable", key, map[string]any{"formats": formats, "names": names, "theme_version": themeVersion}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			cpath, cleanup, err := rc.localBlob(ctx, computed.Key)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			inputs := []Input{{Name: "computed.json", LocalPath: cpath}}
			req := map[string]any{"op": "render", "package": rc.pkg.PyPackage, "computed": "computed.json", "formats": pyFormats,
				"names": names, "theme": theme, "narrative": json.RawMessage(narrative.Narrative), "assumptions": assumptions,
				"presentation": pres,
				"run": map[string]any{"id": artifactRun.String(), "date": rc.today(), "agent_version": rc.pkg.Version,
					"preparer": rc.preparer(ctx, parentRun)},
				"model_info": map[string]any{"model": narrative.Model, "self_hosted": narrative.SelfHosted}}
			if tpl != nil {
				tpath, tclean, err := rc.r.Files.OpenLocalCopy(ctx, tplFile.StorageKey)
				if err != nil {
					return nil, 0, "", "", err
				}
				defer tclean()
				input := "template" + filepath.Ext(tplFile.Name)
				inputs = append(inputs, Input{Name: input, LocalPath: tpath})
				req["template"] = map[string]any{"input": input, "name": names["template"],
					"saved": map[string]any{"slots": tpl.Slots, "mapping": tpl.Mapping}}
			}
			if logo != nil {
				lpath, lclean, err := rc.r.Files.OpenLocalCopy(ctx, *logo)
				if err == nil {
					defer lclean()
					inputs = append(inputs, Input{Name: "logo.png", LocalPath: lpath})
					req["logo"] = "logo.png"
				}
			}
			res, err := rc.exec(ctx, req, inputs)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			var r struct {
				Passed    bool              `json:"passed"`
				Artifacts []json.RawMessage `json:"artifacts"`
				Theme     json.RawMessage   `json:"theme"`
			}
			if err := json.Unmarshal(mustJSON(res.Result), &r); err != nil {
				return nil, 0, "", "", err
			}
			ro := renderOut{Theme: r.Theme, Passed: r.Passed}
			var failed []string
			docxPath := ""
			for _, a := range r.Artifacts {
				var meta struct {
					Name          string          `json:"name"`
					Format        string          `json:"format"`
					Kind          string          `json:"kind"`
					Size          int64           `json:"size"`
					SHA256        string          `json:"sha256"`
					Quality       json.RawMessage `json:"quality"`
					Meta          json.RawMessage `json:"meta"`
					ChartBindings json.RawMessage `json:"chart_bindings"`
				}
				if err := json.Unmarshal(a, &meta); err != nil {
					return nil, 0, "", "", err
				}
				var q struct {
					Passed bool `json:"passed"`
					Checks []struct {
						Name   string `json:"name"`
						OK     bool   `json:"ok"`
						Detail string `json:"detail"`
						Label  string `json:"label"`
					} `json:"checks"`
				}
				_ = json.Unmarshal(meta.Quality, &q)
				if !q.Passed {
					for _, c := range q.Checks {
						if !c.OK {
							failed = append(failed, checkLine(meta.Format, c.Label, c.Detail))
						}
					}
					continue
				}
				path, ok := res.Artifacts[meta.Name]
				if !ok {
					return nil, 0, "", "", fmt.Errorf("render produced no %s", meta.Name)
				}
				if meta.Format == "docx" {
					docxPath = path
					if hiddenDocx {
						continue // rendered for the PDF only
					}
				}
				if meta.Kind == "template" {
					q, err := rc.recalcTemplate(ctx, path, meta.Name, meta.Meta, meta.Quality)
					if err != nil {
						return nil, 0, "", "", err
					}
					meta.Quality = q
					fi, err := os.Stat(path)
					if err != nil {
						return nil, 0, "", "", err
					}
					meta.Size, meta.SHA256 = fi.Size(), fileSHA(path)
				}
				f, err := os.Open(path)
				if err != nil {
					return nil, 0, "", "", err
				}
				skey := am.ArtifactKey(rc.run.TenantID, artifactRun, meta.Name)
				err = rc.r.Files.Upload(ctx, skey, f, "", meta.Size)
				f.Close()
				if err != nil {
					return nil, 0, "", "", err
				}
				ro.Artifacts = append(ro.Artifacts, renderedArt{Name: meta.Name, Format: meta.Format, Kind: meta.Kind, Key: skey,
					Size: meta.Size, SHA256: meta.SHA256, Version: versions[meta.Format], Quality: meta.Quality, Meta: meta.Meta,
					ChartBindings: meta.ChartBindings})
			}
			if wantPDF && len(failed) == 0 {
				pdfArt, why, err := rc.deliverPDF(ctx, artifactRun, docxPath, names, versions)
				if err != nil {
					return nil, 0, "", "", err
				}
				if len(why) > 0 {
					failed = append(failed, why...)
				} else {
					ro.Artifacts = append(ro.Artifacts, pdfArt)
				}
			}
			if len(failed) > 0 {
				// A run is not "succeeded" until its files pass the gate.
				return nil, rc.sandboxCredits(res.Elapsed), "deliverables/quality.py", "",
					fmt.Errorf("quality gate: %s", strings.Join(failed, "; "))
			}
			return ro, rc.sandboxCredits(res.Elapsed), "deliverables/render.py", "", nil
		})
	if err != nil {
		if strings.Contains(err.Error(), "quality gate") {
			return renderOut{}, key, &qualityErr{error: err, failed: gateLines(err)}
		}
		return renderOut{}, key, err
	}
	var ro renderOut
	return ro, key, json.Unmarshal(out, &ro)
}

// preparer is who prepared the round, as a document names them (AM5: the
// reconciliation statement says who prepared it and when).
func (rc *runCtx) preparer(ctx context.Context, run am.Run) string {
	return rc.r.Store.DisplayName(ctx, rc.install.OrgID, run.UserID, "")
}

type qualityErr struct {
	error
	// failed are the checks that did not pass, as the package words them.
	failed []string
}

// ── publish ──────────────────────────────────────────────────────────────

func (rc *runCtx) publish(ctx context.Context, artifactRun uuid.UUID, ro renderOut, renderKey string, computed blobRef) ([]am.Artifact, error) {
	key := am.StepKey(rc.run.ID, "publish", renderKey)
	var arts []am.Artifact
	_, err := rc.step(ctx, "publish", "publish", key, map[string]any{"artifacts": len(ro.Artifacts)}, false,
		func(ctx context.Context) (any, float64, string, string, error) {
			var theme struct {
				ID      string `json:"id"`
				Version int    `json:"version"`
			}
			_ = json.Unmarshal(ro.Theme, &theme)
			// Every number's source (file · sheet · cells · script) travels with the file.
			cv, _, err := rc.readComputed(ctx, computed)
			if err != nil {
				return nil, 0, "", "", err
			}
			cites := make([]json.RawMessage, 0, len(cv.Citations))
			for _, c := range cv.Citations {
				var m map[string]any
				if json.Unmarshal(c, &m) == nil {
					if srcs, ok := m["sources"].([]any); ok {
						for _, s := range srcs {
							if sm, ok := s.(map[string]any); ok {
								delete(sm, "row_numbers")
							}
						}
					}
					cites = append(cites, mustJSON(m))
				}
			}
			citeJSON := mustJSON(cites)
			ids := []string{}
			for _, a := range ro.Artifacts {
				row, _, err := rc.r.Store.InsertArtifact(ctx, am.Artifact{RunID: artifactRun, InstallID: rc.run.InstallID,
					Format: a.Format, Kind: a.Kind, Name: a.Name, Version: a.Version, StorageKey: a.Key, Size: a.Size,
					SHA256: a.SHA256, ThemeID: theme.ID, ThemeVersion: theme.Version, Validation: a.Quality,
					ChartBindings: a.ChartBindings, Citations: citeJSON, ProducedByStep: key}, rc.run.TenantID, rc.run.UserID)
				if err != nil {
					return nil, 0, "", "", err
				}
				ids = append(ids, row.ID.String())
			}
			return map[string]any{"artifact_ids": ids}, 0, "", "", nil
		})
	if err != nil {
		return nil, err
	}
	all, err := rc.r.Store.RunArtifacts(ctx, artifactRun)
	if err != nil {
		return nil, err
	}
	for _, a := range all {
		if a.ProducedByStep == key {
			arts = append(arts, a)
		}
	}
	return arts, nil
}

// postOutputs puts the delivered files in the chat.
func (rc *runCtx) postOutputs(ctx context.Context, artifactRun uuid.UUID, arts []am.Artifact, summary map[string]any,
	narrative narrativeOut, rerender bool, gate *gateView) error {
	st := rc.r.Store
	run, err := st.RunByID(ctx, artifactRun)
	if err != nil {
		return err
	}
	out := am.OutputsPayload{RunID: artifactRun.String(), Summary: summary, Rendered: rerender,
		Model: rc.modelLine(narrative)}
	if run.Period != nil {
		out.PeriodLabel = am.Text("%s", am.Period(*run.Period))
	}
	if v, ok := summary["period_label"].(string); ok {
		out.PeriodLabel = am.LabelFromPackage(v, out.PeriodLabel)
	}
	out.Credits = run.CreditsSpent
	if gate != nil {
		what, next := gate.words()
		out.Gate = &am.OutputsGate{Passed: gate.Passed, What: what, Next: next}
		out.Draft = !gate.Passed
	}
	sort.Slice(arts, func(i, j int) bool { return formatOrder(arts[i].Format) < formatOrder(arts[j].Format) })
	for _, a := range arts {
		out.Artifacts = append(out.Artifacts, am.OutputArtifact{ID: a.ID.String(), Name: a.Name, Format: a.Format, Size: a.Size,
			Version: a.Version, Quality: a.Validation})
	}
	rid := artifactRun
	if _, _, err := st.AddItem(ctx, st.Pool, run.InstallID, run.TenantID, run.UserID, &rid, out, nil); err != nil {
		return err
	}
	if out.Draft {
		// the gate did not pass: the message that follows says what the round came to, in the gate's words
		return nil
	}
	text := am.Text("เสร็จแล้วครับ")
	if gate != nil && !out.Gate.What.Empty() {
		text = am.Text("เสร็จแล้วครับ %s", am.Words(out.Gate.What))
	}
	switch {
	case rc.pkg.Manifest.Narrative == "rules":
		text = am.Text("%s %s", am.Words(text), am.Words(documentsNote(summary)))
	case rerender:
		text = am.Text("สร้างไฟล์เพิ่มจากตัวเลขชุดเดิมแล้วครับ ไม่ได้คำนวณใหม่ ตัวเลขและที่มาตรงกับรายงานเดิมทุกตัว")
	case narrative.Model == "":
		text = am.Text("%s คำอธิบายในรอบนี้เขียนจากกฎ เพราะไม่มีโมเดลที่ตอบได้ ตัวเลขทุกตัวยังมาจากสคริปต์เหมือนเดิม", am.Words(text))
	case narrative.SelfHosted:
		text = am.Text("%s ตัวเลขทุกตัวมาจากสคริปต์ คำอธิบายเขียนด้วย %s ที่รันในองค์กร", am.Words(text), am.ModelName(narrative.Model))
	default:
		text = am.Text("%s ตัวเลขทุกตัวมาจากสคริปต์ คำอธิบายเขียนด้วย %s (ผู้ให้บริการภายนอก เห็นเฉพาะตารางที่คำนวณแล้ว)",
			am.Words(text), am.ModelName(narrative.Model))
	}
	return st.PostText(ctx, run, text)
}

// modelLine is what the outputs card always says about models (AM4): which
// model saw the documents (or that none did), what the person chose, and —
// plainly — when another model was used instead.
func (rc *runCtx) modelLine(narrative narrativeOut) *am.OutputsModel {
	line := &am.OutputsModel{Model: narrative.Model, SelfHosted: narrative.SelfHosted, Choice: rc.choice(),
		Uses: am.ModelUse(rc.pkg.Manifest)}
	flag := func(v bool) *bool { return &v }
	switch line.Uses {
	case "vision":
		used, notChosen := rc.visionModel()
		line.ChosenModel = rc.chosenModel()
		line.VisionModel = used
		line.VisionSelfHosted = flag(used != "" && rc.r.Model.IsSelfHosted(used))
		line.NotChosen = flag(notChosen)
	case "text":
		chosen := rc.chosenModel()
		line.ChosenModel = chosen
		line.NotChosen = flag(narrative.Model != "" && chosen != "" && !sameModel(narrative.Model, chosen))
		line.WrittenByRules = flag(narrative.Model == "")
	}
	return line
}

// documentsNote says where an extraction agent's numbers came from and which
// model saw the documents (AM2: the report names it and whether it runs in
// the organisation).
func documentsNote(summary map[string]any) am.PersonText {
	text := am.Text("ตัวเลขทุกตัวอ่านจากเอกสารด้วยสคริปต์ หรือเป็นค่าที่คุณพิมพ์/ยืนยัน โมเดลไม่ได้เป็นผู้กำหนดตัวเลขใด")
	models, _ := summary["models"].([]any)
	for _, m := range models {
		mm, _ := m.(map[string]any)
		name, _ := mm["model"].(string)
		if name == "" {
			continue
		}
		where := am.Text("ผู้ให้บริการภายนอก")
		if self, _ := mm["self_hosted"].(bool); self {
			where = am.Text("รันในองค์กร")
		}
		text = am.Text("%s · โมเดลที่เห็นเอกสาร: %s (%s) อ่านซ้ำ %s ฉบับที่เป็นสแกนหรือรูปถ่าย เพื่อเทียบกับ OCR เท่านั้น",
			am.Words(text), am.ModelName(name), am.Words(where), am.Count(int(numberOf(mm["documents"]))))
	}
	return text
}

func formatOrder(f string) int {
	switch f {
	case "docx":
		return 0
	case "pdf":
		return 1
	case "pptx":
		return 2
	case "xlsx":
		return 3
	}
	return 4
}

// startNextRound starts the round the user asked to keep files for.
func (rc *runCtx) startNextRound(ctx context.Context) {
	st := rc.r.Store
	files, err := st.RunFiles(ctx, rc.run.ID, "next_round")
	if err != nil || len(files) == 0 {
		return
	}
	// AM4: the next round carries the install's settings and nothing of this
	// one — not even its typed instruction: "ปิดงบเดือนกันยายน" typed for this
	// round would close the next round's files as September too.
	nr, _, err := st.CreateRun(ctx, st.Pool, am.Run{InstallID: rc.run.InstallID, TenantID: rc.run.TenantID, UserID: rc.run.UserID,
		Kind: am.KindRun, DefinitionVersion: rc.run.DefinitionVersion, Checksum: rc.run.Checksum,
		EngineChecksum: rc.run.EngineChecksum, BindingVersion: rc.run.BindingVersion, Formats: rc.run.Formats,
		ThreadID: rc.run.ThreadID, ModelChoice: rc.run.ModelChoice}, "")
	if err != nil {
		rc.log.Error("next round failed", "err", err)
		return
	}
	ids := []uuid.UUID{}
	for _, f := range files {
		ids = append(ids, f.ID)
	}
	if _, err := st.Pool.Exec(ctx, `UPDATE agent_market.agent_files SET run_id = $2, status = 'active' WHERE id = ANY($1)`, ids, nr.ID); err != nil {
		return
	}
	rid := nr.ID
	_, _, _ = st.AddItem(ctx, st.Pool, nr.InstallID, nr.TenantID, nr.UserID, &rid, am.QueuedProgress(nr), nil)
	_ = st.PostText(ctx, nr, am.Text("เริ่มรอบใหม่ด้วยไฟล์ที่เก็บไว้ครับ"))
}

// checkLine is one failed check as the quality gate's error and the person's
// message carry it: the file's format, the check's label, and its detail
// when the detail is something a person can read. (What a person is shown of
// the whole line is decided once more where it is said: am.FailureFor.)
func checkLine(format, label, detail string) string {
	line := formatThai(format) + ": " + label
	if d := am.ShownToPerson(am.TrimText(detail, 120)); d != "" {
		line += " (" + d + ")"
	}
	return line
}

func formatThai(format string) string {
	switch format {
	case "docx":
		return "รายงาน"
	case "pptx":
		return "สไลด์"
	case "xlsx":
		return "ตาราง"
	case "pdf":
		return "PDF"
	case "template":
		return "แบบฟอร์ม"
	}
	return "ไฟล์"
}

// gateLines reads the failed checks back out of a quality gate's error.
func gateLines(err error) []string {
	s := strings.TrimPrefix(err.Error(), "engine step failed: ")
	i := strings.Index(s, "quality gate: ")
	if i < 0 {
		return nil
	}
	var out []string
	for _, l := range strings.Split(s[i+len("quality gate: "):], "; ") {
		if l = strings.TrimSpace(l); l != "" {
			out = append(out, l)
		}
	}
	return out
}
