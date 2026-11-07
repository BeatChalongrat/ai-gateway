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
// agent nobody has set takes its default: off (AM16: for every agent; until
// then an agent that touched neither accounting nor the web defaulted to on).
//
// AM16 — masking is the admin's second switch for the agent, and its own.
// Until then it followed the first one (AM13b): documents that could leave
// were masked, documents that could not were sent to the in-house model as
// typed. The owner separated them: reading a document correctly matters more
// than masking it on the way, so documents that leave are sent as typed
// unless an admin turns masking on for that agent. Both are off by default,
// and each change of either is written to the audit log.

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
	// Web (AM12): the agent works on the web — it sends what a person asked for
	// to a search provider and opens pages outside the organisation. Its default
	// is off too: nothing of it leaves until an admin says so.
	Web bool `json:"web"`
	// Effective: documents of this agent may go to an outside model.
	Effective bool       `json:"effective"`
	Words     PersonText `json:"words"`
	// Masked (AM16): the admin's own choice for this agent — personal data in the text this agent sends
	// to a model is masked first. Off unless an admin turns it on.
	Masked bool `json:"masked"`
	// PagesMayLeave (AM16): the image of a page of this agent's documents may be read by a model
	// outside the organisation. An image cannot be masked, so this needs Effective AND masking off.
	PagesMayLeave bool `json:"pages_may_leave"`
	// Recipients (AM16, recipients.go): who outside the organisation this agent's documents may reach —
	// its own ticks (AgentRecipients) that the organisation's list (OrgRecipients) also holds, and only
	// while Effective. Empty is nobody: "may leave" with nothing ticked opens nothing.
	Recipients      []string `json:"recipients"`
	AgentRecipients []string `json:"agent_recipients"`
	OrgRecipients   []string `json:"org_recipients"`
}

// WithMasked puts the admin's masking switch beside the leave switch.
func (e Egress) WithMasked(masked bool) Egress {
	e.Masked = masked
	e.PagesMayLeave = e.Effective && !masked
	return e
}

// TouchesAccounting is whether an agent reads accounting data: every agent
// whose manifest's category is "finance" does (all five shipped agents).
func TouchesAccounting(m Manifest) bool {
	return strings.EqualFold(strings.TrimSpace(m.Category), "finance")
}

// EgressOf decides one agent's egress from the organisation's switch, the
// agent's manifest and its own setting (nil: never set).
func EgressOf(orgMayLeave bool, m Manifest, set *bool) Egress {
	e := Egress{Org: orgMayLeave, Accounting: TouchesAccounting(m), Web: m.UsesWeb()}
	if set != nil {
		e.Agent = *set
	} else {
		// AM16: off for every agent nobody has set — the owner's "both off by default"
		e.AgentDefault = true
		e.Agent = false
	}
	e.Effective = e.Org && e.Agent
	e.PagesMayLeave = e.Effective
	if e.Web {
		// AM12: for an agent that works on the web the same switch decides whether it may search and open
		// pages at all, and the words say that
		switch {
		case !e.Org:
			e.Words = Text("องค์กรไม่อนุญาตให้ agent ทำงานนอกองค์กร agent นี้จึงยังค้นหาและเปิดหน้าเว็บไม่ได้")
		case !e.Agent && e.AgentDefault:
			e.Words = Text("agent นี้ค้นหาและเปิดหน้าเว็บนอกองค์กร จึงปิดไว้เป็นค่าตั้งต้น — ผู้ดูแลเปิดให้ agent นี้ได้ที่นี่ เมื่อเปิดแล้ว ข้อความคำขอของผู้ใช้จะถูกส่งไปค้นหาและให้โมเดลนอกองค์กรอ่าน")
		case !e.Agent:
			e.Words = Text("ผู้ดูแลตั้งให้ agent นี้ไม่ค้นหาและไม่เปิดหน้าเว็บนอกองค์กร")
		default:
			e.Words = Text("agent นี้ค้นหาและเปิดหน้าเว็บนอกองค์กรได้ ข้อความคำขอของผู้ใช้ถูกส่งไปค้นหาและให้โมเดลนอกองค์กรอ่าน")
		}
		return e
	}
	switch {
	case !e.Org:
		e.Words = Text("องค์กรไม่อนุญาตให้ส่งเอกสารไปโมเดลนอกองค์กร agent นี้จึงใช้ได้เฉพาะโมเดลในองค์กร")
	case !e.Agent && e.AgentDefault && !e.Accounting:
		e.Words = Text("องค์กรอนุญาตให้ส่งเอกสารไปโมเดลนอกองค์กรได้ แต่ agent นี้ยังไม่ได้เปิดให้ส่งออก ซึ่งเป็นค่าตั้งต้น — ผู้ดูแลเปิดให้ agent นี้ได้ที่แอปผู้ดูแลระบบ")
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
	set, _, err := s.AgentSwitches(ctx, q, org, slug)
	return set, err
}

// AgentSwitches is an agent's row of the admin's two switches (AM16): whether its documents may leave
// (nil: never set) and whether they are masked (false: never set — the default). A row an admin made by
// setting only masking carries no word about leaving; its leave value is the default it was made with.
func (s *Store) AgentSwitches(ctx context.Context, q Querier, org, slug string) (mayLeave *bool, masked bool, err error) {
	var leave, leaveSet bool
	err = q.QueryRow(ctx, `SELECT documents_may_leave, leave_set, masked FROM agent_market.agent_egress WHERE org_id = $1 AND slug = $2`,
		orgOr(org), slug).Scan(&leave, &leaveSet, &masked)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	if !leaveSet {
		return nil, masked, nil
	}
	return &leave, masked, nil
}

// EgressFor is one agent's egress in an organisation, as rounds and menus apply it.
func (s *Store) EgressFor(ctx context.Context, org, slug string, m Manifest) (Egress, error) {
	o, err := s.OrgSettingsFor(ctx, orgOr(org))
	if err != nil && !errors.Is(err, ErrNotFound) {
		return Egress{}, err
	}
	set, masked, err := s.AgentSwitches(ctx, s.Pool, org, slug)
	if err != nil {
		return Egress{}, err
	}
	orgList, err := s.OrgRecipients(ctx, s.Pool, org)
	if err != nil {
		return Egress{}, err
	}
	own, err := s.AgentRecipients(ctx, s.Pool, org, slug)
	if err != nil {
		return Egress{}, err
	}
	return EgressOf(o.DocumentsMayLeave, m, set).WithMasked(masked).WithRecipients(orgList, own), nil
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
	orgList, err := a.store.OrgRecipients(ctx, a.store.Pool, acc.OrgID)
	if err != nil {
		a.fail(w, err, "egress")
		return
	}
	for _, d := range defs {
		m, err := ParseManifest(d.Manifest)
		if err != nil {
			continue
		}
		set, masked, err := a.store.AgentSwitches(ctx, a.store.Pool, acc.OrgID, d.Slug)
		if err != nil {
			a.fail(w, err, "egress")
			return
		}
		own, err := a.store.AgentRecipients(ctx, a.store.Pool, acc.OrgID, d.Slug)
		if err != nil {
			a.fail(w, err, "egress")
			return
		}
		out = append(out, row{Slug: d.Slug, Name: d.Name, Egress: EgressOf(o.DocumentsMayLeave, m, set).WithMasked(masked).WithRecipients(orgList, own)})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	// AM16: who can be ticked at all — every recipient the gateway's catalogue names, route and company
	writeJSON(w, http.StatusOK, map[string]any{"org": o.DocumentsMayLeave, "agents": out,
		"org_recipients": orgList, "available": a.runnerModels(ctx).RecipientOptions()})
}

// adminPutEgress sets one agent's own settings: whether its documents may leave (it can be set either
// way at any time; it only ever narrows what the organisation's switch allows), and — AM16 — whether
// they are masked. One request sets one or both; each one that moved is its own line of the audit log.
func (a *API) adminPutEgress(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	var body struct {
		DocumentsMayLeave *bool `json:"documents_may_leave"`
		Masked            *bool `json:"masked"`
		// AM16: the agent's own list of recipients, whole (recipients.go)
		Recipients *[]string `json:"recipients"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body); err != nil ||
		(body.DocumentsMayLeave == nil && body.Masked == nil && body.Recipients == nil) {
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
		before, wasMasked, err := a.store.AgentSwitches(ctx, tx, acc.OrgID, slug)
		if err != nil {
			return nil, err
		}
		from := EgressOf(o.DocumentsMayLeave, m, before).WithMasked(wasMasked)
		// one row holds both; the one the request does not name stays as it is (leave_set: an admin
		// has said whether the documents may leave — a row made for masking alone says nothing of it)
		leave, leaveSet, masked := from.Agent, before != nil, wasMasked
		if body.DocumentsMayLeave != nil {
			leave, leaveSet = *body.DocumentsMayLeave, true
		}
		if body.Masked != nil {
			masked = *body.Masked
		}
		if body.DocumentsMayLeave != nil || body.Masked != nil {
			if _, err := tx.Exec(ctx, `INSERT INTO agent_market.agent_egress (org_id, slug, documents_may_leave, leave_set, masked, updated_by)
				VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (org_id, slug) DO UPDATE SET documents_may_leave = EXCLUDED.documents_may_leave,
				leave_set = EXCLUDED.leave_set, masked = EXCLUDED.masked,
				updated_by = EXCLUDED.updated_by, updated_at = now()`, orgOr(acc.OrgID), slug, leave, leaveSet, masked,
				normEmail(c.Email)); err != nil {
				return nil, err
			}
		}
		var entries []AuditEntry
		if body.Recipients != nil {
			// an agent's list may only narrow the organisation's, never widen it: a recipient the
			// organisation has not named cannot be ticked here (one ticked before, and since taken off
			// the organisation's list, may be left as it is or unticked — it counts for nothing)
			ceiling, err := a.store.OrgRecipients(ctx, tx, acc.OrgID)
			if err != nil {
				return nil, err
			}
			had, err := a.store.AgentRecipients(ctx, tx, acc.OrgID, slug)
			if err != nil {
				return nil, err
			}
			ok := map[string]bool{}
			for _, k := range append(ceiling, had...) {
				ok[k] = true
			}
			for _, k := range cleanRecipients(*body.Recipients) {
				if !ok[k] {
					return nil, refusal{status: http.StatusConflict, code: "BAD_REQUEST", vars: []string{"code", "RECIPIENT_NOT_ON_THE_ORGANISATIONS_LIST"}}
				}
			}
			added, removed, err := a.store.setAgentRecipients(ctx, tx, acc.OrgID, slug, *body.Recipients, normEmail(c.Email))
			if err != nil {
				return nil, err
			}
			entries = append(entries, recipientEntries(r, "agent", "agent_market_agent", slug, added, removed, map[string]any{"agent": def.Name})...)
		}
		if body.DocumentsMayLeave != nil && (from.Agent != *body.DocumentsMayLeave || from.AgentDefault) {
			entries = append(entries, entry(r, "agent_market.agent.documents_may_leave", "agent_market_agent", slug, map[string]any{
				"setting": "documents_may_leave", "label": T(LangOf(r.Context()), "เอกสารของ agent นี้ส่งไปโมเดลนอกองค์กรได้"), "agent": def.Name,
				"from": from.Agent, "from_default": from.AgentDefault, "to": *body.DocumentsMayLeave,
				"org_allows": o.DocumentsMayLeave, "masked": masked}))
		}
		if body.Masked != nil && wasMasked != *body.Masked {
			entries = append(entries, entry(r, "agent_market.agent.masked", "agent_market_agent", slug, map[string]any{
				"setting": "masked", "label": T(LangOf(r.Context()), "ปิดบังข้อมูลส่วนบุคคลก่อนส่งให้โมเดล"), "agent": def.Name,
				"from": wasMasked, "to": *body.Masked, "documents_may_leave": leave && o.DocumentsMayLeave}))
		}
		return entries, nil // nothing moved: nothing to log
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
