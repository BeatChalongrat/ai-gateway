package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── Which model a round uses (AM4) ───────────────────────────────────────
//
// The person's choice (the round's, else the install's; "auto" by default)
// picks the model that reads scans and photos (vision agents) or writes the
// report's words (the financial report). The organisation decides whether
// anything may go to a model outside it (org_settings.documents_may_leave,
// off by default): while it is off, only models that run inside the
// organisation are ever sent anything — a chosen external model, the
// runner's own external fallback included. The choice never changes a check
// and never becomes the source of a number.

// catalogCache is the gateway's model catalogue (GET /v1/models), refreshed
// now and then; the heartbeat publishes it as "platform.models".
type catalogCache struct {
	mu      sync.Mutex
	models  []am.CatalogModel
	fetched time.Time
}

// Catalog returns the gateway's models, fetching them at most every 30 minutes.
func (m *ModelClient) Catalog(ctx context.Context) []am.CatalogModel {
	if m == nil {
		return nil
	}
	m.catalog.mu.Lock()
	defer m.catalog.mu.Unlock()
	if time.Since(m.catalog.fetched) < 30*time.Minute || m.BaseURL == "" || m.APIKey == "" {
		return m.catalog.models
	}
	m.catalog.fetched = time.Now() // one attempt per window, success or not
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(m.BaseURL, "/")+"/models", nil)
	if err != nil {
		return m.catalog.models
	}
	req.Header.Set("Authorization", "Bearer "+m.APIKey)
	req.Header.Set("X-Client-Name", "agent-runner")
	client := m.HTTP
	if client == nil {
		client = http.DefaultClient
	}
	resp, err := client.Do(req)
	if err != nil {
		return m.catalog.models
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return m.catalog.models
	}
	var out struct {
		Data []struct {
			ID           string  `json:"id"`
			SelfHosted   bool    `json:"self_hosted"`
			InputPrice   float64 `json:"input_price_per_1m"`
			OutputPrice  float64 `json:"output_price_per_1m"`
			Context      int     `json:"context_window"`
			Capabilities *struct {
				Vision bool `json:"vision"`
			} `json:"capabilities"`
			// AM16: who reads what is sent to this model (the gateway's own word; none for an in-house one)
			Recipients []string `json:"recipients"`
		} `json:"data"`
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 4<<20)).Decode(&out) != nil {
		return m.catalog.models
	}
	models := make([]am.CatalogModel, 0, len(out.Data))
	for _, d := range out.Data {
		models = append(models, am.CatalogModel{ID: d.ID, SelfHosted: d.SelfHosted, InPrice: d.InputPrice, OutPrice: d.OutputPrice,
			Context: d.Context, Vision: d.Capabilities != nil && d.Capabilities.Vision, Recipients: d.Recipients})
	}
	m.catalog.models = models
	return models
}

// IsSelfHosted: the model runs inside the organisation (the runner's own
// setting, or the gateway catalogue's flag).
func (m *ModelClient) IsSelfHosted(model string) bool {
	if model == "" || m == nil {
		return false
	}
	if m.SelfHosted[model] {
		return true
	}
	m.catalog.mu.Lock()
	defer m.catalog.mu.Unlock()
	for _, c := range m.catalog.models {
		if c.ID == model {
			return c.SelfHosted
		}
	}
	return false
}

func (m *ModelClient) readsImages(model string) bool {
	if model == "" || m == nil {
		return false
	}
	if model == m.VisionModel {
		return true
	}
	m.catalog.mu.Lock()
	defer m.catalog.mu.Unlock()
	for _, c := range m.catalog.models {
		if c.ID == model {
			return c.Vision
		}
	}
	return false
}

// modelsCapability is the heartbeat's "platform.models": what the control
// plane's picker shows (the runner's own models and the gateway catalogue).
func (r *Runner) modelsCapability(ctx context.Context) am.Binding {
	m := r.Model
	rm := am.RunnerModels{SelfHosted: map[string]bool{}}
	if m != nil {
		rm.Primary, rm.Fallback, rm.Vision = m.Primary, m.Fallback, m.VisionModel
		for k, v := range m.SelfHosted {
			rm.SelfHosted[k] = v
		}
		rm.Catalog = m.Catalog(ctx)
	}
	d, _ := json.Marshal(rm)
	return am.Binding{Capability: "platform.models", Impl: "gateway", Version: fmt.Sprintf("%d", len(rm.Catalog)), Detail: d}
}

// choice is the model this round asked for: the round's, else the install's, else auto.
//
// AM11b: whatever a person picks is what runs — so a pick that may not be used now is no pick. A model
// outside the organisation chosen while this agent's data could leave, after the switch is turned off,
// and a model that cannot read images chosen for an agent that reads scans, are not in the menu
// (am.MenuFor) and here read as "อัตโนมัติ": the menu shows it, the round runs it, and nothing is said
// afterwards about a model that was "not used". (A chosen model that fails to answer is another matter:
// the round is rescued by the next one and says so.)
func (rc *runCtx) choice() string {
	c := am.ModelAuto
	switch {
	case rc.run.ModelChoice != nil && *rc.run.ModelChoice != "":
		c = *rc.run.ModelChoice
	case rc.install.ModelChoice != "":
		c = rc.install.ModelChoice
	}
	if c == am.ModelAuto || rc.r == nil || rc.r.Model == nil {
		return c
	}
	if !rc.mayUse(c) || (am.ModelUse(rc.pkg.Manifest) == "vision" && !rc.r.Model.readsImages(c)) || rc.r.Model.windowTooSmall(c) {
		return am.ModelAuto
	}
	return c
}

// windowTooSmall: the gateway states a context window for this model that
// cannot take an agent's question (am.MinAgentContext) — the menu does not
// list such a model (am.MenuFor), and a pick of it from before is no pick.
// The models the runner itself is configured with are taken as able.
func (m *ModelClient) windowTooSmall(id string) bool {
	if m == nil || id == m.Primary || id == m.Fallback || id == m.VisionModel {
		return false
	}
	for _, c := range m.catalog.models {
		if c.ID == id {
			return c.Context > 0 && c.Context < am.MinAgentContext
		}
	}
	return false
}

// reaches (AM16): a company that can run this model is among the recipients named for the agent. An
// in-house model is not asked — nothing leaves.
func (m *ModelClient) reaches(model string, allowed []string) bool {
	if m == nil || len(allowed) == 0 {
		return false
	}
	named := map[string]bool{}
	for _, k := range allowed {
		named[k] = true
	}
	m.catalog.mu.Lock()
	defer m.catalog.mu.Unlock()
	for _, c := range m.catalog.models {
		if c.ID != model {
			continue
		}
		for _, k := range c.Recipients {
			if named[k] {
				return true
			}
		}
	}
	return false
}

// mayUse: the organisation lets this model see the round's data — an in-house model always; a model
// outside only while the agent's documents may leave AND a company that runs it has been named for
// the agent (AM16: "may leave" with nobody named reaches nobody).
func (rc *runCtx) mayUse(model string) bool {
	return model != "" && (rc.r.Model.IsSelfHosted(model) || (rc.docsMayLeave && rc.r.Model.reaches(model, rc.recipients)))
}

// mayRead: this model may be given the image of a page of the round's documents. An image cannot be
// masked (the gateway masks text), so while the admin's masking switch for the agent is on a page is
// read inside the organisation only — whatever the other switch says (AM16).
func (rc *runCtx) mayRead(model string) bool {
	return model != "" && (rc.r.Model.IsSelfHosted(model) || (rc.docsMayLeave && !rc.masked && rc.r.Model.reaches(model, rc.recipients)))
}

// visionModel is the model that reads this round's scans and photos, and
// whether it is not the one chosen (the chosen one is not allowed now or
// cannot read images). "" = none may read them (OCR on the organisation's
// own machine only).
func (rc *runCtx) visionModel() (model string, notChosen bool) {
	m := rc.r.Model
	if m == nil || !m.Available() {
		return "", false
	}
	caps := rc.pkg.Manifest.Capabilities
	if !contains(caps.Optional, "model.vision") && !contains(caps.Required, "model.vision") {
		return "", false
	}
	c := rc.choice()
	if c != am.ModelAuto && m.readsImages(c) && rc.mayRead(c) {
		return c, false
	}
	if rc.mayRead(m.VisionModel) {
		return m.VisionModel, c != am.ModelAuto && c != m.VisionModel
	}
	return "", c != am.ModelAuto
}

// writingModels are the models that may write the report's words, in order:
// the chosen one, then the runner's primary and fallback — each only if the
// organisation lets it see the computed table.
func (rc *runCtx) writingModels() []string {
	var out []string
	add := func(x string) {
		if rc.mayUse(x) && !contains(out, x) {
			out = append(out, x)
		}
	}
	if c := rc.choice(); c != am.ModelAuto {
		add(c)
	}
	if rc.r.Model != nil {
		add(rc.r.Model.Primary)
		add(rc.r.Model.Fallback)
	}
	return out
}

// chosenModel is what the person's choice resolves to for this round (for
// saying so when another model answered).
func (rc *runCtx) chosenModel() string {
	if c := rc.choice(); c != am.ModelAuto {
		return c
	}
	if am.ModelUse(rc.pkg.Manifest) == "vision" {
		return rc.r.Model.VisionModel
	}
	return rc.r.Model.Primary
}

// docsMayLeave: this round's documents may go to a model outside the
// organisation — the organisation's switch and, under it, the agent's own
// (AM9: off by default for an agent that touches accounting).
func (r *Runner) docsMayLeave(ctx context.Context, org, slug string, m am.Manifest) bool {
	leave, _, _ := r.switchesOf(ctx, org, slug, m)
	return leave
}

// switchesOf is what the admin's two switches say for this agent's rounds (AM16): whether its
// documents may leave, and whether personal data is masked before a model reads it. A setting that
// cannot be read is the careful one of each: nothing leaves, and what is sent is masked.
func (r *Runner) switchesOf(ctx context.Context, org, slug string, m am.Manifest) (mayLeave, masked bool, recipients []string) {
	e, err := r.Store.EgressFor(ctx, orgOrDefault(org), slug, m)
	if err != nil {
		return false, true, []string{}
	}
	if e.Recipients == nil {
		return e.Effective, e.Masked, []string{}
	}
	return e.Effective, e.Masked, e.Recipients
}

func orgOrDefault(org string) string {
	if org == "" {
		return am.DefaultOrg
	}
	return org
}
