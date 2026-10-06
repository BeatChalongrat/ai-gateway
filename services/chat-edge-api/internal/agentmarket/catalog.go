package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── The catalog a stranger reads (AM3) ──────────────────────────────────
//
// The organisation's catalog owner decides what people see (catalog_state:
// published, unpublished, disabled). Before installing, a person can open
// the agent's real outputs on its sample data (a catalog sample: the
// agent's own self-test, run once per released version by the runner under
// an install nobody owns), see roughly what a run costs and how long it
// takes, and read the data sheet: what it reads, which models see which
// part of the data and whether they run inside the organisation, where the
// files are kept and for how long.

// Catalog statuses.
const (
	StatusPublished   = "published"
	StatusUnpublished = "unpublished"
	StatusDisabled    = "disabled"
)

// CatalogState is one agent's standing in an organisation's catalog.
type CatalogState struct {
	Slug      string    `json:"slug"`
	Status    string    `json:"status"`
	Reason    string    `json:"reason"`
	ChangedBy string    `json:"changed_by"`
	ChangedAt time.Time `json:"changed_at"`
}

// CatalogStates maps slug → state for an organisation (no row = unpublished).
func (s *Store) CatalogStates(ctx context.Context, org string) (map[string]CatalogState, error) {
	rows, err := s.Pool.Query(ctx, `SELECT slug, status, reason, changed_by, changed_at FROM agent_market.catalog_state
		WHERE org_id = $1`, orgOr(org))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]CatalogState{}
	for rows.Next() {
		var c CatalogState
		if err := rows.Scan(&c.Slug, &c.Status, &c.Reason, &c.ChangedBy, &c.ChangedAt); err != nil {
			return nil, err
		}
		out[c.Slug] = c
	}
	return out, rows.Err()
}

// CatalogStateFor is one agent's state (unpublished when the organisation never decided).
func (s *Store) CatalogStateFor(ctx context.Context, org, slug string) (CatalogState, error) {
	c := CatalogState{Slug: slug, Status: StatusUnpublished}
	err := s.Pool.QueryRow(ctx, `SELECT status, reason, changed_by, changed_at FROM agent_market.catalog_state
		WHERE org_id = $1 AND slug = $2`, orgOr(org), slug).Scan(&c.Status, &c.Reason, &c.ChangedBy, &c.ChangedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return c, nil
	}
	return c, err
}

// SetCatalogState records a catalog decision. Disabling also stops the
// agent's runs that have not started (queued): they end cancelled with a
// notice in their thread; a running step finishes, a parked run stays
// parked (its answer is refused while the agent is off) — nothing that
// already happened is deleted.
//
// beforeCommit (AM7) runs last, inside the transaction, with how many queued
// rounds stopped; its error rolls the whole change back.
func (s *Store) SetCatalogState(ctx context.Context, org, slug, status, reason, by string, beforeCommit ...func(stopped int) error) (int, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	if _, err := tx.Exec(ctx, `INSERT INTO agent_market.catalog_state (org_id, slug, status, reason, changed_by)
		VALUES ($1, $2, $3, $4, $5) ON CONFLICT (org_id, slug) DO UPDATE SET status = EXCLUDED.status,
		reason = EXCLUDED.reason, changed_by = EXCLUDED.changed_by, changed_at = now()`, orgOr(org), slug, status, reason, by); err != nil {
		return 0, err
	}
	stopped := 0
	if status == StatusDisabled {
		rows, err := tx.Query(ctx, `UPDATE agent_market.agent_runs r SET status = 'cancelled', status_reason = 'agent_disabled',
			cancel_requested = true, finished_at = now(), updated_at = now()
			FROM agent_market.agent_installs i
			WHERE r.install_id = i.id AND i.org_id = $1 AND i.slug = $2 AND i.status = 'active' AND r.status = 'queued'
			RETURNING r.id, r.install_id, r.tenant_id, r.user_id, r.params`, orgOr(org), slug)
		if err != nil {
			return 0, err
		}
		type stoppedRun struct {
			id, install, tenant, user uuid.UUID
			params                    json.RawMessage
		}
		var list []stoppedRun
		for rows.Next() {
			var x stoppedRun
			if err := rows.Scan(&x.id, &x.install, &x.tenant, &x.user, &x.params); err != nil {
				rows.Close()
				return 0, err
			}
			list = append(list, x)
		}
		rows.Close()
		for _, x := range list {
			rid := x.id
			text := Text("ผู้ดูแลปิด agent นี้ไว้ชั่วคราว รอบนี้จึงยังไม่เริ่ม%s — ไฟล์ที่ส่งมายังอยู่ครบ เมื่อเปิดใช้อีกครั้งให้ส่งใหม่หรือพิมพ์ “ทำรอบใหม่”",
				Aside(reason))
			// AM11: said to each run's person in that run's language, not in the admin's
			if _, _, err := s.AddItem(InRunLang(ctx, Run{Params: x.params}), tx, x.install, x.tenant, x.user, &rid,
				Notice{Text: text, Tone: "warning", RunID: x.id.String(), Reason: "agent_disabled"}, nil); err != nil {
				return 0, err
			}
			// AM6: a round that never started delivered nothing, and is charged nothing
			if err := s.Settle(ctx, tx, x.id); err != nil {
				return 0, err
			}
			if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_thread_items SET payload = payload || '{"status":"cancelled","outcome":"cancelled"}'::jsonb,
				rev = nextval('agent_market.thread_rev'), updated_at = now()
				WHERE install_id = $1 AND kind = 'progress' AND payload->>'run_id' = $2`, x.install, x.id.String()); err != nil {
				return 0, err
			}
			stopped++
		}
	}
	for _, f := range beforeCommit {
		if err := f(stopped); err != nil {
			return 0, err
		}
	}
	return stopped, tx.Commit(ctx)
}

// agentGate refuses an action on a disabled agent (and an install of an
// unpublished one by someone who may not publish). true = go on.
func (a *API) agentGate(w http.ResponseWriter, r *http.Request, slug string, installing bool) bool {
	acc := accessOf(r)
	st, err := a.store.CatalogStateFor(r.Context(), acc.OrgID, slug)
	if err != nil {
		a.fail(w, err, "agent")
		return false
	}
	switch {
	case st.Status == StatusDisabled:
		reason := ""
		if st.Reason != "" {
			reason = ": " + st.Reason
		}
		contact := acc.Contact
		if validEmail(st.ChangedBy) {
			contact = Contact{Name: a.store.DisplayName(r.Context(), acc.OrgID, uuid.Nil, st.ChangedBy), Email: st.ChangedBy}
			if contact.Name == contact.Email {
				contact.Name = ""
			}
		}
		say(w, http.StatusConflict, "AGENT_DISABLED", "reason", reason, "contact", contact.LabelIn(langOfWriter(w)))
		return false
	case installing && st.Status == StatusUnpublished && !acc.Publisher():
		a.sayTo(w, r, http.StatusForbidden, "AGENT_UNPUBLISHED")
		return false
	}
	return true
}

// ── samples ──────────────────────────────────────────────────────────────

// SampleSummary is what the catalog card says about the agent's sample run.
type SampleSummary struct {
	RunID      uuid.UUID  `json:"run_id"`
	Version    string     `json:"version"`
	Status     string     `json:"status"`
	Outcome    string     `json:"outcome"`
	Reason     string     `json:"reason,omitempty"`
	Credits    float64    `json:"credits"`
	Seconds    *float64   `json:"seconds"`
	FinishedAt *time.Time `json:"finished_at,omitempty"`
}

// CatalogSampleFor is the newest sample of a package (its latest version
// first; an older version's sample stands in while the newest is not made yet).
func (s *Store) CatalogSampleFor(ctx context.Context, slug string) (SampleSummary, error) {
	var x SampleSummary
	var open bool
	err := s.Pool.QueryRow(ctx, `
		SELECT r.id, d.version, r.status, r.gate_open, r.status_reason, r.credits_cost::float8,
			CASE WHEN r.finished_at IS NOT NULL AND r.started_at IS NOT NULL
				THEN extract(epoch FROM r.finished_at - r.started_at) - r.awaiting_ms / 1000.0 END,
			r.finished_at
		FROM agent_market.catalog_samples c
		JOIN agent_market.agent_definitions d ON d.id = c.definition_id
		JOIN agent_market.agent_runs r ON r.id = c.run_id
		WHERE d.slug = $1
		ORDER BY (r.status = 'succeeded') DESC, string_to_array(d.version, '.')::int[] DESC, c.created_at DESC
		LIMIT 1`, slug).Scan(&x.RunID, &x.Version, &x.Status, &open, &x.Reason, &x.Credits, &x.Seconds, &x.FinishedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	x.Outcome = OutcomeOf(x.Status, open)
	return x, err
}

// CreateCatalogSample makes (or finds) the install nobody owns for a
// released version and queues its self-test as the catalog's sample. It
// replaces an earlier sample of the same version (an admin's "make it
// again"). The runner calls it for every released version without one.
func (s *Store) CreateCatalogSample(ctx context.Context, def Definition, bindings json.RawMessage, bindingVersion int) (Run, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return Run{}, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	var install uuid.UUID
	err = tx.QueryRow(ctx, `SELECT id FROM agent_market.agent_installs WHERE status = 'catalog' AND definition_id = $1
		ORDER BY created_at LIMIT 1`, def.ID).Scan(&install)
	if errors.Is(err, pgx.ErrNoRows) {
		title := def.Name
		if m, err := ParseManifest(def.Manifest); err == nil && m.ShortName != "" {
			title = m.ShortName
		}
		err = tx.QueryRow(ctx, `INSERT INTO agent_market.agent_installs (tenant_id, user_id, definition_id, slug, definition_version,
			checksum, title, capability_bindings, binding_version, status, org_id)
			VALUES ($1, $1, $2, $3, $4, $5, $6, $7, $8, 'catalog', 'default') RETURNING id`,
			uuid.Nil, def.ID, def.Slug, def.Version, def.Checksum, title, jsonOr(bindings, "{}"), bindingVersion).Scan(&install)
		if err == nil {
			if m, perr := ParseManifest(def.Manifest); perr == nil {
				rules := rulesOf(m)
				for k, v := range m.Install.Defaults {
					if !rules.known(k) {
						continue
					}
					if kept, _, problem := rules.check(k, v); problem.Empty() {
						if err = s.SetDefault(ctx, tx, install, Default{Key: k, Value: kept, Source: "install",
							Label: rules.label(LangOf(ctx), k), Note: T(LangOf(ctx), "ตัวอย่างในแคตตาล็อก")}); err != nil {
							break
						}
					}
				}
			}
		}
	}
	if err != nil {
		return Run{}, err
	}
	run, _, err := s.CreateRun(ctx, tx, Run{InstallID: install, TenantID: uuid.Nil, UserID: uuid.Nil, Kind: KindSelfTest,
		Message: T(LangOf(ctx), "ตัวอย่างในแคตตาล็อก"), DefinitionVersion: def.Version, Checksum: def.Checksum, EngineChecksum: def.EngineChecksum,
		BindingVersion: bindingVersion}, "")
	if err != nil {
		return run, err
	}
	if _, err := tx.Exec(ctx, `INSERT INTO agent_market.catalog_samples (definition_id, install_id, run_id) VALUES ($1, $2, $3)
		ON CONFLICT (definition_id) DO UPDATE SET run_id = EXCLUDED.run_id, install_id = EXCLUDED.install_id, created_at = now()`,
		def.ID, install, run.ID); err != nil {
		return run, err
	}
	return run, tx.Commit(ctx)
}

// MissingSamples lists released (active) versions — the newest of each
// package — that have no sample yet.
func (s *Store) MissingSamples(ctx context.Context) ([]Definition, error) {
	defs, err := s.ListDefinitions(ctx)
	if err != nil {
		return nil, err
	}
	var out []Definition
	for _, d := range defs {
		var n int
		if err := s.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.catalog_samples WHERE definition_id = $1`, d.ID).Scan(&n); err != nil {
			return nil, err
		}
		if n == 0 {
			out = append(out, d)
		}
	}
	return out, nil
}

// latestArtifacts keeps the newest version of each format.
func latestArtifacts(arts []Artifact) []Artifact {
	best := map[string]Artifact{}
	for _, x := range arts {
		k := x.Format + "|" + x.Kind
		if cur, ok := best[k]; !ok || x.Version > cur.Version {
			best[k] = x
		}
	}
	out := make([]Artifact, 0, len(best))
	for _, x := range best {
		out = append(out, x)
	}
	sort.Slice(out, func(i, j int) bool { return formatRank(out[i].Format) < formatRank(out[j].Format) })
	return out
}

func formatRank(f string) int {
	switch f {
	case "docx":
		return 0
	case "pptx":
		return 1
	case "xlsx":
		return 2
	case "pdf":
		return 3
	}
	return 4
}

func (a *API) catalogSample(w http.ResponseWriter, r *http.Request) {
	slug := chi.URLParam(r, "slug")
	ctx := r.Context()
	sm, err := a.store.CatalogSampleFor(ctx, slug)
	if errors.Is(err, ErrNotFound) {
		say(w, http.StatusNotFound, "NO_SAMPLE")
		return
	}
	if err != nil {
		a.fail(w, err, "sample")
		return
	}
	arts, err := a.store.RunArtifacts(ctx, sm.RunID)
	if err != nil {
		a.fail(w, err, "sample")
		return
	}
	files, err := a.store.RunFiles(ctx, sm.RunID)
	if err != nil {
		a.fail(w, err, "sample")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"sample": sm, "artifacts": nonNil(latestArtifacts(arts)), "files": nonNil(files)})
}

// sampleArtifact / sampleFile serve a catalog sample's outputs and inputs to
// anyone the organisation lets in — only rows of an install nobody owns.
func (a *API) sampleArtifact(w http.ResponseWriter, r *http.Request) {
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var art Artifact
	var key string
	err := a.store.Pool.QueryRow(r.Context(), `SELECT a.name, a.format, a.size, a.sha256, a.storage_key
		FROM agent_market.agent_output_artifacts a JOIN agent_market.agent_installs i ON i.id = a.install_id
		WHERE a.id = $1 AND i.status = 'catalog'`, id).Scan(&art.Name, &art.Format, &art.Size, &art.SHA256, &key)
	if errors.Is(err, pgx.ErrNoRows) {
		a.fail(w, ErrNotFound, "sample")
		return
	}
	if err != nil {
		a.fail(w, err, "sample")
		return
	}
	art.StorageKey = key
	a.serveArtifact(w, r, art)
}

func (a *API) sampleFile(w http.ResponseWriter, r *http.Request) {
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var name, key string
	err := a.store.Pool.QueryRow(r.Context(), `SELECT f.name, f.storage_key FROM agent_market.agent_files f
		JOIN agent_market.agent_installs i ON i.id = f.install_id WHERE f.id = $1 AND i.status = 'catalog'`, id).Scan(&name, &key)
	if errors.Is(err, pgx.ErrNoRows) {
		a.fail(w, ErrNotFound, "sample")
		return
	}
	if err != nil {
		a.fail(w, err, "sample")
		return
	}
	body, _, _, err := a.files.Download(r.Context(), key)
	if err != nil {
		a.fail(w, err, "file")
		return
	}
	defer body.Close()
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", contentDisposition(name))
	_, _ = io.Copy(w, body)
}

// serveArtifact streams one delivered file.
func (a *API) serveArtifact(w http.ResponseWriter, r *http.Request, art Artifact) {
	body, _, _, err := a.files.Download(r.Context(), art.StorageKey)
	if err != nil {
		a.fail(w, err, "artifact")
		return
	}
	defer body.Close()
	w.Header().Set("Content-Type", artifactMime(art))
	w.Header().Set("Content-Disposition", contentDisposition(art.Name))
	w.Header().Set("Content-Length", strconv.FormatInt(art.Size, 10))
	w.Header().Set("X-Content-SHA256", art.SHA256)
	_, _ = io.Copy(w, body)
}

// ── what a run of an agent costs and takes, at this organisation ─────────

// AgentStats are the organisation's own numbers for one agent (last 30 days,
// succeeded runs, the catalog's samples left out).
type AgentStats struct {
	Runs          int64    `json:"runs"`
	Users         int64    `json:"users"`
	MedianCredits *float64 `json:"median_credits"`
	MedianSeconds *float64 `json:"median_seconds"`
	PerDocument   *float64 `json:"per_document"`
	Documents     int64    `json:"documents"`
}

// AgentStatsFor measures one agent over the organisation's runs.
func (s *Store) AgentStatsFor(ctx context.Context, org, slug string, days int) (AgentStats, error) {
	var st AgentStats
	since := time.Now().Add(-time.Duration(days) * 24 * time.Hour)
	err := s.Pool.QueryRow(ctx, `
		SELECT count(*), count(DISTINCT r.user_id),
			percentile_cont(0.5) WITHIN GROUP (ORDER BY r.credits_spent)::float8,
			percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM r.finished_at - r.started_at) - r.awaiting_ms / 1000.0)::float8
		FROM agent_market.agent_runs r JOIN agent_market.agent_installs i ON i.id = r.install_id
		WHERE i.org_id = $1 AND i.slug = $2 AND i.status <> 'catalog' AND r.kind = 'run' AND r.status = 'succeeded'
			AND r.created_at >= $3 AND r.started_at IS NOT NULL AND r.finished_at IS NOT NULL`, orgOr(org), slug, since).
		Scan(&st.Runs, &st.Users, &st.MedianCredits, &st.MedianSeconds)
	if err != nil {
		return st, err
	}
	rows, err := s.Pool.Query(ctx, `
		SELECT st.output->'summary', r.credits_spent::float8 FROM agent_market.agent_run_steps st
		JOIN agent_market.agent_runs r ON r.id = st.run_id JOIN agent_market.agent_installs i ON i.id = r.install_id
		WHERE i.org_id = $1 AND i.slug = $2 AND i.status <> 'catalog' AND r.kind = 'run' AND r.status = 'succeeded'
			AND r.created_at >= $3 AND st.name = 'compute' AND st.status = 'succeeded'`, orgOr(org), slug, since)
	if err != nil {
		return st, err
	}
	defer rows.Close()
	var credits float64
	for rows.Next() {
		var raw json.RawMessage
		var c float64
		if err := rows.Scan(&raw, &c); err != nil {
			return st, err
		}
		var sum struct {
			Documents *int64 `json:"documents"`
			Claims    *int64 `json:"claims"`
		}
		if json.Unmarshal(raw, &sum) != nil {
			continue
		}
		switch {
		case sum.Documents != nil:
			st.Documents += *sum.Documents
			credits += c
		case sum.Claims != nil:
			st.Documents += *sum.Claims
			credits += c
		}
	}
	if st.Documents > 0 {
		v := credits / float64(st.Documents)
		st.PerDocument = &v
	}
	return st, rows.Err()
}

// ── the catalog itself ───────────────────────────────────────────────────

type catalogEntry struct {
	Slug         string         `json:"slug"`
	Version      string         `json:"version"`
	Name         string         `json:"name"`
	Manifest     shownManifest  `json:"manifest"` // AM11: in the language of the screen it is sent to
	InstallID    *uuid.UUID     `json:"install_id,omitempty"`
	SampleRunID  *uuid.UUID     `json:"sample_run_id,omitempty"`
	Status       string         `json:"status"`
	StatusReason string         `json:"status_reason,omitempty"`
	Sample       *SampleSummary `json:"sample,omitempty"`
	Stats        *AgentStats    `json:"stats,omitempty"`
	// AM6: what the agent's manifest says of itself (capability.go)
	Scope        string `json:"scope"`
	Jurisdiction string `json:"jurisdiction"`
}

// visible: a member sees published and disabled agents (a disabled one says
// so and offers nothing); a publisher also sees the unpublished ones.
func visible(acc Access, status string) bool {
	return status != StatusUnpublished || acc.Publisher()
}

func (a *API) entryFor(ctx context.Context, acc Access, d Definition, states map[string]CatalogState, installs map[string]uuid.UUID) catalogEntry {
	st, ok := states[d.Slug]
	if !ok {
		st = CatalogState{Status: StatusUnpublished}
	}
	e := catalogEntry{Slug: d.Slug, Version: d.Version, Name: d.Name, Manifest: shownManifest(d.Manifest), Status: st.Status, StatusReason: st.Reason,
		Scope: ScopeOrgSpecific}
	if m, err := ParseManifest(d.Manifest); err == nil {
		e.Name = m.NameIn(LangOf(ctx), d.Name) // AM11: an agent is named in the screen's language
		e.Scope, e.Jurisdiction = m.ScopeSaid(), m.Jurisdiction
	}
	if id, ok := installs[d.Slug]; ok {
		id := id
		e.InstallID = &id
	}
	if sm, err := a.store.CatalogSampleFor(ctx, d.Slug); err == nil {
		sm := sm
		e.Sample = &sm
		if sm.Status == RunSucceeded {
			rid := sm.RunID
			e.SampleRunID = &rid
		}
	}
	if stats, err := a.store.AgentStatsFor(ctx, acc.OrgID, d.Slug, 30); err == nil {
		e.Stats = &stats
	}
	return e
}

func (a *API) catalog(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	acc := accessOf(r)
	ctx := r.Context()
	defs, err := a.store.ListDefinitions(ctx)
	if err != nil {
		a.fail(w, err, "catalog")
		return
	}
	states, err := a.store.CatalogStates(ctx, acc.OrgID)
	if err != nil {
		a.fail(w, err, "catalog")
		return
	}
	installs, err := a.store.ListInstalls(ctx, c.TenantUUID, c.UserID)
	if err != nil {
		a.fail(w, err, "catalog")
		return
	}
	bySlug := map[string]uuid.UUID{}
	for _, i := range installs {
		if _, ok := bySlug[i.Slug]; !ok {
			bySlug[i.Slug] = i.ID
		}
	}
	out := []catalogEntry{}
	for _, d := range defs {
		st := StatusUnpublished
		if s, ok := states[d.Slug]; ok {
			st = s.Status
		}
		// an agent the person already installed stays visible to them (to open it)
		if _, mine := bySlug[d.Slug]; !visible(acc, st) && !mine {
			continue
		}
		out = append(out, a.entryFor(ctx, acc, d, states, bySlug))
	}
	writeJSON(w, http.StatusOK, map[string]any{"agents": out})
}

func (a *API) catalogItem(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	acc := accessOf(r)
	ctx := r.Context()
	d, err := a.store.DefinitionBySlug(ctx, chi.URLParam(r, "slug"))
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	states, err := a.store.CatalogStates(ctx, acc.OrgID)
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	installs, _ := a.store.ListInstalls(ctx, c.TenantUUID, c.UserID)
	bySlug := map[string]uuid.UUID{}
	for _, i := range installs {
		if _, ok := bySlug[i.Slug]; !ok {
			bySlug[i.Slug] = i.ID
		}
	}
	e := a.entryFor(ctx, acc, d, states, bySlug)
	if !visible(acc, e.Status) && e.InstallID == nil {
		a.fail(w, ErrNotFound, "agent")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"slug": d.Slug, "version": d.Version, "name": e.Name,
		"manifest": shownManifest(d.Manifest), "checksum": d.Checksum, "instructions": d.Instructions, "status": e.Status,
		"status_reason": e.StatusReason, "install_id": e.InstallID, "sample": e.Sample, "stats": e.Stats,
		"scope": e.Scope, "jurisdiction": e.Jurisdiction})
}

// ── the data sheet ───────────────────────────────────────────────────────

// Guide is the part of the manifest's "guide" block the platform reads
// (the web renders the whole block): exactly what each model is sent.
type Guide struct {
	ModelInputs map[string]string `json:"model_inputs"`
	Never       []string          `json:"never"`
}

// DatasheetModel is one model that sees part of the data.
type DatasheetModel struct {
	Capability string `json:"capability"`
	Sees       string `json:"sees"`
	Model      string `json:"model"`
	Role       string `json:"role"` // primary | fallback | vision
	SelfHosted bool   `json:"self_hosted"`
}

// Datasheet is the per-agent page procurement and the person clicking
// install both read, generated from the manifest and what the runner binds.
type Datasheet struct {
	Slug        string            `json:"slug"`
	Version     string            `json:"version"`
	Name        string            `json:"name"`
	Summary     string            `json:"summary"`
	Reads       []map[string]any  `json:"reads"`
	Limits      map[string]int    `json:"limits"`
	Produces    []map[string]any  `json:"produces"`
	Models      []DatasheetModel  `json:"models"`
	NoModel     []string          `json:"no_model"` // parts of the work no model sees
	Storage     map[string]any    `json:"storage"`
	Retention   []map[string]any  `json:"retention"`
	Isolation   []string          `json:"isolation"`
	Permissions map[string]any    `json:"permissions"`
	Never       []string          `json:"never"`
	Cannot      string            `json:"cannot"`
	Status      string            `json:"status"`
	Runner      map[string]any    `json:"runner"`
	Generated   time.Time         `json:"generated_at"`
	Notes       map[string]string `json:"notes,omitempty"`
	// AM6: what the agent's manifest says of itself (capability.go)
	Scope        string `json:"scope"`
	Jurisdiction string `json:"jurisdiction"`
}

// BuildDatasheet assembles the sheet (a function of its inputs, for tests).
// AM11: the platform's own lines of it are in l — the language of the request
// that asks for the sheet; what the manifest says of the agent is the
// package's, in the language the package was written in.
func BuildDatasheet(l Lang, d Definition, caps map[string]Binding, status string, now time.Time) (Datasheet, error) {
	// AM11: the sheet is built from the manifest as a screen in l reads it — the package's own words
	// in the English the package gives for them, where it gives one
	shown := ManifestIn(l, d.Manifest)
	m, err := ParseManifest(shown)
	if err != nil {
		return Datasheet{}, err
	}
	var extra struct {
		Guide *Guide `json:"guide"`
	}
	_ = json.Unmarshal(shown, &extra)
	ds := Datasheet{Slug: d.Slug, Version: d.Version, Name: m.NameIn(l, d.Name), Summary: m.Summary, Status: status, Generated: now,
		Scope: m.ScopeSaid(), Jurisdiction: m.Jurisdiction,
		Limits: map[string]int{"max_file_mb": m.Limits.MaxFileMB, "max_files": m.Limits.MaxFiles},
		Cannot: m.Cannot, Models: []DatasheetModel{}, NoModel: []string{}, Never: []string{},
		Permissions: map[string]any{"default": m.Permissions.Default, "denied": nonNil(m.Permissions.Denied)}}
	for _, in := range m.Inputs {
		ds.Reads = append(ds.Reads, map[string]any{"role": in.Role, "label": in.Label, "note": in.Note, "required": in.Required,
			"accept": nonNil(in.Accept)})
	}
	for _, o := range m.Outputs {
		ds.Produces = append(ds.Produces, map[string]any{"format": o.Format, "label": o.Label, "default": o.Default})
	}
	inputs := map[string]string{}
	if extra.Guide != nil {
		for k, v := range extra.Guide.ModelInputs {
			inputs[k] = v
		}
		ds.Never = nonNil(extra.Guide.Never)
	}
	uses := func(capName string) bool {
		for _, c := range append(append([]string{}, m.Capabilities.Required...), m.Capabilities.Optional...) {
			if c == capName {
				return true
			}
		}
		return false
	}
	// The text model: an agent whose narrative is written by a model sends it
	// the computed table (and a person's typed question or worded answer) —
	// its primary and its fallback. Any other agent sends a person's typed
	// words (with that run's table or card) only to a model inside the
	// organisation, or to none (the runner's textModels).
	typed := T(l, " · และข้อความที่คุณพิมพ์ถามเกี่ยวกับตัวเลข หรือพิมพ์ตอบการ์ดคำถามเป็นประโยค")
	textDetail := func() (primary, fallback string, self map[string]bool) {
		var det struct {
			Primary    string          `json:"primary"`
			Fallback   string          `json:"fallback"`
			SelfHosted map[string]bool `json:"self_hosted"`
		}
		if b, ok := caps["model.text"]; ok {
			_ = json.Unmarshal(b.Detail, &det)
		}
		return det.Primary, det.Fallback, det.SelfHosted
	}
	if uses("model.text") && m.Narrative != "rules" {
		sees := inputs["model.text"]
		if sees == "" {
			sees = T(l, "ตารางตัวเลขที่คำนวณแล้วของรอบนั้น (ไม่ใช่ไฟล์ต้นฉบับ) เพื่อเขียนคำบรรยายในรายงาน")
		}
		sees += typed
		if b, ok := caps["model.text"]; ok {
			var det struct {
				Primary    string          `json:"primary"`
				Fallback   string          `json:"fallback"`
				SelfHosted map[string]bool `json:"self_hosted"`
			}
			_ = json.Unmarshal(b.Detail, &det)
			if det.Primary != "" {
				ds.Models = append(ds.Models, DatasheetModel{Capability: "model.text", Sees: sees, Model: det.Primary, Role: "primary",
					SelfHosted: det.SelfHosted[det.Primary]})
			}
			if det.Fallback != "" && det.Fallback != det.Primary {
				ds.Models = append(ds.Models, DatasheetModel{Capability: "model.text", Sees: sees, Model: det.Fallback, Role: "fallback",
					SelfHosted: det.SelfHosted[det.Fallback]})
			}
		}
	} else {
		ds.NoModel = append(ds.NoModel, T(l, "คำบรรยายและสรุปในไฟล์เขียนด้วยกฎของ agent ไม่มีโมเดลภาษาใดเขียนหรือเห็นเอกสารของคุณ"))
		primary, fallback, self := textDetail()
		inside := false
		for _, x := range []string{primary, fallback} {
			if x != "" && self[x] {
				inside = true
				ds.Models = append(ds.Models, DatasheetModel{Capability: "model.text", Model: x, Role: "questions", SelfHosted: true,
					Sees: T(l, "เฉพาะเมื่อคุณพิมพ์ถามเกี่ยวกับตัวเลข หรือพิมพ์ตอบการ์ดคำถามเป็นประโยค: ข้อความนั้นกับตารางหรือการ์ดของรอบนั้น — ส่งให้โมเดลในองค์กรเท่านั้น")})
				break
			}
		}
		if !inside {
			ds.NoModel = append(ds.NoModel, T(l, "ข้อความที่คุณพิมพ์ไม่ถูกส่งให้โมเดลใด (ไม่มีโมเดลในองค์กร) — agent อ่านคำตอบจากคำสำคัญ หรือใช้ค่าแนะนำ"))
		}
	}
	if uses("model.vision") {
		sees := inputs["model.vision"]
		if sees == "" {
			sees = T(l, "ภาพหน้าเอกสารที่เป็นสแกนหรือรูปถ่ายเท่านั้น")
		}
		if b, ok := caps["model.vision"]; ok {
			var det struct {
				Model      string `json:"model"`
				SelfHosted bool   `json:"self_hosted"`
			}
			_ = json.Unmarshal(b.Detail, &det)
			if det.Model == "" {
				det.Model = b.Version
			}
			ds.Models = append(ds.Models, DatasheetModel{Capability: "model.vision", Sees: sees, Model: det.Model, Role: "vision",
				SelfHosted: det.SelfHosted})
		} else {
			ds.NoModel = append(ds.NoModel, T(l, "ตอนนี้ไม่มีโมเดลอ่านภาพที่ทำงานในองค์กร สแกนและรูปถ่ายจึงอ่านด้วย OCR บนเครื่องขององค์กรเท่านั้น"))
		}
	}
	ds.NoModel = append(ds.NoModel, T(l, "ตัวเลขทุกตัวคำนวณด้วยสคริปต์ ไม่มีโมเดลใดเป็นผู้กำหนดตัวเลข"))
	// Where the files are kept and for how long: the runner says (heartbeat).
	storage := map[string]any{"backend": "local", "prefix": "agent-market/"}
	retention := map[string]float64{"staged_hours": 30 * 24, "scratch_days": 7, "removed_days": 30}
	if b, ok := caps["platform.storage"]; ok {
		var det map[string]any
		if json.Unmarshal(b.Detail, &det) == nil {
			if v, ok := det["backend"].(string); ok && v != "" {
				storage["backend"] = v
			}
			for _, k := range []string{"staged_hours", "scratch_days", "removed_days"} {
				if v, ok := det[k].(float64); ok && v > 0 {
					retention[k] = v
				}
			}
		}
	}
	ds.Storage = storage
	// AM4: an accounting archive — a finished round's evidence for the
	// organisation's years (ApplyOrg sets them), everything else short.
	ds.Retention = []map[string]any{
		{"what": T(l, "ไฟล์ที่ส่งเข้าและไฟล์ผลลัพธ์ของรอบที่สำเร็จ (หลักฐานว่าตัวเลขคำนวณจากไฟล์ใด)"), "keep": float64(DefaultRetentionYears),
			"unit": "years"},
		{"what": T(l, "ข้อมูลของรอบที่ล้มเหลวหรือยกเลิก (ไฟล์ที่ส่งเข้าและข้อมูลชั่วคราว)"), "keep": retention["scratch_days"], "unit": "days"},
		{"what": T(l, "ไฟล์ที่อัปโหลดแต่ไม่ได้ใช้ในรอบใด"), "keep": retention["staged_hours"] / 24, "unit": "days"},
		{"what": T(l, "แชทและการตั้งค่าของ agent ที่ถอนการติดตั้งแล้ว (หลักฐานของรอบที่สำเร็จเก็บต่อตามข้อแรก)"),
			"keep": retention["removed_days"], "unit": "days"},
		{"what": T(l, "เมื่อลบบัญชีผู้ใช้: แชทและการตั้งค่าส่วนตัวถูกลบ ส่วนหลักฐานของรอบที่สำเร็จเป็นขององค์กร เก็บต่อตามข้อแรก "+
			"การลบก่อนกำหนดเป็นการตัดสินใจของผู้ดูแล"), "keep": 0, "unit": "account_deletion"},
	}
	if days := int(m.IndexRetention().Hours() / 24); days > 0 {
		ds.Retention = append(ds.Retention, map[string]any{"what": T(l, "สรุปเอกสารสำหรับตรวจเอกสารซ้ำ (เลขผู้เสียภาษี เลขที่ วันที่ ยอดรวม)"),
			"keep": float64(days), "unit": "days"})
	}
	ds.Isolation = []string{
		T(l, "ทุกขั้นตอนทำงานในกล่องแยก (sandbox) ที่ไม่มีเครือข่าย ออกอินเทอร์เน็ตไม่ได้"),
		T(l, "ตัวแปลง PDF อยู่ในเครือข่ายภายในที่ไม่มีทางออกไปภายนอก"),
		T(l, "agent ใช้กุญแจ API ของตัวเอง แยกจากแชท และมีงบเครดิตของตัวเอง"),
	}
	ds.Runner = map[string]any{}
	return ds, nil
}

// ApplyOrg fills in the organisation's own decisions (AM4): how many years a
// finished round's evidence is kept, and — while this agent's documents may
// not leave (AM9: the organisation's switch and the agent's own) — that a
// model outside the organisation is not used at all.
func (ds *Datasheet) ApplyOrg(l Lang, o OrgSettings, e Egress) {
	years := o.RetentionYears
	if years <= 0 {
		years = DefaultRetentionYears
	}
	for _, r := range ds.Retention {
		if r["unit"] == "years" {
			r["keep"] = float64(years)
		}
	}
	if ds.Notes == nil {
		ds.Notes = map[string]string{}
	}
	ds.Notes["not_archive"] = T(l, "ที่นี่ไม่ใช่ที่เก็บเอกสารของบริษัท — ต้นฉบับยังต้องเก็บตามที่บริษัทเก็บอยู่ ระบบเก็บสำเนาและหลักฐานว่าตัวเลขคำนวณจากไฟล์ใด")
	if !e.Effective {
		why := T(l, "องค์กรไม่อนุญาตให้ส่งข้อมูลออกนอกองค์กร")
		if e.Org {
			why = T(l, "agent นี้อ่านข้อมูลบัญชีและไม่ส่งเอกสารออกนอกองค์กร")
			if !e.AgentDefault {
				why = T(l, "ผู้ดูแลตั้งให้ agent นี้ไม่ส่งเอกสารออกนอกองค์กร")
			}
		}
		kept := ds.Models[:0]
		for _, m := range ds.Models {
			if !m.SelfHosted {
				ds.NoModel = append(ds.NoModel, T(l, "โมเดล ")+m.Model+T(l, " อยู่นอกองค์กร จึงไม่ใช้ — ")+why+" "+
					T(l, "(ถ้าโมเดลในองค์กรไม่ตอบ คำบรรยายจะเขียนด้วยกฎของ agent แทน)"))
				continue
			}
			kept = append(kept, m)
		}
		ds.Models = kept
	}
}

func (a *API) datasheet(w http.ResponseWriter, r *http.Request) {
	acc := accessOf(r)
	ctx := r.Context()
	d, err := a.store.DefinitionBySlug(ctx, chi.URLParam(r, "slug"))
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	st, err := a.store.CatalogStateFor(ctx, acc.OrgID, d.Slug)
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	caps := map[string]Binding{}
	runner := map[string]any{"healthy": false}
	if rs, err := a.store.LatestRunner(ctx, a.cfg.RunnerStaleAfter); err == nil {
		_ = json.Unmarshal(rs.Capabilities, &caps)
		runner = map[string]any{"healthy": rs.Healthy, "version": rs.Version, "seen_at": rs.SeenAt}
	}
	l := langOfWriter(w)
	ds, err := BuildDatasheet(l, d, caps, st.Status, time.Now())
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	if o, err := a.store.OrgSettingsFor(ctx, acc.OrgID); err == nil {
		e := EgressOf(false, Manifest{}, nil)
		if m, err := ParseManifest(d.Manifest); err == nil {
			if got, err := a.store.EgressFor(ctx, acc.OrgID, d.Slug, m); err == nil {
				e = got
			}
		}
		ds.ApplyOrg(l, o, e)
	}
	ds.Runner = runner
	writeJSON(w, http.StatusOK, ds)
}

// trimReason keeps a disable reason to one short line.
func trimReason(s string) string {
	s = strings.Join(strings.Fields(s), " ")
	if r := []rune(s); len(r) > 200 {
		s = string(r[:200])
	}
	return s
}
