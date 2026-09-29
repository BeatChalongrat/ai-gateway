package agentmarket

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"sort"
	"strings"
)

// ── Which model reads the documents (AM4) ────────────────────────────────
//
// "อัตโนมัติ" is the default and most people never move off it: it resolves
// from the capability the agent's manifest declares (model.vision → the
// runner's vision model; model.text → its text model).
//
// A model outside the organisation is offered only when the organisation
// lets documents leave (org_settings.documents_may_leave, off by default);
// otherwise it is shown blocked with the reason, never quietly selectable.
// The choice never changes a check and never becomes the source of a
// number: it buys a better reading of a bad scan and better prose, nothing
// else.
//
// AM6: the menu is the chat's own. The rows this file used to build — a model
// under an outcome's name, a cost and a time per row, the line about one
// in-house model, "เลือกโมเดลเอง" — are gone, and so is the organisation's
// list of models. What is left is what the chat's menu cannot know by
// itself: which of the models can do this agent's job, which of those the
// organisation's policy allows, and one sentence for each of the two reasons
// a model is not to be picked. What a round costs is said where a round
// starts (estimate.go), not per model.

// ModelAuto is the default choice.
const ModelAuto = "auto"

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
	Primary    string          `json:"primary"`
	Fallback   string          `json:"fallback"`
	Vision     string          `json:"vision"`
	SelfHosted map[string]bool `json:"self_hosted"`
	Catalog    []CatalogModel  `json:"catalog"`
}

// MenuModel is one model that can do the agent's job: its id as the gateway
// names it (the id the chat's own list has), and whether the organisation's
// policy allows it.
type MenuModel struct {
	ID      string `json:"id"`
	Allowed bool   `json:"allowed"`
}

// ModelPicker is what the chat's model menu needs to serve as an agent's.
type ModelPicker struct {
	Uses      string      `json:"uses"` // vision | text | none
	Choice    string      `json:"choice"`
	AutoModel string      `json:"auto_model"` // what "อัตโนมัติ" resolves to now
	Models    []MenuModel `json:"models"`
	// BlockedReason is why a listed model is not allowed ("" when every one
	// is); HiddenReason is why a model is not listed at all ("" when every
	// model can do the job).
	BlockedReason string `json:"blocked_reason"`
	HiddenReason  string `json:"hidden_reason"`
	Reads         string `json:"reads"`
	Note          string `json:"note"`
	// cannot are the runner's models that cannot do this agent's job.
	cannot map[string]bool
}

const (
	reasonOutside   = "โมเดลนอกองค์กรเลือกไม่ได้ เพราะองค์กรยังไม่อนุญาตให้ส่งเอกสารออกนอกองค์กร"
	reasonNoVision  = "agent นี้ต้องใช้โมเดลที่อ่านภาพได้"
	reasonNotListed = "โมเดลนี้ไม่อยู่ในรายการโมเดลที่ agent นี้ใช้ได้"
)

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

// all is every model the runner has, once each: its catalogue's and its own.
func (rm RunnerModels) all() []string {
	seen := map[string]bool{}
	var out []string
	add := func(id string) {
		if id != "" && !seen[id] {
			seen[id] = true
			out = append(out, id)
		}
	}
	for _, c := range rm.Catalog {
		add(c.ID)
	}
	add(rm.Primary)
	add(rm.Fallback)
	add(rm.Vision)
	sort.Strings(out)
	return out
}

// lock is why a model may not be given this agent's documents now ("" = it may).
func (rm RunnerModels) lock(use, id string, docsMayLeave bool) string {
	switch {
	case !rm.canRead(use, id):
		return reasonNoVision
	case !rm.SelfHosted[id] && !docsMayLeave:
		return reasonOutside
	}
	return ""
}

// MenuFor builds the menu for one install.
func MenuFor(in Install, m Manifest, o OrgSettings, rm RunnerModels) ModelPicker {
	use := ModelUse(m)
	p := ModelPicker{Uses: use, Choice: in.ModelChoice, Models: []MenuModel{}, cannot: map[string]bool{}}
	if p.Choice == "" {
		p.Choice = ModelAuto
	}
	switch use {
	case "vision":
		p.Reads = "อ่านเอกสารที่เป็นสแกนหรือรูปถ่าย (PDF ดิจิทัลและ e-Tax อ่านตรงจากไฟล์ ไม่ส่งให้โมเดล)"
		p.HiddenReason = reasonNoVision
	case "text":
		p.Reads = "เขียนคำอธิบายในรายงานจากตัวเลขที่คำนวณเสร็จแล้ว (ตัวเลขทุกตัวคำนวณด้วยสคริปต์ ไม่ได้มาจากโมเดล)"
	default:
		p.Reads = "agent นี้ไม่ส่งเอกสารให้โมเดลใดเลย ทุกค่าอ่านจากไฟล์และคำนวณด้วยสคริปต์"
	}
	p.Note = "โมเดลที่เลือกไม่ได้ข้ามการตรวจใด ๆ และไม่ได้เป็นที่มาของตัวเลข — ช่วยให้อ่านสแกนที่ไม่ชัดและเขียนคำอธิบายได้ดีขึ้นเท่านั้น"
	if use == "none" {
		return p
	}
	p.AutoModel = rm.autoModel(use)
	for _, id := range rm.all() {
		if !rm.canRead(use, id) {
			p.cannot[id] = true
			continue
		}
		allowed := rm.lock(use, id, o.DocumentsMayLeave) == ""
		if !allowed {
			p.BlockedReason = reasonOutside
		}
		p.Models = append(p.Models, MenuModel{ID: id, Allowed: allowed})
	}
	return p
}

// ChoiceAllowed: the choice is "อัตโนมัติ", or a model of the menu that the
// organisation's policy allows. Any other is refused, with the reason a
// person reads.
func (p ModelPicker) ChoiceAllowed(choice string) (bool, string) {
	if choice == ModelAuto {
		return true, ""
	}
	for _, m := range p.Models {
		if m.ID != choice {
			continue
		}
		if !m.Allowed {
			return false, p.BlockedReason
		}
		return true, ""
	}
	if p.cannot[choice] && p.HiddenReason != "" {
		return false, p.HiddenReason
	}
	return false, reasonNotListed
}

func (a *API) menuFor(ctx context.Context, in Install) (ModelPicker, error) {
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
	return MenuFor(in, m, o, rm), nil
}

func (a *API) installModels(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	p, err := a.menuFor(r.Context(), in)
	if err != nil {
		a.fail(w, err, "model")
		return
	}
	writeJSON(w, http.StatusOK, p)
}

// putInstallModel remembers the person's choice as this install's: every
// round of the install runs with it.
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
	p, err := a.menuFor(r.Context(), in)
	if err != nil {
		a.fail(w, err, "model")
		return
	}
	if ok, why := p.ChoiceAllowed(body.Choice); !ok {
		say(w, http.StatusConflict, "MODEL_NOT_ALLOWED", "detail", why)
		return
	}
	if _, err := a.store.Pool.Exec(r.Context(), `UPDATE agent_market.agent_installs SET model_choice = $2, updated_at = now() WHERE id = $1`,
		in.ID, body.Choice); err != nil {
		a.fail(w, err, "model")
		return
	}
	p.Choice = body.Choice
	writeJSON(w, http.StatusOK, p)
}
