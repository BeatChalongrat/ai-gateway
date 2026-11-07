package agentmarket

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
)

// ── Who an agent's documents may reach (AM16) ────────────────────────────
//
// "Documents may leave" said yes or no; it did not say to whom. With it on,
// every model of the catalogue was open. The owner: let the admin choose which
// outside providers an agent may reach — limiting the recipient costs the
// agent nothing — and:
//
//   - name both the route and the company: a page sent through a router is
//     seen by two parties, and the admin must see two;
//   - "may leave" on with nothing ticked means nobody;
//   - the organisation has its own list, the ceiling; an agent's list may
//     only narrow it, never widen it.
//
// A recipient is a key the gateway gives for each model of its catalogue
// (backend/app/services/providers/recipients.py): "<route>" for a provider
// reached directly, "<route>:<company>" behind a router — once for each
// company that can RUN the model there, which is not always the one that made
// it (asked of OpenRouter on 13 Oct 2026, Claude was run by Amazon and GPT by
// Microsoft). Nobody types a key: the admin ticks what the catalogue names.
//
// What holds it is not this file. The runner leaves a model out of an agent's
// menu when no ticked company runs it, and sends the agent's list with every
// model call; the gateway refuses anything else before a byte is sent.

// RecipientOption is one recipient an admin can tick: the route, and the
// company that runs the model behind it — both read what is sent.
type RecipientOption struct {
	Key     string   `json:"key"`
	Route   string   `json:"route"`
	Company string   `json:"company"`
	Models  []string `json:"models"`
}

// SplitRecipient is a key's two names: "openrouter:google" → openrouter,
// google; "deepseek" → deepseek, deepseek (reached directly: one party).
func SplitRecipient(key string) (route, company string) {
	if i := strings.Index(key, ":"); i >= 0 {
		return key[:i], key[i+1:]
	}
	return key, key
}

// cleanRecipients is a list as it is kept: trimmed, no blanks, once each, sorted.
func cleanRecipients(in []string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, k := range in {
		k = strings.TrimSpace(k)
		if k != "" && !seen[k] {
			seen[k] = true
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

func inBoth(a, b []string) []string {
	have := map[string]bool{}
	for _, k := range b {
		have[k] = true
	}
	out := []string{}
	for _, k := range a {
		if have[k] {
			out = append(out, k)
		}
	}
	return out
}

// WithRecipients puts beside the two switches who outside the organisation
// this agent's documents may reach: its own ticks that the organisation's
// list also holds, and only while its documents may leave at all. With that
// switch on and nothing ticked, the answer is nobody — the switch alone
// opens nothing.
func (e Egress) WithRecipients(org, agent []string) Egress {
	e.OrgRecipients, e.AgentRecipients = cleanRecipients(org), cleanRecipients(agent)
	e.Recipients = []string{}
	if e.Effective {
		e.Recipients = inBoth(e.AgentRecipients, e.OrgRecipients)
	}
	return e
}

// recipientsOf is the keys the gateway's catalogue gives for a model: every
// company that can run it. None: an in-house model, or one nobody can be said
// to run.
func (rm RunnerModels) recipientsOf(id string) []string {
	if c, ok := rm.find(id); ok {
		return c.Recipients
	}
	return nil
}

// reaches: a model outside the organisation may be given this agent's
// documents only when a company that can run it is among the allowed.
func (rm RunnerModels) reaches(id string, allowed []string) bool {
	return len(inBoth(rm.recipientsOf(id), allowed)) > 0
}

// RecipientOptions is every recipient the catalogue names, with the models
// each can run — what an admin is offered to tick.
func (rm RunnerModels) RecipientOptions() []RecipientOption {
	by := map[string]*RecipientOption{}
	for _, c := range rm.Catalog {
		if c.SelfHosted || rm.SelfHosted[c.ID] {
			continue
		}
		for _, k := range c.Recipients {
			o := by[k]
			if o == nil {
				route, company := SplitRecipient(k)
				o = &RecipientOption{Key: k, Route: route, Company: company, Models: []string{}}
				by[k] = o
			}
			o.Models = append(o.Models, c.ID)
		}
	}
	out := make([]RecipientOption, 0, len(by))
	for _, o := range by {
		sort.Strings(o.Models)
		out = append(out, *o)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key < out[j].Key })
	return out
}

// OrgRecipients is the organisation's own list: the ceiling.
func (s *Store) OrgRecipients(ctx context.Context, q Querier, org string) ([]string, error) {
	var out []string
	err := q.QueryRow(ctx, `SELECT coalesce(array_agg(recipient ORDER BY recipient), '{}') FROM agent_market.org_recipients WHERE org_id = $1`,
		orgOr(org)).Scan(&out)
	return cleanRecipients(out), err
}

// AgentRecipients is one agent's own ticks (whether or not the organisation's list still holds them).
func (s *Store) AgentRecipients(ctx context.Context, q Querier, org, slug string) ([]string, error) {
	var out []string
	err := q.QueryRow(ctx, `SELECT coalesce(array_agg(recipient ORDER BY recipient), '{}') FROM agent_market.agent_recipients
		WHERE org_id = $1 AND slug = $2`, orgOr(org), slug).Scan(&out)
	return cleanRecipients(out), err
}

func moved(before, want []string) (added, removed []string) {
	was, now := map[string]bool{}, map[string]bool{}
	for _, k := range before {
		was[k] = true
	}
	for _, k := range want {
		now[k] = true
		if !was[k] {
			added = append(added, k)
		}
	}
	for _, k := range before {
		if !now[k] {
			removed = append(removed, k)
		}
	}
	return added, removed
}

func (s *Store) setOrgRecipients(ctx context.Context, tx pgx.Tx, org string, want []string, by string) (added, removed []string, err error) {
	before, err := s.OrgRecipients(ctx, tx, org)
	if err != nil {
		return nil, nil, err
	}
	want = cleanRecipients(want)
	if _, err = tx.Exec(ctx, `DELETE FROM agent_market.org_recipients WHERE org_id = $1 AND NOT (recipient = ANY($2::text[]))`, orgOr(org), want); err != nil {
		return nil, nil, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO agent_market.org_recipients (org_id, recipient, updated_by)
		SELECT $1, k, $3 FROM unnest($2::text[]) AS k ON CONFLICT (org_id, recipient) DO NOTHING`, orgOr(org), want, by); err != nil {
		return nil, nil, err
	}
	added, removed = moved(before, want)
	return added, removed, nil
}

func (s *Store) setAgentRecipients(ctx context.Context, tx pgx.Tx, org, slug string, want []string, by string) (added, removed []string, err error) {
	before, err := s.AgentRecipients(ctx, tx, org, slug)
	if err != nil {
		return nil, nil, err
	}
	want = cleanRecipients(want)
	if _, err = tx.Exec(ctx, `DELETE FROM agent_market.agent_recipients WHERE org_id = $1 AND slug = $2 AND NOT (recipient = ANY($3::text[]))`,
		orgOr(org), slug, want); err != nil {
		return nil, nil, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO agent_market.agent_recipients (org_id, slug, recipient, updated_by)
		SELECT $1, $2, k, $4 FROM unnest($3::text[]) AS k ON CONFLICT (org_id, slug, recipient) DO NOTHING`, orgOr(org), slug, want, by); err != nil {
		return nil, nil, err
	}
	added, removed = moved(before, want)
	return added, removed, nil
}

// recipientEntries is one audit line for each recipient added or removed, naming both parties.
func recipientEntries(r *http.Request, scope, resourceType, resourceID string, added, removed []string, more map[string]any) []AuditEntry {
	var out []AuditEntry
	line := func(action, key string) {
		route, company := SplitRecipient(key)
		d := map[string]any{"recipient": key, "route": route, "company": company}
		for k, v := range more {
			d[k] = v
		}
		out = append(out, entry(r, "agent_market."+scope+"."+action, resourceType, resourceID, d))
	}
	for _, k := range added {
		line("recipient_allowed", k)
	}
	for _, k := range removed {
		line("recipient_removed", k)
	}
	return out
}

// runnerModels is what the runner last said it has: its own models and the gateway's catalogue.
func (a *API) runnerModels(ctx context.Context) RunnerModels {
	var rm RunnerModels
	if st, err := a.store.LatestRunner(ctx, a.cfg.RunnerStaleAfter); err == nil {
		rm = RunnerModelsFrom(st.Capabilities)
	}
	return rm
}

// adminPutOrgRecipients sets the organisation's own list — the ceiling for every agent. A recipient
// the catalogue does not name cannot be put on it: nobody types a key. Taking one off the list takes
// it away from every agent at once (an agent's own tick stays where it is, and counts for nothing).
func (a *API) adminPutOrgRecipients(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	var body struct {
		Recipients *[]string `json:"recipients"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body); err != nil || body.Recipients == nil {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	acc := accessOf(r)
	c, _ := caller(r)
	want := cleanRecipients(*body.Recipients)
	known := map[string]bool{}
	for _, o := range a.runnerModels(ctx).RecipientOptions() {
		known[o.Key] = true
	}
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		before, err := a.store.OrgRecipients(ctx, tx, acc.OrgID)
		if err != nil {
			return nil, err
		}
		was := map[string]bool{}
		for _, k := range before {
			was[k] = true
		}
		for _, k := range want {
			if !known[k] && !was[k] { // (one already on the list may stay while the runner is away)
				return nil, refusal{status: http.StatusBadRequest, code: "BAD_REQUEST", vars: []string{"code", "UNKNOWN_RECIPIENT"}}
			}
		}
		added, removed, err := a.store.setOrgRecipients(ctx, tx, acc.OrgID, want, normEmail(c.Email))
		if err != nil {
			return nil, err
		}
		return recipientEntries(r, "org", "agent_market_org", orgOr(acc.OrgID), added, removed, nil), nil
	})
	if err != nil {
		a.failChange(w, r, err, "recipients")
		return
	}
	now, err := a.store.OrgRecipients(ctx, a.store.Pool, acc.OrgID)
	if err != nil {
		a.fail(w, err, "recipients")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"org_recipients": now})
}
