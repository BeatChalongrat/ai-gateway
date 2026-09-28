package agentmarket

import (
	"context"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"sort"
	"strings"
	"time"
)

// ── Which model reads the documents (AM4) ────────────────────────────────
//
// "อัตโนมัติ (แนะนำ)" is the default and most people never move off it: it
// resolves from the capability the agent's manifest declares (model.vision
// → the runner's vision model; model.text → its text model). Below it sits a
// short list the organisation's admin curates — named by outcome, not by
// model ("เร็วและประหยัด", "แม่นที่สุด") — each row saying what an engineer and
// an accountant both need: the real model, inside or outside the
// organisation, a rough cost and time per run.
//
// A model outside the organisation is offered only when the organisation
// lets documents leave (org_settings.documents_may_leave, off by default);
// otherwise it is shown disabled with the reason, never quietly selectable.
// The choice never changes a check and never becomes the source of a
// number: it buys a better reading of a bad scan and better prose, nothing
// else.

// ModelAuto is the default choice.
const ModelAuto = "auto"

// Outcomes an admin can give a curated model.
const (
	OutcomeFast     = "fast"
	OutcomeAccurate = "accurate"
)

// ModelEntry is one model the organisation's admin puts on the list.
type ModelEntry struct {
	Model   string `json:"model"`
	Outcome string `json:"outcome"`
}

// CatalogModel is one model of the gateway's catalogue as the runner sees it
// (its heartbeat capability "platform.models").
type CatalogModel struct {
	ID         string  `json:"id"`
	Vision     bool    `json:"vision"`
	SelfHosted bool    `json:"self_hosted"`
	InPrice    float64 `json:"input_price_per_1m"`
	OutPrice   float64 `json:"output_price_per_1m"`
}

// RunnerModels is what the runner publishes about models.
type RunnerModels struct {
	Primary    string         `json:"primary"`
	Fallback   string         `json:"fallback"`
	Vision     string         `json:"vision"`
	SelfHosted map[string]bool `json:"self_hosted"`
	Catalog    []CatalogModel `json:"catalog"`
}

// ModelOption is one row of the picker.
type ModelOption struct {
	ID         string   `json:"id"`      // "auto" or the model id
	Outcome    string   `json:"outcome"` // auto | fast | accurate
	Label      string   `json:"label"`
	Model      string   `json:"model"` // the real model (auto: what it resolves to now)
	SelfHosted bool     `json:"self_hosted"`
	CostTHB    *float64 `json:"cost_thb"`
	Seconds    *float64 `json:"seconds"`
	Disabled   bool     `json:"disabled"`
	Reason     string   `json:"reason,omitempty"`
}

// ModelPicker is what the agent's settings page and the composer show.
type ModelPicker struct {
	Uses              string        `json:"uses"` // vision | text | none
	Reads             string        `json:"reads"`
	Choice            string        `json:"choice"`
	Options           []ModelOption `json:"options"`
	DocumentsMayLeave bool          `json:"documents_may_leave"`
	Note              string        `json:"note"`
}

var outcomeLabel = map[string]string{ModelAuto: "อัตโนมัติ (แนะนำ)", OutcomeFast: "เร็วและประหยัด", OutcomeAccurate: "แม่นที่สุด"}

// ModelUse is what a model does for this agent: read scans (vision), write
// the report's words (text), or nothing.
func ModelUse(m Manifest) string {
	caps := append(append([]string{}, m.Capabilities.Required...), m.Capabilities.Optional...)
	has := func(c string) bool {
		for _, x := range caps {
			if x == c {
				return true
			}
		}
		return false
	}
	switch {
	case has("model.vision"):
		return "vision"
	case has("model.text") && m.Narrative != "rules":
		return "text"
	}
	return "none"
}

// RunnerModelsFrom reads the runner's model facts from its heartbeat capabilities.
func RunnerModelsFrom(caps json.RawMessage) RunnerModels {
	var available map[string]Binding
	_ = json.Unmarshal(caps, &available)
	var rm RunnerModels
	if b, ok := available["platform.models"]; ok {
		_ = json.Unmarshal(b.Detail, &rm)
	}
	if b, ok := available["model.text"]; ok && rm.Primary == "" {
		_ = json.Unmarshal(b.Detail, &rm)
	}
	if rm.SelfHosted == nil {
		rm.SelfHosted = map[string]bool{}
	}
	for _, c := range rm.Catalog {
		if c.SelfHosted {
			rm.SelfHosted[c.ID] = true
		}
	}
	return rm
}

func (rm RunnerModels) find(id string) (CatalogModel, bool) {
	for _, c := range rm.Catalog {
		if c.ID == id {
			return c, true
		}
	}
	return CatalogModel{}, false
}

// canRead: the model can do this agent's job (a vision agent needs a model that reads images).
func (rm RunnerModels) canRead(use, id string) bool {
	if use != "vision" {
		return true
	}
	if id == rm.Vision {
		return true
	}
	c, ok := rm.find(id)
	return ok && c.Vision
}

// autoModel is what "อัตโนมัติ" resolves to for this use right now.
func (rm RunnerModels) autoModel(use string) string {
	if use == "vision" {
		return rm.Vision
	}
	return rm.Primary
}

// curated is the organisation's list, or — until an admin curates one — the
// runner's own models: its main model as "แม่นที่สุด" and, for writing, its
// second model as "เร็วและประหยัด".
func curated(o OrgSettings, rm RunnerModels, use string) []ModelEntry {
	if len(o.AgentModels) > 0 {
		return o.AgentModels
	}
	out := []ModelEntry{}
	if m := rm.autoModel(use); m != "" {
		out = append(out, ModelEntry{Model: m, Outcome: OutcomeAccurate})
	}
	if use == "text" && rm.Fallback != "" && rm.Fallback != rm.Primary {
		out = append(out, ModelEntry{Model: rm.Fallback, Outcome: OutcomeFast})
	}
	return out
}

// modelCost is a rough cost and time per run for one model: the
// organisation's own succeeded runs of this agent with that model (90 days),
// else the catalog's sample run scaled by the models' prices.
func (s *Store) modelCost(ctx context.Context, org, slug, model string, rm RunnerModels, cfg Config) (*float64, *float64) {
	var credits, secs *float64
	q := `SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY r.credits_spent)::float8,
			percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM r.finished_at - r.started_at) - r.awaiting_ms / 1000.0)::float8
		FROM agent_market.agent_runs r JOIN agent_market.agent_installs i ON i.id = r.install_id
		WHERE i.org_id = $1 AND i.slug = $2 AND i.status <> 'catalog' AND r.kind = 'run' AND r.status = 'succeeded'
			AND r.created_at >= now() - interval '90 days' AND r.started_at IS NOT NULL AND r.finished_at IS NOT NULL`
	args := []any{orgOr(org), slug}
	if model != "" {
		q += ` AND r.model_used = $3`
		args = append(args, model)
	}
	_ = s.Pool.QueryRow(ctx, q, args...).Scan(&credits, &secs)
	if credits == nil {
		if smp, err := s.CatalogSampleFor(ctx, slug); err == nil && smp.Status == RunSucceeded {
			c := smp.Credits
			var used string
			_ = s.Pool.QueryRow(ctx, `SELECT COALESCE(model_used, '') FROM agent_market.agent_runs WHERE id = $1`, smp.RunID).Scan(&used)
			if model != "" && used != "" && used != model {
				a, okA := rm.find(model)
				b, okB := rm.find(used)
				if okA && okB && b.InPrice+b.OutPrice > 0 {
					c = c * (a.InPrice + a.OutPrice) / (b.InPrice + b.OutPrice)
				}
			}
			credits = &c
			if secs == nil {
				secs = smp.Seconds
			}
		}
	}
	if credits == nil || cfg.CreditsPerUSD <= 0 {
		return nil, secs
	}
	thb := math.Round(*credits/cfg.CreditsPerUSD*cfg.THBPerUSD*100) / 100
	return &thb, secs
}

// Picker builds the picker for one install.
func (s *Store) Picker(ctx context.Context, cfg Config, in Install, m Manifest, o OrgSettings, rm RunnerModels) ModelPicker {
	use := ModelUse(m)
	p := ModelPicker{Uses: use, Choice: in.ModelChoice, DocumentsMayLeave: o.DocumentsMayLeave}
	if p.Choice == "" {
		p.Choice = ModelAuto
	}
	switch use {
	case "vision":
		p.Reads = "อ่านเอกสารที่เป็นสแกนหรือรูปถ่าย (PDF ดิจิทัลและ e-Tax อ่านตรงจากไฟล์ ไม่ส่งให้โมเดล)"
	case "text":
		p.Reads = "เขียนคำอธิบายในรายงานจากตัวเลขที่คำนวณเสร็จแล้ว (ตัวเลขทุกตัวคำนวณด้วยสคริปต์ ไม่ได้มาจากโมเดล)"
	default:
		p.Reads = "agent นี้ไม่ส่งเอกสารให้โมเดลใดเลย ทุกค่าอ่านจากไฟล์และคำนวณด้วยสคริปต์"
	}
	p.Note = "โมเดลที่เลือกไม่ได้ข้ามการตรวจใด ๆ และไม่ได้เป็นที่มาของตัวเลข — ช่วยให้อ่านสแกนที่ไม่ชัดและเขียนคำอธิบายได้ดีขึ้นเท่านั้น"
	auto := rm.autoModel(use)
	autoOpt := ModelOption{ID: ModelAuto, Outcome: ModelAuto, Label: outcomeLabel[ModelAuto], Model: auto, SelfHosted: rm.SelfHosted[auto]}
	if use != "none" {
		autoOpt.CostTHB, autoOpt.Seconds = s.modelCost(ctx, in.OrgID, in.Slug, "", rm, cfg)
	}
	p.Options = append(p.Options, autoOpt)
	if use == "none" {
		return p
	}
	seen := map[string]bool{}
	for _, e := range curated(o, rm, use) {
		if e.Model == "" || seen[e.Model] || !rm.canRead(use, e.Model) {
			continue
		}
		seen[e.Model] = true
		opt := ModelOption{ID: e.Model, Outcome: e.Outcome, Label: outcomeLabel[e.Outcome], Model: e.Model,
			SelfHosted: rm.SelfHosted[e.Model]}
		if opt.Label == "" {
			opt.Label = e.Model
		}
		opt.CostTHB, opt.Seconds = s.modelCost(ctx, in.OrgID, in.Slug, e.Model, rm, cfg)
		if !opt.SelfHosted && !o.DocumentsMayLeave {
			opt.Disabled = true
			opt.Reason = "โมเดลนี้อยู่นอกองค์กร และองค์กรยังไม่อนุญาตให้ส่งเอกสารออกนอกองค์กร — ผู้ดูแลเปิดได้ในหน้าผู้ดูแล"
		}
		p.Options = append(p.Options, opt)
	}
	sort.SliceStable(p.Options[1:], func(i, j int) bool {
		return p.Options[1+i].Outcome == OutcomeFast && p.Options[1+j].Outcome != OutcomeFast
	})
	return p
}

// ChoiceAllowed: the choice is on the install's picker and not disabled.
func (p ModelPicker) ChoiceAllowed(choice string) (bool, string) {
	for _, o := range p.Options {
		if o.ID == choice {
			if o.Disabled {
				return false, o.Reason
			}
			return true, ""
		}
	}
	return false, "โมเดลนี้ไม่อยู่ในรายการที่องค์กรเลือกไว้"
}

func (a *API) pickerFor(ctx context.Context, in Install) (ModelPicker, error) {
	def, err := a.store.DefinitionByID(ctx, in.DefinitionID)
	if err != nil {
		return ModelPicker{}, err
	}
	m, err := ParseManifest(def.Manifest)
	if err != nil {
		return ModelPicker{}, err
	}
	o, err := a.store.OrgSettingsFor(ctx, orgOr(in.OrgID))
	if err != nil {
		return ModelPicker{}, err
	}
	var rm RunnerModels
	if st, err := a.store.LatestRunner(ctx, a.cfg.RunnerStaleAfter); err == nil {
		rm = RunnerModelsFrom(st.Capabilities)
	}
	return a.store.Picker(ctx, a.cfg, in, m, o, rm), nil
}

// modelChoiceAllowed checks a per-round choice sent with a message.
func (a *API) modelChoiceAllowed(w http.ResponseWriter, r *http.Request, in Install, choice string) bool {
	p, err := a.pickerFor(r.Context(), in)
	if err != nil {
		a.fail(w, err, "model")
		return false
	}
	if ok, why := p.ChoiceAllowed(choice); !ok {
		say(w, http.StatusConflict, "MODEL_NOT_ALLOWED", "detail", why)
		return false
	}
	return true
}

func (a *API) installModels(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	p, err := a.pickerFor(r.Context(), in)
	if err != nil {
		a.fail(w, err, "model")
		return
	}
	writeJSON(w, http.StatusOK, p)
}

// putInstallModel remembers the person's choice as this install's default.
func (a *API) putInstallModel(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	var body struct {
		Choice string `json:"choice"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil || strings.TrimSpace(body.Choice) == "" {
		badRequest(w, "BAD_JSON")
		return
	}
	if !a.modelChoiceAllowed(w, r, in, body.Choice) {
		return
	}
	if _, err := a.store.Pool.Exec(r.Context(), `UPDATE agent_market.agent_installs SET model_choice = $2, updated_at = now() WHERE id = $1`,
		in.ID, body.Choice); err != nil {
		a.fail(w, err, "model")
		return
	}
	in.ModelChoice = body.Choice
	p, _ := a.pickerFor(r.Context(), in)
	writeJSON(w, http.StatusOK, p)
}

// adminModels is the gateway's catalogue as the runner last saw it, for the
// admin choosing which models people may pick.
func (a *API) adminModels(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	acc := accessOf(r)
	o, err := a.store.OrgSettingsFor(r.Context(), acc.OrgID)
	if err != nil {
		a.fail(w, err, "model")
		return
	}
	var rm RunnerModels
	var seen *time.Time
	if st, err := a.store.LatestRunner(r.Context(), a.cfg.RunnerStaleAfter); err == nil {
		rm = RunnerModelsFrom(st.Capabilities)
		t := st.SeenAt
		seen = &t
	}
	writeJSON(w, http.StatusOK, map[string]any{"catalog": nonNil(rm.Catalog), "curated": nonNil(o.AgentModels),
		"defaults": map[string]string{"text": rm.Primary, "text_second": rm.Fallback, "vision": rm.Vision},
		"self_hosted": rm.SelfHosted, "documents_may_leave": o.DocumentsMayLeave, "seen_at": seen})
}
