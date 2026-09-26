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
	key := am.StepKey(rc.run.ID, "narrative", computeKey, rc.pkg.Checksum, rc.r.Model.Primary, rc.r.Model.Fallback)
	out, err := rc.step(ctx, "narrative", "ai", key, map[string]any{"models": []string{rc.r.Model.Primary, rc.r.Model.Fallback}}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			cv, _, err := rc.readComputed(ctx, computed)
			if err != nil {
				return nil, 0, "", "", err
			}
			user := narrativePrompt(cv)
			var parsed map[string]any
			c, tried, err := rc.r.Model.Complete(ctx, rc.pkg.Instructions, user, func(s string) error {
				var v map[string]any
				if err := json.Unmarshal([]byte(extractJSON(s)), &v); err != nil {
					return fmt.Errorf("ไม่ใช่ JSON")
				}
				sum, _ := v["summary"].(string)
				if strings.TrimSpace(sum) == "" {
					return fmt.Errorf("ไม่มี summary")
				}
				var all []string
				all = append(all, sum)
				for _, k := range []string{"movements", "watch", "next_steps"} {
					arr, _ := v[k].([]any)
					if len(arr) > 6 {
						v[k] = arr[:6]
					}
					for _, x := range arr {
						s, _ := x.(string)
						all = append(all, s)
					}
				}
				if err := GuardNumbers(strings.Join(all, "\n"), cv.Quotable, cv.QuotableIDs); err != nil {
					return err
				}
				parsed = v
				return nil
			})
			if err != nil {
				// No model answered: the rules write it. The report says so.
				var fb map[string]any
				_ = json.Unmarshal(cv.Fallback, &fb)
				fb["written_by"] = "rules"
				nb, _ := json.Marshal(fb)
				// Refused answers were still paid for: the budget counts them.
				return narrativeOut{Narrative: nb, Tried: tried, Tokens: c.Tokens, CostUSD: c.CostUSD},
					c.CostUSD * rc.r.CreditsPerUSD, "metrics.py:fallback_narrative", "", nil
			}
			parsed["written_by"] = "model"
			parsed["model"] = c.Model
			nb, _ := json.Marshal(parsed)
			self := rc.r.Model.SelfHosted[c.Model]
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
	names := map[string]string{}
	versions := map[string]int{}
	for _, f := range formats {
		v, err := st.NextVersion(ctx, artifactRun, f)
		if err != nil {
			return renderOut{}, "", err
		}
		versions[f] = v
		names[f] = am.ArtifactName(rc.install.Slug, period, f, v)
	}
	parentRun := rc.run
	if artifactRun != rc.run.ID {
		if p, err := st.RunByID(ctx, artifactRun); err == nil {
			parentRun = p
		}
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
		names["template"] = am.TemplateArtifactName(rc.install.Slug, period, tpl.Kind, v)
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
				"run":          map[string]any{"id": artifactRun.String(), "date": rc.today(), "agent_version": rc.pkg.Version},
				"model_info":   map[string]any{"model": narrative.Model, "self_hosted": narrative.SelfHosted}}
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
			res, err := rc.r.Exec.Run(ctx, req, inputs)
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
							failed = append(failed, meta.Format+": "+c.Label+" ("+trim(c.Detail, 120)+")")
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
			return renderOut{}, key, &qualityErr{err}
		}
		return renderOut{}, key, err
	}
	var ro renderOut
	return ro, key, json.Unmarshal(out, &ro)
}

type qualityErr struct{ error }

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
	narrative narrativeOut, rerender bool) error {
	st := rc.r.Store
	run, err := st.RunByID(ctx, artifactRun)
	if err != nil {
		return err
	}
	out := am.OutputsPayload{RunID: artifactRun.String(), Summary: summary, Rendered: rerender,
		Model: map[string]any{"model": narrative.Model, "self_hosted": narrative.SelfHosted}}
	if run.Period != nil {
		out.PeriodLabel = *run.Period
	}
	if v, ok := summary["period_label"].(string); ok {
		out.PeriodLabel = v
	}
	out.Credits = run.CreditsSpent
	sort.Slice(arts, func(i, j int) bool { return formatOrder(arts[i].Format) < formatOrder(arts[j].Format) })
	for _, a := range arts {
		out.Artifacts = append(out.Artifacts, am.OutputArtifact{ID: a.ID.String(), Name: a.Name, Format: a.Format, Size: a.Size,
			Version: a.Version, Quality: a.Validation})
	}
	rid := artifactRun
	if _, _, err := st.AddItem(ctx, st.Pool, run.InstallID, run.TenantID, run.UserID, &rid, "outputs", out, nil); err != nil {
		return err
	}
	text := "เสร็จแล้วครับ"
	if rc.pkg.Manifest.Narrative == "rules" {
		text += " " + documentsNote(summary)
		return st.PostText(ctx, run, text, nil)
	}
	if rerender {
		text = "สร้างไฟล์เพิ่มจากตัวเลขชุดเดิมแล้วครับ ไม่ได้คำนวณใหม่ ตัวเลขและที่มาตรงกับรายงานเดิมทุกตัว"
	} else if narrative.Model == "" {
		text += " คำอธิบายในรอบนี้เขียนจากกฎ เพราะไม่มีโมเดลที่ตอบได้ ตัวเลขทุกตัวยังมาจากสคริปต์เหมือนเดิม"
	} else if narrative.SelfHosted {
		text += " ตัวเลขทุกตัวมาจากสคริปต์ คำอธิบายเขียนด้วย " + narrative.Model + " ที่รันในองค์กร"
	} else {
		text += " ตัวเลขทุกตัวมาจากสคริปต์ คำอธิบายเขียนด้วย " + narrative.Model + " (ผู้ให้บริการภายนอก เห็นเฉพาะตารางที่คำนวณแล้ว)"
	}
	return st.PostText(ctx, run, text, nil)
}

// documentsNote says where an extraction agent's numbers came from and which
// model saw the documents (AM2: the report names it and whether it runs in
// the organisation).
func documentsNote(summary map[string]any) string {
	text := "ตัวเลขทุกตัวอ่านจากเอกสารด้วยสคริปต์ หรือเป็นค่าที่คุณพิมพ์/ยืนยัน โมเดลไม่ได้เป็นผู้กำหนดตัวเลขใด"
	models, _ := summary["models"].([]any)
	for _, m := range models {
		mm, _ := m.(map[string]any)
		name, _ := mm["model"].(string)
		if name == "" {
			continue
		}
		where := "ผู้ให้บริการภายนอก"
		if self, _ := mm["self_hosted"].(bool); self {
			where = "รันในองค์กร"
		}
		text += fmt.Sprintf(" · โมเดลที่เห็นเอกสาร: %s (%s) อ่านซ้ำ %d ฉบับที่เป็นสแกนหรือรูปถ่าย เพื่อเทียบกับ OCR เท่านั้น",
			name, where, int(numberOf(mm["documents"])))
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
	nr, _, err := st.CreateRun(ctx, st.Pool, am.Run{InstallID: rc.run.InstallID, TenantID: rc.run.TenantID, UserID: rc.run.UserID,
		Kind: am.KindRun, Message: rc.run.Message, DefinitionVersion: rc.run.DefinitionVersion, Checksum: rc.run.Checksum,
		EngineChecksum: rc.run.EngineChecksum, BindingVersion: rc.run.BindingVersion, Formats: rc.run.Formats}, "")
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
	_, _, _ = st.AddItem(ctx, st.Pool, nr.InstallID, nr.TenantID, nr.UserID, &rid, "progress",
		am.Progress{RunID: nr.ID.String(), Kind: am.KindRun, Status: am.RunQueued}, nil)
	_ = st.PostText(ctx, nr, "เริ่มรอบใหม่ด้วยไฟล์ที่เก็บไว้ครับ", nil)
}
