package runner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
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
	// AM8: the figures a sentence may name, by key — {"operating_expenses_var_budget_pct": {text, cite,
	// label}}. A narrative names them ({key}); the report puts each figure and its own citation there.
	Figures     map[string]figureView `json:"figures"`
	Fallback    json.RawMessage       `json:"fallback_narrative"`
	Source      json.RawMessage       `json:"source"`
	Excluded    json.RawMessage       `json:"excluded"`
	Citations   []json.RawMessage     `json:"citations"`
	Materiality string                `json:"materiality"`
	Script      string                `json:"script"`
	// AM21 §6.1: the layout catalogue's limits (characters, counts), told to the model before it writes
	WritingLimits map[string]int `json:"writing_limits"`
}

type figureView struct {
	Text  string `json:"text"`
	Cite  string `json:"cite"`
	Label string `json:"label"`
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
			// AM11: the commentary is printed in the report, so it is written in the report's language
			// (docLang: the package's, not the run's) — the prompt says which, and the number guard
			// reads the answer as a text of that language
			l := rc.docLang(ctx)
			user := narrativePrompt(l, cv)
			var parsed map[string]any
			c, tried, err := rc.r.Model.CompleteWith(ctx, models, rc.pkg.Instructions, user, func(s string) error {
				v, err := checkNarrative(l, s, cv)
				if err != nil {
					return err
				}
				parsed = v
				return nil
			})
			if errors.Is(err, ErrModelUnreachable) && ctx.Err() == nil {
				// AM15: no model that may see the table could be reached. The rules stand in for a model
				// whose answer could not be used — not for one that is away: the run waits for it.
				return nil, c.CostUSD * rc.r.CreditsPerUSD, "", "", err
			}
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
func checkNarrative(l am.Lang, s string, cv computedView) (map[string]any, error) {
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
	if len(cv.Figures) > 0 {
		if err := guardFigures(l, strings.Join(all, "\n"), cv); err != nil {
			return nil, err
		}
		return v, nil
	}
	if err := GuardNumbersIn(l, strings.Join(all, "\n"), cv.Quotable, cv.QuotableIDs); err != nil {
		return nil, err
	}
	return v, nil
}

// figureRe is a figure named by its key in a sentence: {revenue}, {revenue_var_prior_pct:abs}.
var figureRe = regexp.MustCompile(`\{([A-Za-z0-9_][A-Za-z0-9_.\-/]*)(?::(abs))?\}`)

// guardFigures (AM8): a narrative names every figure by its key, and only keys of the computed table;
// it types no figure itself. The citation a reader follows is then the figure's own — never one found
// afterwards by matching printed text (two figures printing "10.5%" took each other's citation).
// What is left once the keys are taken out may hold small counts, years and the table's account
// codes, nothing else (GuardNumbers with no figures allowed).
func guardFigures(l am.Lang, text string, cv computedView) error {
	var unknown []string
	for _, m := range figureRe.FindAllStringSubmatch(text, -1) {
		if _, ok := cv.Figures[m[1]]; !ok {
			unknown = append(unknown, m[0])
		}
	}
	if len(unknown) > 0 {
		return fmt.Errorf("อ้างตัวเลขที่ไม่มีในตาราง: %s ใช้ได้เฉพาะชื่อในรายการตัวเลขที่อ้างได้", strings.Join(unknown, ", "))
	}
	rest := figureRe.ReplaceAllString(text, " ")
	// AM11b: a brace left over is a figure's name that is not whole. "…ค่าใช้จ่ายในการขายและบริหารที่:abs}" is
	// what was left of "…ที่สูงกว่างบ {operating_expenses_var_budget_pct:abs}" once the platform's echo guard had
	// taken out the 44 characters that repeated an example printed in the package's instructions (4 of 27
	// commentaries on live). The report printed the sentence as it was; it is now no answer, for every
	// installed version — the model is told, and a second cut ends in the rules' own commentary.
	if strings.ContainsAny(rest, "{}") {
		return fmt.Errorf("มีชื่อตัวเลขที่เขียนไม่ครบ (วงเล็บปีกกาไม่ครบคู่) — เขียนชื่อของตัวเลขให้ครบในรูป {ชื่อ} หรือ {ชื่อ:abs} และเขียนประโยคด้วยคำของคุณเอง อย่าคัดลอกประโยคตัวอย่างจากคำสั่ง")
	}
	if err := GuardNumbersIn(l, rest, nil, cv.QuotableIDs); err != nil {
		return fmt.Errorf("%s — อย่าพิมพ์ตัวเลขเอง ให้เขียนชื่อของตัวเลขในวงเล็บปีกกา เช่น {revenue}", err.Error())
	}
	return nil
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

// narrativePrompt is what a model is shown of a run's figures, and asked. It is addressed to a model and
// written in Thai; the one thing in it that is the run's is the language the answer is to be written in
// (modellang.go) — l. A Thai run's prompt is exactly what it was.
func narrativePrompt(l am.Lang, cv computedView) string {
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
	if len(cv.Figures) > 0 {
		// AM8: the model names a figure by its key; the report prints it with its own citation
		b.WriteString("\nตัวเลขที่อ้างได้ — เขียนเป็น {ชื่อ} ห้ามพิมพ์ตัวเลขเอง (ชื่อ = ค่าที่จะพิมพ์ · ตัวเลขนี้คืออะไร):\n")
		keys := make([]string, 0, len(cv.Figures))
		for k := range cv.Figures {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			f := cv.Figures[k]
			fmt.Fprintf(&b, "- {%s} = %s · %s\n", k, f.Text, f.Label)
		}
		b.WriteString(writingLimitsLine(cv))
		b.WriteString("\nเขียนคำอธิบายตามรูปแบบ JSON ใน instructions " + languageName(l) + " ทุกตัวเลขเขียนเป็น {ชื่อ} จากรายการด้านบน " +
			"(ถ้าบอกทิศทางเป็นคำแล้ว เช่น " + directionWord(l) + " ใช้ {ชื่อ:abs} เพื่อพิมพ์โดยไม่มีเครื่องหมาย)" + answerIn(l))
		return b.String()
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
	b.WriteString("\nเขียนคำอธิบายตามรูปแบบ JSON ใน instructions " + languageName(l) + " ใช้ตัวเลขจากรายการด้านบนเท่านั้น" + answerIn(l))
	return b.String()
}

// writingLimitsLine (AM21 §6.1) tells the model, before it writes, how much the layouts take: the
// summary's paragraph, a point of a list, how many points. A model that writes to fit is not refused
// afterwards; one that is not told writes freely and ends up truncated or refused. An engine from
// before this card sends no limits, and the prompt is as it was.
func writingLimitsLine(cv computedView) string {
	lim := cv.WritingLimits
	if lim["summary_chars"] == 0 || lim["bullet_chars"] == 0 || lim["bullets_max"] == 0 {
		return ""
	}
	return fmt.Sprintf("\nความยาวที่เอกสารรับได้ (นับตัวอักษร): สรุปไม่เกิน %d · แต่ละข้อในรายการไม่เกิน %d ตัวอักษร และไม่เกิน %d ข้อต่อหัวข้อ · เขียนให้พอดี ส่วนที่ยาวกว่านี้จะถูกปฏิเสธ\n",
		lim["summary_chars"], lim["bullet_chars"], lim["bullets_max"])
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

// refusedFormat (AM21) is a file the renderer did not write: its content fits no layout of the
// catalogue, and the check says which section.
type refusedFormat struct {
	Format string `json:"format"`
	Name   string `json:"name"`
	Check  struct {
		Label  string `json:"label"`
		Detail string `json:"detail"`
	} `json:"check"`
}

// refusedLines are the gate's lines for the files the renderer refused: the run fails naming the section.
func refusedLines(l am.Lang, refused []refusedFormat) []string {
	var out []string
	for _, r := range refused {
		out = append(out, checkLine(l, r.Format, r.Check.Label, r.Check.Detail))
	}
	return out
}

type renderOut struct {
	Artifacts []renderedArt   `json:"artifacts"`
	Theme     json.RawMessage `json:"theme"`
	Passed    bool            `json:"passed"`
	// Asked (AM11b): what the renderer did with the directives it was given
	Asked renderAsked `json:"asked"`
	// form (AM10): the round's own form this render filled, if any
	form *am.RoundForm
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
	pyFormats := []string{} // never nil: the engine takes [] as "no file", null as its defaults
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
	// AM10: a form dropped into this round comes first, for this round only.
	tpl, tplFile, roundForm, err := rc.roundFormInput(ctx, artifactRun)
	if err != nil {
		return renderOut{}, "", err
	}
	if tpl == nil && !rc.formOnly {
		tpl, tplFile, err = rc.templateInput(ctx)
		if err != nil {
			return renderOut{}, "", err
		}
	}
	if rc.formOnly && tpl == nil {
		return renderOut{}, "", fmt.Errorf("the round has no form ready to fill")
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
	if len(rc.instr) > 0 {
		// AM11b: documents made another way are another render (a round nobody asked anything of keeps its key)
		keyParts = append(keyParts, map[string]any{"instructions": rc.instr})
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
			if rc.pkg.Manifest.UsesWeb() {
				// AM12: the pictures of what each page showed, for the documents that print them
				if data, err := os.ReadFile(cpath); err == nil {
					inputs = append(inputs, rc.evidenceInputs(ctx, data)...)
				}
			}
			req := map[string]any{"op": "render", "package": rc.pkg.PyPackage, "computed": "computed.json", "formats": pyFormats,
				"names": names, "theme": theme, "narrative": json.RawMessage(narrative.Narrative), "assumptions": assumptions,
				"presentation": pres,
				"run": map[string]any{"id": artifactRun.String(), "date": rc.today(), "agent_version": rc.pkg.Version,
					"preparer": rc.preparer(ctx, parentRun), "organisation": rc.organisation(ctx), "kind": parentRun.Kind},
				"model_info": map[string]any{"model": narrative.Model, "self_hosted": narrative.SelfHosted}}
			if len(rc.instr) > 0 {
				req["instructions"] = map[string]any{"apply": rc.instr}
			}
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
				Asked     renderAsked       `json:"asked"`
				// AM21: a format whose content fits no layout of the catalogue — refused, no file written,
				// the check naming the section
				Refused []refusedFormat `json:"refused"`
			}
			if err := json.Unmarshal(mustJSON(res.Result), &r); err != nil {
				return nil, 0, "", "", err
			}
			ro := renderOut{Theme: r.Theme, Passed: r.Passed, Asked: r.Asked}
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
					PrintRows     []printRow      `json:"print_rows"`
				}
				if err := json.Unmarshal(a, &meta); err != nil {
					return nil, 0, "", "", err
				}
				if rc.formOnly && meta.Kind != "template" {
					// AM10: a fill of the round's form delivers the form alone. An engine released before
					// it read "no formats" as its defaults and makes its own files again — an install
					// pinned to such a version must not get a second register or report for it.
					continue
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
				// AM8: the customer's own form that did not pass is delivered beside the agent's files as
				// what it is — not passed, with every slot it lacks named on the download card — and the
				// round does not fail for it (the agent's own files passed their gate). A form filled
				// half-way was reported "passed" before; now it is never reported so.
				if !q.Passed && meta.Kind != "template" {
					for _, c := range q.Checks {
						if !c.OK {
							failed = append(failed, checkLine(am.LangOf(ctx), meta.Format, c.Label, c.Detail))
						}
					}
					continue
				}
				path, ok := res.Artifacts[meta.Name]
				if !ok {
					return nil, 0, "", "", fmt.Errorf("render produced no %s", meta.Name)
				}
				if meta.Format == "xlsx" && meta.Kind != "template" {
					// AM9: printed and looked at — every row's identity beside its amounts on one page
					c, err := rc.printProof(ctx, meta.Name, path, meta.PrintRows)
					if err != nil {
						return nil, 0, "", "", err
					}
					if c != nil {
						meta.Quality = withCheck(am.LangOf(ctx), meta.Quality, *c)
						if !c.OK {
							failed = append(failed, checkLine(am.LangOf(ctx), meta.Format, c.Label.In(am.LangOf(ctx)), c.Detail.In(am.LangOf(ctx))))
							continue
						}
					}
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
			failed = append(failed, refusedLines(am.LangOf(ctx), r.Refused)...)
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
	if err := json.Unmarshal(out, &ro); err != nil {
		return ro, key, err
	}
	ro.form = roundForm
	return ro, key, nil
}

// moreFormats are the formats a person may ask this round for afterwards: the manifest's outputs, PDF
// only with a converter — the same rule the formats endpoint applies (AM8: the card offered .docx and
// .pptx to agents that make only .xlsx, and never PDF to the one that makes it).
func (rc *runCtx) moreFormats() []string {
	var out []string
	for _, o := range rc.pkg.Manifest.Outputs {
		if o.Format == "pdf" && (rc.r.Converter == nil || !rc.r.Converter.Available()) {
			continue
		}
		if o.Format != "" && !contains(out, o.Format) {
			out = append(out, o.Format)
		}
	}
	return out
}

// unfilledTemplateSlots counts the slots a delivered template lacks (its quality's "unfilled").
func unfilledTemplateSlots(arts []am.Artifact) int {
	n := 0
	for _, a := range arts {
		if a.Format != "template" {
			continue
		}
		var q struct {
			Passed   bool              `json:"passed"`
			Unfilled []json.RawMessage `json:"unfilled"`
		}
		if json.Unmarshal(a.Validation, &q) == nil && !q.Passed {
			n += len(q.Unfilled)
			if len(q.Unfilled) == 0 {
				n++
			}
		}
	}
	return n
}

// preparer is who prepared the round, as a document names them (AM5: the
// reconciliation statement says who prepared it and when). AM8: a real value
// or nothing — their name, else their e-mail; a sample says it is one. A
// working paper with no preparer says on its face that it is a draft; it
// never prints a stand-in.
func (rc *runCtx) preparer(ctx context.Context, run am.Run) string {
	if run.Kind == am.KindSelfTest {
		return am.T(rc.docLang(ctx), SamplePreparer) // printed on the sample's working paper: the paper's language
	}
	if n := rc.r.Store.DisplayName(ctx, rc.install.OrgID, run.UserID, ""); n != "" {
		return n
	}
	return rc.r.Store.UserEmail(ctx, run.UserID)
}

// SamplePreparer is what a self-test's working paper names as its preparer:
// nobody prepared it, and it says so.
const SamplePreparer = "ไฟล์ตัวอย่างของ agent (ไม่มีผู้จัดทำ)"

// organisation is the organisation's name, which a working paper's header
// prints when the install has no company of its own (AM8).
func (rc *runCtx) organisation(ctx context.Context) string {
	o, err := rc.r.Store.OrgSettingsFor(ctx, orgOrDefault(rc.install.OrgID))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(o.Name)
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
		Model: rc.modelLine(ctx, run, narrative), MoreFormats: rc.moreFormats()}
	if run.Period != nil {
		out.PeriodLabel = am.Text("%s", am.Period(*run.Period))
	}
	// the package's own name for the period is used when it says something else than the run's month
	// (a quarter, a year). When it is that month as a Thai reader says it, it is formatting, and the
	// platform's text already says it in both languages ("กันยายน 2569" / "September 2026")
	if v, ok := summary["period_label"].(string); ok && (run.Period == nil || strings.TrimSpace(v) != am.PeriodLabel(*run.Period)) {
		out.PeriodLabel = am.LabelFromPackage(v, out.PeriodLabel)
	}
	// AM11b: what the run was charged is not put on the files card — a person using an agent sees no
	// credits; the admin's Spend page has them.
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
	if gate != nil && !out.Gate.What.EmptyIn(am.LangOf(ctx)) {
		text = am.Text("เสร็จแล้วครับ %s", am.Words(out.Gate.What))
	}
	if n := unfilledTemplateSlots(arts); n > 0 {
		// AM8: said in the round's own message, not only in a file's details
		text = am.Text("%s · แบบฟอร์มของคุณยังกรอกไม่ครบ %s ช่อง ดูชื่อช่องในการ์ดไฟล์ ยังไม่ควรส่งต่อ",
			am.Words(text), am.Count(n))
	}
	switch {
	case rc.pkg.Manifest.UsesWeb():
		// AM12: what a model did in a round on the web, and what it did not — in the words that fit what
		// the round came to (webwords.go)
		text = webClosing(text, summary)
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
func (rc *runCtx) modelLine(ctx context.Context, run am.Run, narrative narrativeOut) *am.OutputsModel {
	line := &am.OutputsModel{Model: narrative.Model, SelfHosted: narrative.SelfHosted, Choice: rc.choice(),
		Uses: am.ModelUse(rc.pkg.Manifest), Masking: rc.roundMasking(ctx, run), SentOutUnmasked: rc.roundSentOut(ctx, run)}
	flag := func(v bool) *bool { return &v }
	if rc.pkg.Manifest.UsesWeb() {
		// AM12: a round on the web has no narrative for a model to write; what a model did is read the
		// request and the pages. The card names those models — "no model saw the source files" is not
		// true of this agent, and the card must never say it.
		line.Uses, line.ChosenModel, line.Web = "web", rc.chosenModel(), rc.webModels(ctx, run)
		chosen := false
		for _, w := range line.Web {
			chosen = chosen || sameModel(w.Model, line.ChosenModel)
		}
		line.NotChosen = flag(len(line.Web) > 0 && line.ChosenModel != "" && !chosen)
		return line
	}
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
// AM11: the line is a plain text kept on the run and said in its thread, so
// it is built in l — the run's language.
func checkLine(l am.Lang, format, label, detail string) string {
	line := formatWord(l, format) + ": " + label
	if d := am.ShownIn(l, am.TrimText(detail, 120)); d != "" {
		line += " (" + d + ")"
	}
	return line
}

// formatWord names a kind of file in front of a check of it.
func formatWord(l am.Lang, format string) string {
	switch format {
	case "docx":
		return am.T(l, "รายงาน")
	case "pptx":
		return am.T(l, "สไลด์")
	case "xlsx":
		return am.T(l, "ตาราง")
	case "pdf":
		return "PDF"
	case "template":
		return am.T(l, "แบบฟอร์ม")
	}
	return am.T(l, "ไฟล์")
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
