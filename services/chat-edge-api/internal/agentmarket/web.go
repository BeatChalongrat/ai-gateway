package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── An agent that works on the web (AM12) ────────────────────────────────
//
// Until AM12 every agent worked from files a person sent. An agent whose
// manifest says "starts_from": "request" works from what a person types, and
// one that declares "web" has the web searched for it and pages captured as
// evidence. The control plane does none of that: the runner searches (through
// the model gateway's own search endpoint, with its own key) and has pages
// captured by the fetcher, a service of its own that holds no credential.
// What lives here is what a person and an admin are told and may set:
//
//   - the door: a round of such an agent starts only when the agent may reach
//     outside the organisation (the per-agent switch of AM9 — off until an
//     admin turns it on, for this kind of agent too), and when the runner has
//     a fetcher. Otherwise nothing starts, and the thread says why in words;
//   - an admin's two lists and one policy. There is no list of sites an agent
//     may use — it chooses its own. The lists point the other way: hosts that
//     are never opened, and the organisation's own suppliers (told to a
//     round, never a limit on it);
//   - the record: every search and every address a round tried to open, with
//     what came of it; and the stored pages themselves, served to the round's
//     owner as inert text and pictures.

// WebSpec is what a manifest says of its use of the web.
type WebSpec struct {
	// Prompts are the package's own files a model is given for each step it has
	// a judgement to make in: reading the request ("brief"), writing searches
	// ("plan"), choosing which results to open ("select"), reading a stored
	// page ("read"). Nothing of them is in the platform's code.
	Prompts map[string]string `json:"prompts"`
	Caps    WebCaps           `json:"caps"`
}

// WebCaps is the most one round may use. A round that reaches one delivers
// what it has and says so.
type WebCaps struct {
	Searches     int `json:"searches"`
	Pages        int `json:"pages"`
	PagesPerHost int `json:"pages_per_host"`
	Seconds      int `json:"seconds"`
	SourceMB     int `json:"source_mb"`
	Results      int `json:"results"`
}

// The most any agent's round may use, whatever its manifest asks for.
var webCeiling = WebCaps{Searches: 8, Pages: 10, PagesPerHost: 3, Seconds: 900, SourceMB: 10, Results: 10}

// FromRequest: a round of this agent starts from what a person types.
func (m Manifest) FromRequest() bool { return m.StartsFrom == "request" }

// UsesWeb: the agent has the web searched and pages captured for it.
func (m Manifest) UsesWeb() bool { return m.Web != nil }

func capOf(asked, ceiling, fallback int) int {
	if asked <= 0 {
		asked = fallback
	}
	if asked > ceiling {
		asked = ceiling
	}
	return asked
}

// WebCapsOf is what a round of this agent may use in an organisation: the
// agent's own caps under the platform's ceiling, lowered by the admin's policy.
func WebCapsOf(m Manifest, p WebPolicy) WebCaps {
	var c WebCaps
	if m.Web != nil {
		c = m.Web.Caps
	}
	c = WebCaps{Searches: capOf(c.Searches, webCeiling.Searches, 4), Pages: capOf(c.Pages, webCeiling.Pages, 8),
		PagesPerHost: capOf(c.PagesPerHost, webCeiling.PagesPerHost, 3), Seconds: capOf(c.Seconds, webCeiling.Seconds, 300),
		SourceMB: capOf(c.SourceMB, webCeiling.SourceMB, 10), Results: capOf(c.Results, webCeiling.Results, 8)}
	lower := func(have int, set *int) int {
		if set != nil && *set < have {
			return *set
		}
		return have
	}
	c.Searches, c.Pages = lower(c.Searches, p.MaxSearches), lower(c.Pages, p.MaxPages)
	c.Seconds, c.SourceMB = lower(c.Seconds, p.MaxSeconds), lower(c.SourceMB, p.MaxSourceMB)
	return c
}

// WebPolicy is an organisation's own limits (nil = the agent's own cap).
type WebPolicy struct {
	MaxSearches    *int      `json:"max_searches"`
	MaxPages       *int      `json:"max_pages"`
	MaxSeconds     *int      `json:"max_seconds"`
	MaxSourceMB    *int      `json:"max_source_mb"`
	UnusedKeepDays int       `json:"unused_keep_days"`
	UpdatedBy      string    `json:"updated_by"`
	UpdatedAt      time.Time `json:"updated_at"`
}

// DefaultUnusedKeepDays: how long the stored page of a source that did not
// become a candidate is kept.
const DefaultUnusedKeepDays = 7

// DenyHost is one host an admin said is never opened.
type DenyHost struct {
	Host    string    `json:"host"`
	Note    string    `json:"note"`
	AddedBy string    `json:"added_by"`
	AddedAt time.Time `json:"added_at"`
}

// Supplier is one of the organisation's own suppliers.
type Supplier struct {
	ID        uuid.UUID `json:"id"`
	Name      string    `json:"name"`
	TaxID     string    `json:"tax_id"`
	Website   string    `json:"website"`
	Note      string    `json:"note"`
	AddedBy   string    `json:"added_by"`
	UpdatedAt time.Time `json:"updated_at"`
}

// WebCall is one search or one fetch of a round.
type WebCall struct {
	OrgID       string     `json:"-"`
	InstallID   uuid.UUID  `json:"-"`
	RunID       uuid.UUID  `json:"run_id"`
	Kind        string     `json:"kind"`
	StepKey     string     `json:"-"`
	Source      string     `json:"source"`
	Host        string     `json:"host"`
	URL         string     `json:"url"`
	Outcome     string     `json:"outcome"`
	Reason      string     `json:"reason"`
	HTTPStatus  *int       `json:"http_status,omitempty"`
	Bytes       int64      `json:"bytes"`
	Results     int        `json:"results"`
	Provider    string     `json:"provider"`
	Credits     float64    `json:"credits"`
	StartedAt   time.Time  `json:"started_at"`
	DurationMS  int        `json:"duration_ms"`
	SnapshotSHA string     `json:"snapshot_sha256"`
	BlobPrefix  string     `json:"-"`
	Used        *bool      `json:"used,omitempty"`
	PurgeAfter  *time.Time `json:"purge_after,omitempty"`
	PurgedAt    *time.Time `json:"purged_at,omitempty"`
}

var hostShape = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$`)

// HostOf reads a host out of what an admin typed: a host, or an address of a page on it.
func HostOf(typed string) (string, bool) {
	s := strings.ToLower(strings.TrimSpace(typed))
	if s == "" {
		return "", false
	}
	if !strings.Contains(s, "://") {
		s = "http://" + s
	}
	u, err := url.Parse(s)
	if err != nil {
		return "", false
	}
	h := strings.TrimSuffix(u.Hostname(), ".")
	h = strings.TrimPrefix(h, "www.")
	if len(h) > 253 || !hostShape.MatchString(h) {
		return "", false
	}
	// a site's name ends in a label with a letter in it: four numbers with dots between them are an
	// address, not a name
	if last := h[strings.LastIndex(h, ".")+1:]; !strings.ContainsAny(last, "abcdefghijklmnopqrstuvwxyz") {
		return "", false
	}
	return h, true
}

// HostDenied: the host is one an admin said is never opened, or is under one.
func HostDenied(host string, deny []DenyHost) bool {
	h := strings.TrimSuffix(strings.ToLower(host), ".")
	for _, d := range deny {
		if h == d.Host || strings.HasSuffix(h, "."+d.Host) {
			return true
		}
	}
	return false
}

// ── store ────────────────────────────────────────────────────────────────

// WebPolicyFor is an organisation's policy (the defaults when it has set none).
func (s *Store) WebPolicyFor(ctx context.Context, q Querier, org string) (WebPolicy, error) {
	p := WebPolicy{UnusedKeepDays: DefaultUnusedKeepDays}
	err := q.QueryRow(ctx, `SELECT max_searches, max_pages, max_seconds, max_source_mb, unused_keep_days, updated_by, updated_at
		FROM agent_market.web_policy WHERE org_id = $1`, orgOr(org)).
		Scan(&p.MaxSearches, &p.MaxPages, &p.MaxSeconds, &p.MaxSourceMB, &p.UnusedKeepDays, &p.UpdatedBy, &p.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return p, nil
	}
	return p, err
}

// DenyHosts lists the hosts an admin said are never opened.
func (s *Store) DenyHosts(ctx context.Context, org string) ([]DenyHost, error) {
	rows, err := s.Pool.Query(ctx, `SELECT host, note, added_by, added_at FROM agent_market.web_deny_hosts
		WHERE org_id = $1 ORDER BY host`, orgOr(org))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []DenyHost{}
	for rows.Next() {
		var d DenyHost
		if err := rows.Scan(&d.Host, &d.Note, &d.AddedBy, &d.AddedAt); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// Suppliers lists the organisation's own suppliers.
func (s *Store) Suppliers(ctx context.Context, org string) ([]Supplier, error) {
	rows, err := s.Pool.Query(ctx, `SELECT id, name, tax_id, website, note, added_by, updated_at FROM agent_market.org_suppliers
		WHERE org_id = $1 ORDER BY name`, orgOr(org))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Supplier{}
	for rows.Next() {
		var x Supplier
		if err := rows.Scan(&x.ID, &x.Name, &x.TaxID, &x.Website, &x.Note, &x.AddedBy, &x.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

// RecordWebCall keeps one search or fetch of a round. Written once per step:
// a resumed round that reads the step back writes nothing again.
func (s *Store) RecordWebCall(ctx context.Context, c WebCall) error {
	if c.StartedAt.IsZero() {
		c.StartedAt = time.Now() // a call with no time of its own happened now, not in the year 1
	}
	_, err := s.Pool.Exec(ctx, `INSERT INTO agent_market.web_calls (org_id, install_id, run_id, kind, step_key, source, host, url,
		outcome, reason, http_status, bytes, results, provider, credits, started_at, duration_ms, snapshot_sha256, blob_prefix)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
		ON CONFLICT (run_id, step_key) DO NOTHING`,
		orgOr(c.OrgID), c.InstallID, c.RunID, c.Kind, c.StepKey, c.Source, c.Host, TrimText(c.URL, 2000), c.Outcome, c.Reason,
		c.HTTPStatus, c.Bytes, c.Results, c.Provider, c.Credits, c.StartedAt, c.DurationMS, c.SnapshotSHA, c.BlobPrefix)
	return err
}

// RunWebCalls lists a run's searches and fetches, in the order they were made.
func (s *Store) RunWebCalls(ctx context.Context, run uuid.UUID) ([]WebCall, error) {
	rows, err := s.Pool.Query(ctx, `SELECT run_id, kind, step_key, source, host, url, outcome, reason, http_status, bytes, results,
		provider, credits::float8, started_at, duration_ms, snapshot_sha256, blob_prefix, used, purge_after, purged_at
		FROM agent_market.web_calls WHERE run_id = $1 ORDER BY started_at, id`, run)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []WebCall{}
	for rows.Next() {
		var c WebCall
		if err := rows.Scan(&c.RunID, &c.Kind, &c.StepKey, &c.Source, &c.Host, &c.URL, &c.Outcome, &c.Reason, &c.HTTPStatus, &c.Bytes,
			&c.Results, &c.Provider, &c.Credits, &c.StartedAt, &c.DurationMS, &c.SnapshotSHA, &c.BlobPrefix, &c.Used, &c.PurgeAfter,
			&c.PurgedAt); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// MarkWebUsed says which of a run's captured sources became candidates. The
// stored page of one that did is the run's evidence and is kept with the run;
// one that did not is deleted after keepDays.
func (s *Store) MarkWebUsed(ctx context.Context, run uuid.UUID, used []string, keepDays int) error {
	if keepDays <= 0 {
		keepDays = DefaultUnusedKeepDays
	}
	if used == nil {
		used = []string{}
	}
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.web_calls SET used = (source = ANY($2)),
		purge_after = CASE WHEN source = ANY($2) THEN NULL
			ELSE COALESCE(purge_after, started_at + make_interval(days => $3)) END
		WHERE run_id = $1 AND kind = 'fetch' AND outcome = 'captured' AND purged_at IS NULL`, run, used, keepDays)
	return err
}

// PurgeableWebCalls are stored pages whose time has come.
func (s *Store) PurgeableWebCalls(ctx context.Context, limit int) ([]WebCall, error) {
	rows, err := s.Pool.Query(ctx, `SELECT run_id, step_key, blob_prefix FROM agent_market.web_calls
		WHERE purge_after IS NOT NULL AND purge_after < now() AND purged_at IS NULL AND blob_prefix <> '' LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []WebCall
	for rows.Next() {
		var c WebCall
		if err := rows.Scan(&c.RunID, &c.StepKey, &c.BlobPrefix); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// MarkWebPurged records that a stored page is gone.
func (s *Store) MarkWebPurged(ctx context.Context, run uuid.UUID, stepKey string) error {
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.web_calls SET purged_at = now() WHERE run_id = $1 AND step_key = $2`, run, stepKey)
	return err
}

// ── the door ─────────────────────────────────────────────────────────────

// WebState is whether a round of an agent that works on the web can start
// now, and — when it cannot — why, as a person reads it.
type WebState struct {
	Uses bool `json:"uses"`
	Open bool `json:"open"`
	// Search: the runner has a search provider. Without one a round opens only
	// the links the person sends.
	Search bool       `json:"search"`
	Why    string     `json:"why,omitempty"`
	Words  PersonText `json:"words"`
}

// The names of the pages a web address is found in a message by.
var linkInText = regexp.MustCompile(`https?://[^\s<>"'“”]+`)

// LinksIn are the web addresses a person typed.
func LinksIn(text string) []string {
	var out []string
	for _, l := range linkInText.FindAllString(text, 8) {
		out = append(out, strings.TrimRight(l, ".,;:)]}"))
	}
	return out
}

// webState works out the door for an install.
func (a *API) webState(ctx context.Context, in Install, m Manifest) WebState {
	st := WebState{Uses: m.UsesWeb()}
	if !st.Uses {
		st.Open = true
		return st
	}
	st.Search = a.runnerOffers(ctx, "web.search")
	e, err := a.store.EgressFor(ctx, in.OrgID, in.Slug, m)
	switch {
	case err != nil || !e.Org:
		st.Why = "organisation_closed"
		st.Words = Text("agent นี้ยังค้นหาไม่ได้ครับ องค์กรยังไม่อนุญาตให้ agent ทำงานนอกองค์กร ขอให้ผู้ดูแลระบบเปิดที่แอปผู้ดูแลระบบ หน้า Agent Market หัวข้อโมเดล แล้วส่งคำขออีกครั้ง ยังไม่มีการคิดเครดิต")
	case !e.Agent:
		st.Why = "agent_closed"
		st.Words = Text("agent นี้ยังค้นหาไม่ได้ครับ ผู้ดูแลระบบยังไม่ได้เปิดให้ agent นี้ค้นหาและเปิดหน้าเว็บนอกองค์กร ขอให้ผู้ดูแลเปิดให้ agent นี้ที่แอปผู้ดูแลระบบ หน้า Agent Market หัวข้อโมเดล แล้วส่งคำขออีกครั้ง ยังไม่มีการคิดเครดิต")
	case !a.runnerOffers(ctx, "web.fetch"):
		st.Why = "no_fetcher"
		st.Words = Text("agent นี้ยังเปิดหน้าเว็บไม่ได้ครับ ส่วนที่เก็บหน้าเว็บเป็นหลักฐานยังไม่พร้อมใช้งาน ขอให้ผู้ดูแลระบบตรวจ แล้วส่งคำขออีกครั้ง ยังไม่มีการคิดเครดิต")
	default:
		st.Open = true
		if !st.Search {
			st.Words = Text("ยังไม่มีผู้ให้บริการค้นหา agent นี้จึงยังค้นหาเองไม่ได้ วางลิงก์หน้าสินค้าที่สนใจมาในข้อความ แล้ว agent จะเปิดและเทียบให้ หรือขอให้ผู้ดูแลระบบตั้งผู้ให้บริการค้นหา")
		}
	}
	return st
}

// webClosed says in the thread why a round cannot start, when it cannot. It
// returns true when it said so (and nothing is to be started).
func (a *API) webClosed(ctx context.Context, tx pgx.Tx, in Install, userID, tenant uuid.UUID, thread *uuid.UUID, item ThreadItem, text string) (bool, error) {
	m, err := a.manifestOf(ctx, in)
	if err != nil || !m.UsesWeb() {
		return false, nil
	}
	st := a.webState(ctx, in, m)
	why := st.Why
	words := st.Words
	if st.Open && (st.Search || len(LinksIn(text)) > 0) {
		return false, nil
	}
	if st.Open {
		why = "no_search"
	}
	if _, _, err := a.store.AddItemIn(ctx, tx, in.ID, thread, tenant, userID, nil, Notice{Text: words, Tone: "warning", Reason: why}, nil); err != nil {
		return false, err
	}
	if item.ID != uuid.Nil {
		if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_thread_items SET payload = payload || jsonb_build_object('status', 'handled', 'handled_as', 'web_closed'),
			rev = nextval('agent_market.thread_rev') WHERE id = $1`, item.ID); err != nil {
			return false, err
		}
	}
	return true, nil
}

// ── what a round's owner reads of its sources ────────────────────────────

var evidenceName = regexp.MustCompile(`^evidence_[0-9a-f]{32}\.jpg$`)

// WebEvidenceStep is the step key the pictures cut from a run's stored pages
// are kept under (BlobKey takes a step's key; these belong to no one step).
const WebEvidenceStep = "web-evidence-pictures"

// runEvidence serves one picture cut from a stored page: what the page
// showed where a value was read.
func (a *API) runEvidence(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	name := chi.URLParam(r, "name")
	if !ok || !evidenceName.MatchString(name) {
		badRequest(w, "BAD_ID")
		return
	}
	run, err := a.store.GetRun(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	a.serveBlob(w, r, BlobKey(run.TenantID, run.ID, WebEvidenceStep, name), "image/jpeg")
}

// runSources lists what a run searched for and tried to open.
func (a *API) runSources(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	run, err := a.store.GetRun(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	calls, err := a.store.RunWebCalls(r.Context(), run.ID)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	// why an address was not opened or not kept, in the words of the agent's own package (its manifest
	// holds them in both languages, by the reason's code) and in the screen's language
	words := map[string]string{}
	if in, err := a.store.GetInstall(r.Context(), c.TenantUUID, c.UserID, run.InstallID); err == nil {
		if def, err := a.store.DefinitionByID(r.Context(), in.DefinitionID); err == nil {
			words = reasonWords(def.Manifest, LangOf(r.Context()))
		}
	}
	type row struct {
		WebCall
		Words string `json:"words,omitempty"`
	}
	out := make([]row, 0, len(calls))
	for _, cl := range calls {
		x := row{WebCall: cl}
		if cl.Reason != "" {
			if x.Words = words[cl.Reason]; x.Words == "" {
				x.Words = words["other"]
			}
		}
		out = append(out, x)
	}
	writeJSON(w, http.StatusOK, map[string]any{"calls": out})
}

// reasonWords reads, from a package's manifest, the sentence for each reason a source is not used
// ("strings": {"drop.<reason>": Thai}, "en": {Thai: English}) in one language.
func reasonWords(manifest json.RawMessage, l Lang) map[string]string {
	var m struct {
		Strings map[string]string `json:"strings"`
		En      map[string]string `json:"en"`
	}
	out := map[string]string{}
	if json.Unmarshal(manifest, &m) != nil {
		return out
	}
	for key, thai := range m.Strings {
		code, ok := strings.CutPrefix(key, "drop.")
		if !ok {
			continue
		}
		out[code] = thai
		if l == English && m.En[thai] != "" {
			out[code] = m.En[thai]
		}
	}
	return out
}

// runSourceFile serves one file of a stored page: its picture, or the text a
// person could see on it. Never the markup as a page — a stored page is
// evidence to read, not something a browser is given to run.
func (a *API) runSourceFile(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	run, err := a.store.GetRun(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	a.serveSourceFile(w, r, run, chi.URLParam(r, "source"), chi.URLParam(r, "file"))
}

func (a *API) serveSourceFile(w http.ResponseWriter, r *http.Request, run Run, source, file string) {
	names := map[string][2]string{"picture": {"screenshot.jpg", "image/jpeg"}, "text": {"text.json", "application/json; charset=utf-8"},
		"markup": {"page.html", "text/plain; charset=utf-8"}}
	f, ok := names[file]
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var prefix string
	var purged *time.Time
	err := a.store.Pool.QueryRow(r.Context(), `SELECT blob_prefix, purged_at FROM agent_market.web_calls
		WHERE run_id = $1 AND source = $2 AND kind = 'fetch' AND outcome = 'captured'`, run.ID, source).Scan(&prefix, &purged)
	if err != nil || prefix == "" {
		a.fail(w, ErrNotFound, "run")
		return
	}
	if purged != nil {
		say(w, http.StatusGone, "WEB_PAGE_EXPIRED")
		return
	}
	a.serveBlob(w, r, prefix+"/"+f[0], f[1])
}

func (a *API) serveBlob(w http.ResponseWriter, r *http.Request, key, contentType string) {
	body, _, _, err := a.files.Download(r.Context(), key)
	if err != nil {
		a.fail(w, ErrNotFound, "run")
		return
	}
	defer body.Close()
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Cache-Control", "private, max-age=300")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; sandbox")
	_, _ = io.Copy(w, body)
}

func (a *API) webRoutes(r chi.Router) {
	r.Get("/runs/{id}/evidence/{name}", a.runEvidence)
	r.Get("/runs/{id}/sources", a.runSources)
	r.Get("/runs/{id}/sources/{source}/{file}", a.runSourceFile)
}

// ── the admin door ───────────────────────────────────────────────────────

type webAgentRow struct {
	Slug string  `json:"slug"`
	Name string  `json:"name"`
	Caps WebCaps `json:"caps"`
	Egress
}

// adminWeb is everything an admin sets for agents that work on the web.
func (a *API) adminWeb(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	ctx := r.Context()
	acc := accessOf(r)
	p, err := a.store.WebPolicyFor(ctx, a.store.Pool, acc.OrgID)
	if err != nil {
		a.fail(w, err, "web")
		return
	}
	deny, err := a.store.DenyHosts(ctx, acc.OrgID)
	if err != nil {
		a.fail(w, err, "web")
		return
	}
	sup, err := a.store.Suppliers(ctx, acc.OrgID)
	if err != nil {
		a.fail(w, err, "web")
		return
	}
	agents := []webAgentRow{}
	if o, err := a.store.OrgSettingsFor(ctx, acc.OrgID); err == nil {
		if defs, err := a.store.ListDefinitions(ctx); err == nil {
			for _, d := range defs {
				m, err := ParseManifest(d.Manifest)
				if err != nil || !m.UsesWeb() {
					continue
				}
				set, _ := a.store.AgentEgressSetting(ctx, a.store.Pool, acc.OrgID, d.Slug)
				agents = append(agents, webAgentRow{Slug: d.Slug, Name: m.NameIn(LangOf(ctx), d.Name), Caps: WebCapsOf(m, p),
					Egress: EgressOf(o.DocumentsMayLeave, m, set)})
			}
		}
	}
	sort.Slice(agents, func(i, j int) bool { return agents[i].Name < agents[j].Name })
	writeJSON(w, http.StatusOK, map[string]any{"policy": p, "deny": deny, "suppliers": sup, "agents": agents, "ceiling": webCeiling,
		"search": a.runnerOffers(ctx, "web.search"), "fetcher": a.runnerOffers(ctx, "web.fetch")})
}

func optInt(raw json.RawMessage, lo, hi int) (*int, bool) {
	s := strings.TrimSpace(string(raw))
	if s == "" || s == "null" {
		return nil, true
	}
	n, err := strconv.Atoi(strings.Trim(s, `"`))
	if err != nil || n < lo || n > hi {
		return nil, false
	}
	return &n, true
}

func (a *API) adminPutWebPolicy(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	var body struct {
		MaxSearches    json.RawMessage `json:"max_searches"`
		MaxPages       json.RawMessage `json:"max_pages"`
		MaxSeconds     json.RawMessage `json:"max_seconds"`
		MaxSourceMB    json.RawMessage `json:"max_source_mb"`
		UnusedKeepDays json.RawMessage `json:"unused_keep_days"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<12)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	var p WebPolicy
	var ok [5]bool
	p.MaxSearches, ok[0] = optInt(body.MaxSearches, 0, webCeiling.Searches)
	p.MaxPages, ok[1] = optInt(body.MaxPages, 1, webCeiling.Pages)
	p.MaxSeconds, ok[2] = optInt(body.MaxSeconds, 30, webCeiling.Seconds)
	p.MaxSourceMB, ok[3] = optInt(body.MaxSourceMB, 1, webCeiling.SourceMB)
	keep, okKeep := optInt(body.UnusedKeepDays, 1, 90)
	ok[4] = okKeep
	for _, good := range ok {
		if !good {
			say(w, http.StatusBadRequest, "BAD_WEB_POLICY")
			return
		}
	}
	p.UnusedKeepDays = DefaultUnusedKeepDays
	if keep != nil {
		p.UnusedKeepDays = *keep
	}
	ctx := r.Context()
	acc := accessOf(r)
	who, _ := adminActorOf(ctx)
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		before, err := a.store.WebPolicyFor(ctx, tx, acc.OrgID)
		if err != nil {
			return nil, err
		}
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.web_policy (org_id, max_searches, max_pages, max_seconds, max_source_mb,
			unused_keep_days, updated_by) VALUES ($1, $2, $3, $4, $5, $6, $7)
			ON CONFLICT (org_id) DO UPDATE SET max_searches = EXCLUDED.max_searches, max_pages = EXCLUDED.max_pages,
			max_seconds = EXCLUDED.max_seconds, max_source_mb = EXCLUDED.max_source_mb, unused_keep_days = EXCLUDED.unused_keep_days,
			updated_by = EXCLUDED.updated_by, updated_at = now()`, orgOr(acc.OrgID), p.MaxSearches, p.MaxPages, p.MaxSeconds,
			p.MaxSourceMB, p.UnusedKeepDays, normEmail(who.Email)); err != nil {
			return nil, err
		}
		view := func(x WebPolicy) map[string]any {
			return map[string]any{"max_searches": x.MaxSearches, "max_pages": x.MaxPages, "max_seconds": x.MaxSeconds,
				"max_source_mb": x.MaxSourceMB, "unused_keep_days": x.UnusedKeepDays}
		}
		if jsonText(view(before)) == jsonText(view(p)) {
			return nil, nil
		}
		return []AuditEntry{entry(r, "agent_market.web.policy", "agent_market_web", orgOr(acc.OrgID),
			map[string]any{"from": view(before), "to": view(p)})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "web")
		return
	}
	p, _ = a.store.WebPolicyFor(ctx, a.store.Pool, acc.OrgID)
	writeJSON(w, http.StatusOK, map[string]any{"policy": p})
}

func (a *API) adminPutDenyHost(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	var body struct {
		Host string `json:"host"`
		Note string `json:"note"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<12)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	host, ok := HostOf(body.Host)
	if !ok {
		say(w, http.StatusBadRequest, "BAD_HOST")
		return
	}
	note := TrimText(strings.Join(strings.Fields(body.Note), " "), 300)
	ctx := r.Context()
	acc := accessOf(r)
	who, _ := adminActorOf(ctx)
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		var old *string
		_ = tx.QueryRow(ctx, `SELECT note FROM agent_market.web_deny_hosts WHERE org_id = $1 AND host = $2 FOR UPDATE`,
			orgOr(acc.OrgID), host).Scan(&old)
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.web_deny_hosts (org_id, host, note, added_by) VALUES ($1, $2, $3, $4)
			ON CONFLICT (org_id, host) DO UPDATE SET note = EXCLUDED.note`, orgOr(acc.OrgID), host, note, normEmail(who.Email)); err != nil {
			return nil, err
		}
		if old != nil && *old == note {
			return nil, nil
		}
		return []AuditEntry{entry(r, "agent_market.web.host_denied", "agent_market_web", host, map[string]any{"host": host, "note": note})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "web")
		return
	}
	deny, _ := a.store.DenyHosts(ctx, acc.OrgID)
	writeJSON(w, http.StatusOK, map[string]any{"deny": deny})
}

func (a *API) adminDeleteDenyHost(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	host, ok := HostOf(chi.URLParam(r, "host"))
	if !ok {
		say(w, http.StatusBadRequest, "BAD_HOST")
		return
	}
	ctx := r.Context()
	acc := accessOf(r)
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		tag, err := tx.Exec(ctx, `DELETE FROM agent_market.web_deny_hosts WHERE org_id = $1 AND host = $2`, orgOr(acc.OrgID), host)
		if err != nil || tag.RowsAffected() == 0 {
			return nil, err
		}
		return []AuditEntry{entry(r, "agent_market.web.host_allowed_again", "agent_market_web", host, map[string]any{"host": host})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "web")
		return
	}
	deny, _ := a.store.DenyHosts(ctx, acc.OrgID)
	writeJSON(w, http.StatusOK, map[string]any{"deny": deny})
}

var taxIDShape = regexp.MustCompile(`^[0-9]{13}$`)

func (a *API) adminPutSupplier(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	var body struct {
		ID      string `json:"id"`
		Name    string `json:"name"`
		TaxID   string `json:"tax_id"`
		Website string `json:"website"`
		Note    string `json:"note"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<13)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	name := strings.Join(strings.Fields(body.Name), " ")
	if name == "" || len([]rune(name)) > 160 {
		badRequest(w, "BAD_NAME")
		return
	}
	taxID := strings.NewReplacer(" ", "", "-", "").Replace(strings.TrimSpace(body.TaxID))
	if taxID != "" && !taxIDShape.MatchString(taxID) {
		say(w, http.StatusBadRequest, "BAD_TAX_ID")
		return
	}
	site := ""
	if strings.TrimSpace(body.Website) != "" {
		h, ok := HostOf(body.Website)
		if !ok {
			say(w, http.StatusBadRequest, "BAD_HOST")
			return
		}
		site = h
	}
	note := TrimText(strings.Join(strings.Fields(body.Note), " "), 300)
	ctx := r.Context()
	acc := accessOf(r)
	who, _ := adminActorOf(ctx)
	var id uuid.UUID
	if body.ID != "" {
		parsed, err := uuid.Parse(body.ID)
		if err != nil {
			badRequest(w, "BAD_ID")
			return
		}
		id = parsed
	}
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		action := "agent_market.web.supplier_added"
		if id != uuid.Nil {
			action = "agent_market.web.supplier_changed"
			tag, err := tx.Exec(ctx, `UPDATE agent_market.org_suppliers SET name = $3, tax_id = $4, website = $5, note = $6, updated_at = now()
				WHERE org_id = $1 AND id = $2`, orgOr(acc.OrgID), id, name, taxID, site, note)
			if err != nil {
				return nil, supplierClash(err)
			}
			if tag.RowsAffected() == 0 {
				return nil, ErrNotFound
			}
		} else if err := tx.QueryRow(ctx, `INSERT INTO agent_market.org_suppliers (org_id, name, tax_id, website, note, added_by)
			VALUES ($1, $2, $3, $4, $5, $6) RETURNING id`, orgOr(acc.OrgID), name, taxID, site, note, normEmail(who.Email)).Scan(&id); err != nil {
			return nil, supplierClash(err)
		}
		return []AuditEntry{entry(r, action, "agent_market_supplier", id.String(),
			map[string]any{"name": name, "tax_id": taxID, "website": site, "note": note})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "web")
		return
	}
	sup, _ := a.store.Suppliers(ctx, acc.OrgID)
	writeJSON(w, http.StatusOK, map[string]any{"suppliers": sup})
}

// supplierClash: a second supplier of the same name is refused in words.
func supplierClash(err error) error {
	if err != nil && strings.Contains(err.Error(), "org_suppliers_org_id_name_key") {
		return refusal{status: http.StatusConflict, code: "SUPPLIER_EXISTS"}
	}
	return err
}

func (a *API) adminDeleteSupplier(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	ctx := r.Context()
	acc := accessOf(r)
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		var name string
		if err := tx.QueryRow(ctx, `DELETE FROM agent_market.org_suppliers WHERE org_id = $1 AND id = $2 RETURNING name`,
			orgOr(acc.OrgID), id).Scan(&name); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return nil, nil
			}
			return nil, err
		}
		return []AuditEntry{entry(r, "agent_market.web.supplier_removed", "agent_market_supplier", id.String(), map[string]any{"name": name})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "web")
		return
	}
	sup, _ := a.store.Suppliers(ctx, acc.OrgID)
	writeJSON(w, http.StatusOK, map[string]any{"suppliers": sup})
}

// webRunRow is one run's use of the web, for the admin's page: the hosts it
// actually reached, what it was refused, and what the searching cost.
type webRunRow struct {
	RunID     uuid.UUID `json:"run_id"`
	Slug      string    `json:"slug"`
	Agent     string    `json:"agent"`
	User      string    `json:"user"`
	StartedAt time.Time `json:"started_at"`
	Searches  int       `json:"searches"`
	Fetches   int       `json:"fetches"`
	Reached   []string  `json:"reached"`
	Refused   []webNo   `json:"refused"`
	Bytes     int64     `json:"bytes"`
	Credits   float64   `json:"search_credits"`
}

type webNo struct {
	Host   string `json:"host"`
	Reason string `json:"reason"`
	// why, in the words of the agent's own package, in both languages: the admin app shows the one of
	// its screen (it sends no language of its own), and never the code
	Words map[string]string `json:"words"`
}

// adminWebCalls lists, run by run, where the organisation's agents went.
func (a *API) adminWebCalls(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	days, _ := strconv.Atoi(r.URL.Query().Get("days"))
	if days <= 0 || days > 366 {
		days = 30
	}
	ctx := r.Context()
	acc := accessOf(r)
	rows, err := a.store.Pool.Query(ctx, `SELECT c.run_id, i.slug, r.user_id, c.kind, c.host, c.outcome, c.reason, c.bytes, c.credits::float8, c.started_at
		FROM agent_market.web_calls c
		JOIN agent_market.agent_runs r ON r.id = c.run_id
		JOIN agent_market.agent_installs i ON i.id = c.install_id
		WHERE c.org_id = $1 AND c.started_at > now() - make_interval(days => $2)
		ORDER BY c.started_at DESC LIMIT 5000`, orgOr(acc.OrgID), days)
	if err != nil {
		a.fail(w, err, "web")
		return
	}
	defer rows.Close()
	by := map[uuid.UUID]*webRunRow{}
	users := map[uuid.UUID]uuid.UUID{}
	var order []uuid.UUID
	for rows.Next() {
		var run, user uuid.UUID
		var slug, kind, host, outcome, reason string
		var bytes int64
		var credits float64
		var at time.Time
		if err := rows.Scan(&run, &slug, &user, &kind, &host, &outcome, &reason, &bytes, &credits, &at); err != nil {
			a.fail(w, err, "web")
			return
		}
		row := by[run]
		if row == nil {
			row = &webRunRow{RunID: run, Slug: slug, StartedAt: at, Reached: []string{}, Refused: []webNo{}}
			by[run] = row
			users[run] = user
			order = append(order, run)
		}
		if at.Before(row.StartedAt) {
			row.StartedAt = at
		}
		row.Bytes += bytes
		row.Credits += credits
		if kind == "search" {
			row.Searches++
			continue
		}
		row.Fetches++
		if outcome == "captured" {
			if !contains(row.Reached, host) {
				row.Reached = append(row.Reached, host)
			}
		} else {
			row.Refused = append(row.Refused, webNo{Host: host, Reason: reason})
		}
	}
	names := map[string]string{}
	words := map[string][2]map[string]string{}
	if defs, err := a.store.ListDefinitions(ctx); err == nil {
		for _, d := range defs {
			if m, err := ParseManifest(d.Manifest); err == nil {
				names[d.Slug] = m.NameIn(LangOf(ctx), d.Name)
				if m.UsesWeb() {
					words[d.Slug] = [2]map[string]string{reasonWords(d.Manifest, Thai), reasonWords(d.Manifest, English)}
				}
			}
		}
	}
	out := make([]webRunRow, 0, len(order))
	for _, id := range order {
		row := by[id]
		row.Agent = names[row.Slug]
		for i, no := range row.Refused {
			th, en := words[row.Slug][0][no.Reason], words[row.Slug][1][no.Reason]
			if th == "" {
				th, en = words[row.Slug][0]["other"], words[row.Slug][1]["other"]
			}
			row.Refused[i].Words = map[string]string{"th": th, "en": en}
		}
		row.User = a.store.DisplayName(ctx, acc.OrgID, users[id], "")
		out = append(out, *row)
	}
	writeJSON(w, http.StatusOK, map[string]any{"days": days, "runs": out})
}

func (a *API) adminWebRoutes(r chi.Router) {
	r.Get("/web", a.adminWeb)
	r.Put("/web/policy", a.adminPutWebPolicy)
	r.Put("/web/deny", a.adminPutDenyHost)
	r.Delete("/web/deny/{host}", a.adminDeleteDenyHost)
	r.Put("/web/suppliers", a.adminPutSupplier)
	r.Delete("/web/suppliers/{id}", a.adminDeleteSupplier)
	r.Get("/web/calls", a.adminWebCalls)
}
