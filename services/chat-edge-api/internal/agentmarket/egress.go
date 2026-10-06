package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sort"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
)

// ── Per-agent document egress (AM9) ──────────────────────────────────────
//
// One organisation-wide switch was too blunt: the agent that reads payroll
// and the agent that reads suppliers' invoices should not share one answer.
// The organisation's switch stays the ceiling; each agent has a setting under
// it that can only be stricter — documents leave only when both allow it. An
// agent nobody has set takes its default: off when it touches accounting.

// Egress is whether one agent's documents may go to a model outside the
// organisation, with both levels a person sees where they choose a model.
type Egress struct {
	// Org is the organisation's switch: the ceiling.
	Org bool `json:"org"`
	// Agent is this agent's own setting; AgentDefault: no admin has set it.
	Agent        bool `json:"agent"`
	AgentDefault bool `json:"agent_default"`
	// Accounting: the agent touches accounting data (its default is off).
	Accounting bool `json:"accounting"`
	// Effective: documents of this agent may go to an outside model.
	Effective bool       `json:"effective"`
	Words     PersonText `json:"words"`
}

// TouchesAccounting is whether an agent reads accounting data: every agent
// whose manifest's category is "finance" does (all five shipped agents).
func TouchesAccounting(m Manifest) bool {
	return strings.EqualFold(strings.TrimSpace(m.Category), "finance")
}

// EgressOf decides one agent's egress from the organisation's switch, the
// agent's manifest and its own setting (nil: never set).
func EgressOf(orgMayLeave bool, m Manifest, set *bool) Egress {
	e := Egress{Org: orgMayLeave, Accounting: TouchesAccounting(m)}
	if set != nil {
		e.Agent = *set
	} else {
		e.AgentDefault = true
		e.Agent = !e.Accounting
	}
	e.Effective = e.Org && e.Agent
	switch {
	case !e.Org:
		e.Words = Text("องค์กรไม่อนุญาตให้ส่งเอกสารไปโมเดลนอกองค์กร agent นี้จึงใช้ได้เฉพาะโมเดลในองค์กร")
	case !e.Agent && e.AgentDefault:
		e.Words = Text("องค์กรอนุญาตให้ส่งเอกสารไปโมเดลนอกองค์กรได้ แต่ agent นี้อ่านข้อมูลบัญชี จึงไม่ส่งออกเป็นค่าตั้งต้น — ผู้ดูแลเปิดให้ agent นี้ได้ที่แอปผู้ดูแลระบบ")
	case !e.Agent:
		e.Words = Text("องค์กรอนุญาตให้ส่งเอกสารไปโมเดลนอกองค์กรได้ แต่ผู้ดูแลตั้งให้ agent นี้ไม่ส่งออก")
	default:
		e.Words = Text("องค์กรและ agent นี้อนุญาตให้ส่งเอกสารไปโมเดลนอกองค์กรได้")
	}
	return e
}

// AgentEgressSetting is an agent's own setting in an organisation (nil: never set).
func (s *Store) AgentEgressSetting(ctx context.Context, q Querier, org, slug string) (*bool, error) {
	var v bool
	err := q.QueryRow(ctx, `SELECT documents_may_leave FROM agent_market.agent_egress WHERE org_id = $1 AND slug = $2`,
		orgOr(org), slug).Scan(&v)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &v, nil
}

// EgressFor is one agent's egress in an organisation, as rounds and menus apply it.
func (s *Store) EgressFor(ctx context.Context, org, slug string, m Manifest) (Egress, error) {
	o, err := s.OrgSettingsFor(ctx, orgOr(org))
	if err != nil && !errors.Is(err, ErrNotFound) {
		return Egress{}, err
	}
	set, err := s.AgentEgressSetting(ctx, s.Pool, org, slug)
	if err != nil {
		return Egress{}, err
	}
	return EgressOf(o.DocumentsMayLeave, m, set), nil
}

// ── the admin door ───────────────────────────────────────────────────────

// adminEgress lists every agent of the catalogue with both levels.
func (a *API) adminEgress(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	ctx := r.Context()
	acc := accessOf(r)
	o, err := a.store.OrgSettingsFor(ctx, acc.OrgID)
	if err != nil {
		a.fail(w, err, "egress")
		return
	}
	defs, err := a.store.ListDefinitions(ctx)
	if err != nil {
		a.fail(w, err, "egress")
		return
	}
	type row struct {
		Slug string `json:"slug"`
		Name string `json:"name"`
		Egress
	}
	out := []row{}
	for _, d := range defs {
		m, err := ParseManifest(d.Manifest)
		if err != nil {
			continue
		}
		set, err := a.store.AgentEgressSetting(ctx, a.store.Pool, acc.OrgID, d.Slug)
		if err != nil {
			a.fail(w, err, "egress")
			return
		}
		out = append(out, row{Slug: d.Slug, Name: d.Name, Egress: EgressOf(o.DocumentsMayLeave, m, set)})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	writeJSON(w, http.StatusOK, map[string]any{"org": o.DocumentsMayLeave, "agents": out})
}

// adminPutEgress sets one agent's own setting. It can be set either way at
// any time; it only ever narrows what the organisation's switch allows.
func (a *API) adminPutEgress(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	var body struct {
		DocumentsMayLeave *bool `json:"documents_may_leave"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<12)).Decode(&body); err != nil || body.DocumentsMayLeave == nil {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	acc := accessOf(r)
	c, _ := caller(r)
	slug := chi.URLParam(r, "slug")
	def, err := a.store.DefinitionBySlug(ctx, slug)
	if err != nil {
		a.fail(w, err, "egress")
		return
	}
	m, err := ParseManifest(def.Manifest)
	if err != nil {
		a.fail(w, err, "egress")
		return
	}
	err = a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		o, err := a.store.orgSettingsQ(ctx, tx, acc.OrgID, true)
		if err != nil {
			return nil, err
		}
		before, err := a.store.AgentEgressSetting(ctx, tx, acc.OrgID, slug)
		if err != nil {
			return nil, err
		}
		from := EgressOf(o.DocumentsMayLeave, m, before)
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.agent_egress (org_id, slug, documents_may_leave, updated_by)
			VALUES ($1, $2, $3, $4) ON CONFLICT (org_id, slug) DO UPDATE SET documents_may_leave = EXCLUDED.documents_may_leave,
			updated_by = EXCLUDED.updated_by, updated_at = now()`, orgOr(acc.OrgID), slug, *body.DocumentsMayLeave,
			normEmail(c.Email)); err != nil {
			return nil, err
		}
		if from.Agent == *body.DocumentsMayLeave && !from.AgentDefault {
			return nil, nil // nothing moved: nothing to log
		}
		return []AuditEntry{entry(r, "agent_market.agent.documents_may_leave", "agent_market_agent", slug, map[string]any{
			"setting": "documents_may_leave", "label": T(LangOf(r.Context()), "เอกสารของ agent นี้ส่งไปโมเดลนอกองค์กรได้"), "agent": def.Name,
			"from": from.Agent, "from_default": from.AgentDefault, "to": *body.DocumentsMayLeave,
			"org_allows": o.DocumentsMayLeave})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "egress")
		return
	}
	e, err := a.store.EgressFor(ctx, acc.OrgID, slug, m)
	if err != nil {
		a.fail(w, err, "egress")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"slug": slug, "name": def.Name, "egress": e})
}
