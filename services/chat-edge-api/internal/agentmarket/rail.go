package agentmarket

import (
	"encoding/json"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"
)

// ── The left rail (AM3) ──────────────────────────────────────────────────
//
// "Agent ของฉัน" has to survive twenty installs: the web shows pinned agents
// first, then the most recently used, about five in all, and "ดูทั้งหมด" for
// the rest. What must never hide is a run waiting for an answer: every row
// carries its count of runs awaiting the person, and the response carries
// the total for the section's badge (shown even when it is collapsed).

// RailInstall is one installed agent as the rail shows it.
type RailInstall struct {
	ID         uuid.UUID  `json:"id"`
	Slug       string     `json:"slug"`
	Title      string     `json:"title"`
	Icon       string     `json:"icon"`
	Version    string     `json:"version"`
	PinnedAt   *time.Time `json:"pinned_at"`
	LastUsedAt *time.Time `json:"last_used_at"`
	Waiting    int        `json:"waiting"`
	Disabled   bool       `json:"disabled"`
	Look
}

func (a *API) rail(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	ctx := r.Context()
	rows, err := a.store.Pool.Query(ctx, `
		SELECT i.id, i.slug, i.title, COALESCE(d.manifest->>'icon', ''), i.definition_version, i.pinned_at,
			GREATEST(i.last_opened_at, (SELECT max(r.created_at) FROM agent_market.agent_runs r
				WHERE r.install_id = i.id AND r.kind IN ('run', 'self_test'))),
			(SELECT count(*) FROM agent_market.agent_runs r
				WHERE r.install_id = i.id AND r.kind IN ('run', 'self_test') AND r.status = 'awaiting_input'
				  AND r.status_reason <> 'model_unreachable')::int, -- AM15: not a run that waits for its model
			COALESCE(cs.status, '') = 'disabled', `+lookSQL+`
		FROM agent_market.agent_installs i
		JOIN agent_market.agent_definitions d ON d.id = i.definition_id
		LEFT JOIN agent_market.catalog_state cs ON cs.org_id = i.org_id AND cs.slug = i.slug
		WHERE i.tenant_id = $1 AND i.user_id = $2 AND i.status = 'active'
		ORDER BY i.created_at DESC`, c.TenantUUID, c.UserID)
	if err != nil {
		a.fail(w, err, "installs")
		return
	}
	defer rows.Close()
	out := []RailInstall{}
	total := 0
	for rows.Next() {
		var x RailInstall
		var icon, accent json.RawMessage
		if err := rows.Scan(&x.ID, &x.Slug, &x.Title, &x.Icon, &x.Version, &x.PinnedAt, &x.LastUsedAt, &x.Waiting, &x.Disabled, &icon, &accent); err != nil {
			a.fail(w, err, "installs")
			return
		}
		x.Title = AgentNameIn(langOfWriter(w), x.Slug, x.Title) // AM14: the agent's name as this screen reads it
		x.Look = scanLook(icon, accent)
		total += x.Waiting
		out = append(out, x)
	}
	if err := rows.Err(); err != nil {
		a.fail(w, err, "installs")
		return
	}
	prefs := json.RawMessage(`{}`)
	_ = a.store.Pool.QueryRow(ctx, `SELECT prefs FROM agent_market.user_prefs WHERE user_id = $1`, c.UserID).Scan(&prefs)
	var shared int
	_ = a.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.run_shares WHERE recipient_email = $1 AND revoked_at IS NULL
		AND (expires_at IS NULL OR expires_at > now()) AND last_opened_at IS NULL`, normEmail(c.Email)).Scan(&shared)
	writeJSON(w, http.StatusOK, map[string]any{"installs": out, "waiting_total": total, "prefs": prefs, "shared_unopened": shared})
}

// putPrefs merges the person's rail preferences ({"rail_collapsed": true}).
func (a *API) putPrefs(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	var body map[string]json.RawMessage
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	// only the keys the web keeps here
	clean := map[string]json.RawMessage{}
	for _, k := range []string{"rail_collapsed", "rail_show_all"} {
		if v, ok := body[k]; ok && (string(v) == "true" || string(v) == "false") {
			clean[k] = v
		}
	}
	b, _ := json.Marshal(clean)
	var prefs json.RawMessage
	if err := a.store.Pool.QueryRow(r.Context(), `INSERT INTO agent_market.user_prefs (user_id, prefs) VALUES ($1, $2)
		ON CONFLICT (user_id) DO UPDATE SET prefs = agent_market.user_prefs.prefs || EXCLUDED.prefs, updated_at = now()
		RETURNING prefs`, c.UserID, b).Scan(&prefs); err != nil {
		a.fail(w, err, "prefs")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"prefs": prefs})
}

func (a *API) setPin(w http.ResponseWriter, r *http.Request, on bool) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	var at *time.Time
	if err := a.store.Pool.QueryRow(r.Context(), `UPDATE agent_market.agent_installs
		SET pinned_at = CASE WHEN $2 THEN COALESCE(pinned_at, now()) ELSE NULL END WHERE id = $1 RETURNING pinned_at`,
		in.ID, on).Scan(&at); err != nil {
		a.fail(w, err, "install")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"pinned_at": at})
}

func (a *API) pin(w http.ResponseWriter, r *http.Request)   { a.setPin(w, r, true) }
func (a *API) unpin(w http.ResponseWriter, r *http.Request) { a.setPin(w, r, false) }

// opened marks the install as used now (the rail's "recently used").
func (a *API) opened(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	if _, err := a.store.Pool.Exec(r.Context(), `UPDATE agent_market.agent_installs SET last_opened_at = now() WHERE id = $1`, in.ID); err != nil {
		a.fail(w, err, "install")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}
