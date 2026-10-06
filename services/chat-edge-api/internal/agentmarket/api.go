package agentmarket

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/db"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// API is the control plane. It writes rows and reads them; it never runs a
// step (no sandbox, no model). chat-edge constructs it only when the flag
// is on, with a Postgres pool of its own (never chat's pool).
type API struct {
	cfg       Config
	store     *Store
	files     storage.Backend
	log       *slog.Logger
	closeFn   func()
	sweepOnce sync.Once
	// knowledge reads a person's knowledge base for a question asked in ask
	// mode (AM6, knowledge.go); nil = none is read.
	knowledge Knowledge
	// auditor takes every admin change into the admin audit log (AM7,
	// audit.go); nil = no admin change can be made.
	auditor Auditor
	// smtp is the organisation's mail relay (AM7, mail.go).
	smtp *SMTPMailer
	// personMail sends from a person's own mailbox (AM9, personmail.go); nil: not offered.
	personMail PersonMailer
}

// personMailFrom is the mail connector the configuration names (AM9): the
// tests' own, or the MCP gateway's when its URL is set; else none.
func personMailFrom(cfg Config) PersonMailer {
	if cfg.PersonMail != nil {
		return cfg.PersonMail
	}
	if cfg.MailGatewayURL == "" {
		return nil
	}
	return &MCPMail{Gateway: cfg.MailGatewayURL, Portal: cfg.PortalURL,
		Token: func() string { return strings.TrimSpace(os.Getenv("AGENT_MARKET_MAIL_GATEWAY_TOKEN")) }}
}

// bearerOf is the signed-in person's own portal token, which the mail
// connector forwards (the gateway fetches that person's mailbox token with it).
func bearerOf(r *http.Request) string {
	t, err := middleware.ExtractPortalToken(r)
	if err != nil {
		return ""
	}
	return t
}

// NewAPI opens the control plane's own small pool and applies the
// agent_market migrations. Called by chat-edge's main only when enabled.
func NewAPI(ctx context.Context, cfg Config, dsn string, files storage.Backend, log *slog.Logger) (*API, error) {
	pc, err := db.PoolConfig(dsn, db.DefaultTimeouts())
	if err != nil {
		return nil, err
	}
	pc.MaxConns = int32(cfg.PoolMaxConns)
	pc.MinConns = 0
	pc.ConnConfig.RuntimeParams["application_name"] = "chat-edge-agent-market"
	pool, err := pgxpool.NewWithConfig(ctx, pc)
	if err != nil {
		return nil, fmt.Errorf("agentmarket: pool: %w", err)
	}
	if _, err := Migrate(ctx, pool); err != nil {
		pool.Close()
		return nil, err
	}
	store := NewStore(pool)
	// AM3: the organisation's first access list, from the pilot's settings (once).
	if created, err := store.EnsureOrg(ctx, cfg); err != nil {
		pool.Close()
		return nil, fmt.Errorf("agentmarket: organisation: %w", err)
	} else if created {
		log.Info("agent-market organisation created from AGENT_MARKET_ALLOWED_EMAILS / AGENT_MARKET_ADMIN_EMAILS",
			"allowed", len(cfg.AllowedEmails), "admins", len(cfg.AdminEmails))
	}
	// AM11: the English every registered package carries for its own words — an English item's reader
	// takes them as written for a person, and a thread is read before any definition is
	if err := store.LearnPackageEnglish(ctx); err != nil {
		log.Warn("agent-market: the packages' English could not be read at start (learnt as definitions are read)", "err", err)
	}
	return &API{cfg: cfg, store: store, files: files, log: log.With("component", "agent-market"), closeFn: pool.Close,
		smtp: NewSMTPMailer(store, log), personMail: personMailFrom(cfg)}, nil
}

// AccountProvider is Agent Market's part of "export all my data" and of
// account deletion (account.go), registered by chat-edge while the flag is
// on. The first call also sweeps, in the background, the data of users
// deleted while the flag was off.
func (a *API) AccountProvider() *AccountProvider {
	p := NewAccountProvider(a.store, a.files)
	a.sweepOnce.Do(func() {
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
			defer cancel()
			if n, err := p.SweepDeletedUsers(ctx); err != nil {
				a.log.Warn("sweep of deleted users failed", "err", err)
			} else if n > 0 {
				a.log.Info("purged the agent data of users deleted while the feature was off", "users", n)
			}
		}()
	})
	return p
}

// NewAPIWithStore is for tests.
func NewAPIWithStore(cfg Config, store *Store, files storage.Backend, log *slog.Logger) *API {
	// AM7: admin changes need an audit log; a test that does not look at it keeps none
	return &API{cfg: cfg, store: store, files: files, log: log, closeFn: func() {}, auditor: DiscardAuditor{},
		smtp: &SMTPMailer{store: store, Password: func() string { return "" }, Log: log}, personMail: personMailFrom(cfg)}
}

// Close releases the pool.
func (a *API) Close() {
	if a != nil && a.closeFn != nil {
		a.closeFn()
	}
}

// Routes mounts the control plane (inside chat-edge's authenticated group).
func (a *API) Routes(r chi.Router) {
	r.Use(inRequestLang) // AM11: the language the request names, on its context and its response
	r.Use(a.signedIn)
	r.Get("/availability", a.availability)
	// AM3: a run shared with a named colleague — signed in as that person is
	// enough (they need no Agent Market access of their own), never public.
	r.Get("/shared", a.sharedWithMe)
	r.Get("/shared/{id}", a.sharedRun)
	r.Get("/shared/{id}/artifacts/{aid}/download", a.sharedArtifact)
	r.Post("/shared/{id}/review", a.sharedReview)
	r.Post("/shared/{id}/problem", a.sharedProblem)
	r.Group(func(r chi.Router) {
		r.Use(a.allowed)
		r.Get("/catalog", a.catalog)
		r.Get("/catalog/{slug}", a.catalogItem)
		// AM3: what a stranger reads before installing — the agent's real
		// outputs on its sample data, and the data sheet (what it reads,
		// which models see what, where files live and for how long).
		r.Get("/catalog/{slug}/sample", a.catalogSample)
		r.Get("/catalog/{slug}/datasheet", a.datasheet)
		r.Get("/sample-artifacts/{id}/download", a.sampleArtifact)
		r.Get("/sample-files/{id}/download", a.sampleFile)
		// AM3: the left rail (pins, recently used, what waits for an answer)
		r.Get("/rail", a.rail)
		r.Put("/prefs", a.putPrefs)
		r.Put("/installs/{id}/pin", a.pin)
		r.Delete("/installs/{id}/pin", a.unpin)
		r.Post("/installs/{id}/opened", a.opened)
		r.Get("/installs/{id}/budget", a.installBudget)
		r.Get("/installs/{id}/estimate", a.estimate) // AM6: what a round costs, said before it starts
		r.Post("/runs/{id}/resume", a.resumeBudget)
		// AM3: sign-off, sharing inside the organisation, report a problem
		r.Post("/runs/{id}/review", a.review)
		r.Get("/runs/{id}/reviews", a.reviews)
		r.Get("/runs/{id}/shares", a.listShares)
		r.Post("/runs/{id}/shares", a.createShare)
		r.Delete("/shares/{id}", a.revokeShare)
		r.Get("/people", a.people)
		r.Post("/runs/{id}/problem", a.runProblem)
		r.Post("/installs/{id}/problem", a.installProblem)
		// AM3: the organisation's admins (admin.go checks the role per route)
		// AM7: no admin route here — the chat app is for the people who use
		// agents; what affects anyone else is on the admin app's door
		// (AdminRoutes, admindoor.go), behind chat-edge's admin bearer.
		r.Post("/installs", a.install)
		r.Get("/installs", a.listInstalls)
		r.Get("/installs/{id}", a.getInstall)
		r.Delete("/installs/{id}", a.removeInstall)
		r.Get("/installs/{id}/defaults", a.listDefaults)
		r.Put("/installs/{id}/defaults/{key}", a.putDefault)
		r.Delete("/installs/{id}/defaults/{key}", a.deleteDefault)
		r.Post("/installs/{id}/self-test", a.selfTest)
		r.Get("/installs/{id}/runs", a.listRuns)
		r.Get("/installs/{id}/thread", a.thread)
		r.Get("/installs/{id}/events", a.events)
		r.Post("/installs/{id}/files", a.upload)
		r.Post("/installs/{id}/template", a.uploadTemplate)
		r.Put("/installs/{id}/template", a.saveTemplate)
		r.Get("/installs/{id}/template", a.getTemplate)
		r.Delete("/installs/{id}/template", a.removeTemplate)
		r.Get("/installs/{id}/upgrade", a.upgradeInfo)
		r.Get("/installs/{id}/dashboard", a.dashboard) // AM2: how this agent has been doing
		r.Post("/installs/{id}/upgrade", a.startUpgrade)
		r.Post("/installs/{id}/rollback", a.rollback)
		r.Get("/runs/{id}/exceptions", a.runExceptions)
		r.Post("/exceptions/{id}/decision", a.decideException)
		r.Post("/installs/{id}/messages", a.message)
		a.roundRoutes(r) // AM4: threads, the round-boundary chip, "เริ่มรอบใหม่"
		// AM6: reminders, notifications, e-mail drafts (reminders.go, notify.go, email.go)
		a.reminderMailRoutes(r)
		r.Post("/mapping-preview", a.mappingPreview)
		r.Post("/cards/{id}/answer", a.answer)
		r.Get("/runs/{id}", a.getRun)
		r.Get("/runs/{id}/computed", a.computed)
		r.Post("/runs/{id}/cancel", a.cancel)
		r.Post("/runs/{id}/retry", a.retry)       // AM5: a failed round, tried again with the files it had
		r.Post("/runs/{id}/restore", a.restore)   // AM5: a document that was left out, brought back
		r.Post("/runs/{id}/receipts", a.receipts) // AM6: a receipt confirmed in this system
		r.Post("/runs/{id}/formats", a.formats)
		r.Get("/artifacts/{id}/download", a.downloadArtifact)
		r.Get("/files/{id}/download", a.downloadFile)
		r.Get("/theme", a.getTheme)
		r.Put("/theme", a.putTheme)
	})
}

// ── helpers ──────────────────────────────────────────────────────────────

// writeJSON sends v. AM11: every text of the platform's inside it is in the
// language the request named (lang.go).
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	b, err := MarshalIn(langOfWriter(w), v)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"error":{"code":"AGENT_MARKET_ERROR","message":""}}` + "\n"))
		return
	}
	w.WriteHeader(status)
	_, _ = w.Write(append(b, '\n'))
}

// writeErr sends a message a person reads. AM5: whatever a caller put into
// it, nothing technical leaves (the last line of defence; the callers name
// things in words).
func writeErr(w http.ResponseWriter, status int, code, msg string) {
	if LooksLikeError(msg) {
		if msg = ForPerson(msg); msg == "" {
			msg = MessageIn(langOfWriter(w), "AGENT_MARKET_ERROR")
		}
	}
	writeJSON(w, status, map[string]any{"error": map[string]string{"code": code, "message": msg}})
}

func (a *API) fail(w http.ResponseWriter, err error, what string) {
	if errors.Is(err, ErrAuditUnavailable) {
		// AM7: the change was not made, because the audit log could not record it
		w.Header().Set("Retry-After", "30")
		say(w, http.StatusServiceUnavailable, "AUDIT_UNAVAILABLE")
		return
	}
	if errors.Is(err, ErrNotFound) {
		say(w, http.StatusNotFound, "NOT_FOUND", "what", whatIn(langOfWriter(w), what))
		return
	}
	if busy(err) {
		// Its own pools full or its tables locked: temporary, and contained
		// here — chat never waits on these pools.
		a.log.Warn("agent-market busy", "what", what, "err", err)
		w.Header().Set("Retry-After", "5")
		say(w, http.StatusServiceUnavailable, "AGENT_MARKET_BUSY")
		return
	}
	a.log.Error("agent-market request failed", "what", what, "err", err)
	say(w, http.StatusInternalServerError, "AGENT_MARKET_ERROR")
}

// busy is a database that did not get to the query in time: a lock wait or
// statement cut off by its timeout, no connection left, or a request whose
// deadline passed while it waited for one.
func busy(err error) bool {
	var pg *pgconn.PgError
	if errors.As(err, &pg) {
		switch pg.Code {
		case "55P03", "57014", "53300": // lock_not_available, query_canceled, too_many_connections
			return true
		}
	}
	return errors.Is(err, context.DeadlineExceeded)
}

func caller(r *http.Request) (middleware.Caller, bool) {
	return middleware.CallerFrom(r.Context())
}

func pathID(r *http.Request, name string) (uuid.UUID, bool) {
	id, err := uuid.Parse(chi.URLParam(r, name))
	return id, err == nil
}

// signedIn resolves the person's organisation access for every request (the
// shared-run pages need a signed-in person only; the rest also need access).
func (a *API) signedIn(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, ok := caller(r)
		if !ok {
			say(w, http.StatusUnauthorized, "UNAUTHORIZED")
			return
		}
		acc, err := a.store.AccessFor(r.Context(), a.cfg, c.Email, c.UserID)
		if err != nil {
			a.fail(w, err, "access")
			return
		}
		next.ServeHTTP(w, r.WithContext(withAccess(r.Context(), acc)))
	})
}

// allowed: the organisation lets this person use Agent Market (AM3: the
// admins' access list, not an environment variable).
func (a *API) allowed(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !accessOf(r).Allowed {
			a.sayTo(w, r, http.StatusForbidden, "AGENT_MARKET_NOT_ALLOWED")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (a *API) ownedInstall(w http.ResponseWriter, r *http.Request) (Install, middleware.Caller, bool) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return Install{}, c, false
	}
	in, err := a.store.GetInstall(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "install")
		return Install{}, c, false
	}
	return in, c, true
}

// ── availability, catalog ────────────────────────────────────────────────

func (a *API) availability(w http.ResponseWriter, r *http.Request) {
	acc := accessOf(r)
	// available decides every surface; the rest tells the web which ones
	// (admin pages, publish buttons) and whom to ask when access is closed.
	out := map[string]any{"available": acc.Allowed, "access": acc.Mode, "member": acc.Member, "role": acc.Role,
		"admin": acc.Admin(), "publisher": acc.Publisher(), "contact": acc.Contact,
		"pricing": map[string]float64{"credits_per_usd": a.cfg.CreditsPerUSD, "thb_per_usd": a.cfg.THBPerUSD}}
	if st, err := a.store.LatestRunner(r.Context(), a.cfg.RunnerStaleAfter); err == nil {
		out["runner"] = map[string]any{"healthy": st.Healthy, "seen_at": st.SeenAt, "version": st.Version}
	} else {
		out["runner"] = map[string]any{"healthy": false}
	}
	writeJSON(w, http.StatusOK, out)
}

// ── install ──────────────────────────────────────────────────────────────

type installReq struct {
	Slug           string                     `json:"slug"`
	IdempotencyKey string                     `json:"idempotency_key"`
	Settings       map[string]json.RawMessage `json:"settings"` // company, formats, currency, language, materiality, mapping
	Theme          json.RawMessage            `json:"theme"`
	SelfTest       bool                       `json:"self_test"`
}

func (a *API) install(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	var req installReq
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	def, err := a.store.DefinitionBySlug(ctx, req.Slug)
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	// AM3: the organisation's catalog decides (published; never a disabled agent).
	if !a.agentGate(w, r, def.Slug, true) {
		return
	}
	m, err := ParseManifest(def.Manifest)
	if err != nil {
		a.fail(w, err, "manifest")
		return
	}
	// A setting the person gave that we know and that is wrong is refused, not
	// dropped. AM6: what is known is the platform's settings and the ones this
	// agent's manifest declares.
	rules := rulesOf(m)
	for k, v := range req.Settings {
		if !rules.known(k) {
			continue
		}
		if _, code, problem := rules.check(k, v); !problem.Empty() {
			say(w, http.StatusBadRequest, code, "detail", problem.In(langOfWriter(w)))
			return
		}
	}
	st, err := a.store.LatestRunner(ctx, a.cfg.RunnerStaleAfter)
	if err != nil {
		a.sayTo(w, r, http.StatusServiceUnavailable, "RUNNER_DOWN")
		return
	}
	var available map[string]Binding
	_ = json.Unmarshal(st.Capabilities, &available)
	res, err := Resolve(m, available, 1)
	if err != nil {
		a.log.Warn("install refused", "slug", m.ID, "err", err)
		a.sayTo(w, r, http.StatusConflict, "CAPABILITY_MISSING", "detail", missingWords(langOfWriter(w), err))
		return
	}
	bindings, _ := json.Marshal(res)
	title := m.ShortName
	// AM11: an install made from an English screen is headed by the agent's English name (a package
	// has no short one in English); the title is the person's from then on, like any install's
	if en := m.NameIn(langOfWriter(w), ""); en != "" {
		title = en
	}
	if title == "" {
		title = m.Name
	}
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	in, created, err := a.store.CreateInstall(ctx, tx, Install{
		TenantID: c.TenantUUID, UserID: c.UserID, DefinitionID: def.ID, Slug: def.Slug, DefinitionVersion: def.Version,
		Checksum: def.Checksum, Title: title, CapabilityBindings: bindings, BindingVersion: res.Version,
		BudgetCredits: a.cfg.DefaultBudgetCredits, OrgID: accessOf(r).OrgID,
	}, strings.TrimSpace(req.IdempotencyKey))
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if created {
		settings := map[string]json.RawMessage{}
		for k, v := range m.Install.Defaults {
			settings[k] = v
		}
		for k, v := range req.Settings {
			settings[k] = v
		}
		for k, v := range settings {
			if !rules.known(k) {
				continue
			}
			kept, _, problem := rules.check(k, v)
			if !problem.Empty() {
				continue
			}
			// AM11: a setting's name and note are kept in a row, in the language of the screen that set it
			l := LangOf(ctx)
			if err := a.store.SetDefault(ctx, tx, in.ID, Default{Key: k, Value: kept, Source: "install",
				Label: rules.label(l, k), Note: T(l, "ตั้งตอนติดตั้ง")}); err != nil {
				a.fail(w, err, "install")
				return
			}
		}
		if len(req.Theme) > 0 && string(req.Theme) != "null" {
			if _, err := tx.Exec(ctx, `INSERT INTO agent_market.agent_themes (tenant_id, version, theme, created_by)
				VALUES ($1, COALESCE((SELECT max(version) FROM agent_market.agent_themes WHERE tenant_id = $1), 0) + 1, $2, $3)`,
				c.TenantUUID, req.Theme, c.UserID); err != nil {
				a.fail(w, err, "theme")
				return
			}
		}
		if _, _, err := a.store.AddItem(ctx, tx, in.ID, c.TenantUUID, c.UserID, nil, GreetingFor(m), nil); err != nil {
			a.fail(w, err, "install")
			return
		}
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "install")
		return
	}
	out := map[string]any{"install": in, "created": created, "bindings": res}
	if created && req.SelfTest {
		run, err := a.startSelfTest(ctx, in, def)
		if err != nil {
			a.fail(w, err, "self-test")
			return
		}
		out["self_test_run_id"] = run.ID
	}
	writeJSON(w, http.StatusOK, out)
}

func validSettingKey(k string) bool {
	switch k {
	case "company", "formats", "currency", "language", "materiality", "mapping", "dc_convention", "theme", "fiscal_year_start",
		"company_tax_id", "company_branch", // AM2: who tax invoices must be made out to
		"policy", "holidays", "policy_kb": // AM2: the expense audit's policy table and calendar
		return true
	}
	if _, ok := matchSettings[k]; ok { // AM5: the tolerances of the matching engine
		return true
	}
	if _, ok := accountSettings[k]; ok { // AM5: the account a reconciliation is headed with
		return true
	}
	return strings.HasPrefix(k, "answers.")
}

// matchSetting is one tolerance of the matching engine (AM5,
// backend/agent_market/engine/e3/match.py SETTINGS: the same bounds — a
// value the engine could not use is refused where it is typed).
type matchSetting struct {
	label    wording
	kind     string // money | int | ratio | bool
	min, max float64
}

var matchSettings = map[string]matchSetting{
	"match_amount_tolerance":  {"ผลต่างของยอดที่ยอมให้เสนอเป็นคู่ (บาท)", "money", 0, 1000},
	"match_date_window_days":  {"วันที่ห่างกันได้ไม่เกิน (วัน)", "int", 0, 31},
	"match_fuzzy_threshold":   {"ความคล้ายของชื่อหรือเลขอ้างอิงอย่างน้อย (0.50–1.00)", "ratio", 0.5, 1},
	"match_fuzzy_window_days": {"วันที่ห่างกันได้เมื่อจับคู่จากชื่อ (วัน)", "int", 0, 366},
	"match_ask_above":         {"ถามเมื่อรายการที่ไม่มีคู่มียอดตั้งแต่ (บาท)", "money", 0, 1e12},
	"match_max_group":         {"จำนวนรายการมากที่สุดที่รวมยอดเป็นคู่เดียว (2–6)", "int", 2, 6},
	"match_auto_same_day":     {"จับคู่เองเมื่อวันที่และยอดตรงกันและมีรายการเดียว", "bool", 0, 0},
}

var accountSettings = map[string]wording{
	"bank_name":           "ธนาคาร",
	"bank_account_number": "เลขที่บัญชีธนาคาร",
	"bank_account_name":   "ชื่อบัญชีธนาคาร",
	"bank_gl_code":        "รหัสบัญชีแยกประเภทของบัญชีธนาคารนี้",
}

// validMatchSetting: a number (typed as text or as a number) within the
// setting's bounds; a whole number where days or rows are counted.
func validMatchSetting(m matchSetting, raw json.RawMessage) bool {
	if m.kind == "bool" {
		var b bool
		if json.Unmarshal(raw, &b) == nil {
			return true
		}
		var s string
		return json.Unmarshal(raw, &s) == nil && (s == "true" || s == "false")
	}
	var s string
	if json.Unmarshal(raw, &s) != nil {
		var n json.Number
		if json.Unmarshal(raw, &n) != nil {
			return false
		}
		s = n.String()
	}
	s = strings.ReplaceAll(strings.TrimSpace(s), ",", "")
	v, err := strconv.ParseFloat(s, 64)
	if err != nil || math.IsNaN(v) || math.IsInf(v, 0) || v < m.min || v > m.max {
		return false
	}
	return m.kind != "int" || v == math.Trunc(v)
}

// validSettingValue checks the settings a number depends on (AM2): the
// company's tax ID (13 digits with a valid mod-11 check digit) and branch
// ("00000" = head office, else five digits).
func validSettingValue(k string, raw json.RawMessage) bool {
	var s string
	switch k {
	case "company_tax_id":
		if json.Unmarshal(raw, &s) != nil || len(s) != 13 {
			return false
		}
		sum := 0
		for i, r := range s {
			if r < '0' || r > '9' {
				return false
			}
			if i < 12 {
				sum += int(r-'0') * (13 - i)
			}
		}
		return int(s[12]-'0') == (11-sum%11)%10
	case "company_branch":
		if json.Unmarshal(raw, &s) != nil || len(s) != 5 {
			return false
		}
		for _, r := range s {
			if r < '0' || r > '9' {
				return false
			}
		}
	case "language":
		// AM10: the agents' documents are Thai and nothing else; a setting that
		// offered "en" changed no document, so nothing but "th" is kept
		return json.Unmarshal(raw, &s) == nil && s == "th"
	case "policy":
		// the structured policy table: limits come from here only, never from a document
		return len(PolicyProblems(Thai, raw)) == 0
	case "holidays":
		return len(HolidayProblems(Thai, raw)) == 0
	}
	if m, ok := matchSettings[k]; ok {
		return validMatchSetting(m, raw)
	}
	if _, ok := accountSettings[k]; ok {
		return json.Unmarshal(raw, &s) == nil && len([]rune(s)) <= 40+80*btoi(k != "bank_account_number" && k != "bank_gl_code")
	}
	return true
}

func btoi(b bool) int {
	if b {
		return 1
	}
	return 0
}

// settingProblem says, in l, what is wrong with a refused setting (for a table, which entries).
func settingProblem(l Lang, key string, v json.RawMessage) string {
	msg := T(l, "ค่าไม่ถูกต้อง: ") + settingLabel(l, key)
	switch key {
	case "policy":
		msg += " — " + strings.Join(PolicyProblems(l, v), " · ")
	case "holidays":
		msg += " — " + strings.Join(HolidayProblems(l, v), " · ")
	}
	return msg
}

// settingLabel is the name of one of the platform's settings, in l.
func settingLabel(l Lang, k string) string {
	switch k {
	case "company":
		return T(l, "ชื่อบริษัทบนเอกสาร")
	case "formats":
		return T(l, "ชุดไฟล์ที่ต้องการ")
	case "currency":
		return T(l, "สกุลเงินหลัก")
	case "language":
		return T(l, "ภาษาของเอกสาร")
	case "materiality":
		return T(l, "เกณฑ์ความมีสาระสำคัญ (บาท)")
	case "mapping":
		return T(l, "การจับคู่คอลัมน์")
	case "dc_convention":
		return T(l, "การอ่านเดบิต/เครดิต")
	case "theme":
		return T(l, "ธีมเอกสาร")
	case "fiscal_year_start":
		return T(l, "เดือนเริ่มปีบัญชี")
	case "company_tax_id":
		return T(l, "เลขประจำตัวผู้เสียภาษีของบริษัท")
	case "company_branch":
		return T(l, "สาขาของบริษัท")
	case "policy":
		return T(l, "ตารางนโยบายการเบิก")
	case "holidays":
		return T(l, "ปฏิทินวันหยุด")
	case "policy_kb":
		return T(l, "เอกสารระเบียบ (ใช้เป็นบริบทเท่านั้น)")
	}
	if m, ok := matchSettings[k]; ok {
		return T(l, m.label)
	}
	if name, ok := accountSettings[k]; ok {
		return T(l, name)
	}
	return k
}

func (a *API) startSelfTest(ctx context.Context, in Install, def Definition) (Run, error) {
	run, _, err := a.store.CreateRun(ctx, a.store.Pool, Run{
		InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindSelfTest,
		Message: T(LangOf(ctx), "ทดสอบกับข้อมูลตัวอย่าง"), DefinitionVersion: def.Version, Checksum: def.Checksum,
		EngineChecksum: def.EngineChecksum, BindingVersion: in.BindingVersion,
	}, "")
	if err != nil {
		return run, err
	}
	if err := a.store.SetSelfTestRun(ctx, in.ID, run.ID); err != nil {
		return run, err
	}
	rid := run.ID
	_, _, err = a.store.AddItem(ctx, a.store.Pool, in.ID, in.TenantID, in.UserID, &rid, QueuedProgress(run), nil)
	return run, err
}

func (a *API) selfTest(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok || !a.agentGate(w, r, in.Slug, false) {
		return
	}
	def, err := a.store.DefinitionByID(r.Context(), in.DefinitionID)
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	run, err := a.startSelfTest(r.Context(), in, def)
	if err != nil {
		a.fail(w, err, "self-test")
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"run": run})
}

func (a *API) listInstalls(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	list, err := a.store.ListInstalls(r.Context(), c.TenantUUID, c.UserID)
	if err != nil {
		a.fail(w, err, "installs")
		return
	}
	type row struct {
		Install
		Icon string `json:"icon"`
		Look
		LastRun   *Run `json:"last_run,omitempty"`
		Attention bool `json:"attention"`
	}
	out := []row{}
	for _, i := range list {
		x := row{Install: i}
		if d, err := a.store.DefinitionByID(r.Context(), i.DefinitionID); err == nil {
			if m, err := ParseManifest(d.Manifest); err == nil {
				x.Icon = m.Icon
			}
		}
		x.Look = a.store.LookForSlug(r.Context(), i.Slug)
		if lr, err := a.store.LatestRun(r.Context(), i.ID); err == nil {
			lr := lr
			x.LastRun = &lr
			x.Attention = lr.Status == RunAwaitingInput
		}
		out = append(out, x)
	}
	writeJSON(w, http.StatusOK, map[string]any{"installs": out})
}

func (a *API) getInstall(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	def, err := a.store.DefinitionByID(r.Context(), in.DefinitionID)
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	defaults, err := a.store.ListDefaults(r.Context(), in.ID)
	if err != nil {
		a.fail(w, err, "defaults")
		return
	}
	// AM11: a setting is named in the reader's language, whoever saved it
	if m, err := ParseManifest(def.Manifest); err == nil {
		defaults = rulesOf(m).shown(langOfWriter(w), defaults)
	}
	spent, _ := a.store.SpentThisMonth(r.Context(), in.ID)
	writeJSON(w, http.StatusOK, map[string]any{"install": in, "manifest": shownManifest(def.Manifest), "defaults": nonNil(defaults),
		"look":   a.store.LookForSlug(r.Context(), in.Slug),
		"budget": map[string]any{"credits": in.BudgetCredits, "spent_this_month": spent}})
}

func (a *API) removeInstall(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	if err := a.store.RemoveInstall(r.Context(), c.TenantUUID, c.UserID, id); err != nil {
		a.fail(w, err, "install")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"removed": true})
}

// ── remembered answers ───────────────────────────────────────────────────

func (a *API) listDefaults(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	list, err := a.store.ListDefaults(r.Context(), in.ID)
	if err != nil {
		a.fail(w, err, "defaults")
		return
	}
	// AM6: the settings the agent's manifest declares, each with its value here
	rules, err := a.rulesFor(r.Context(), in)
	if err != nil {
		a.fail(w, err, "defaults")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"defaults": nonNil(rules.shown(langOfWriter(w), list)), "declared": rules.values(langOfWriter(w), list)})
}

func (a *API) putDefault(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	// a setting is the platform's, or one the install's own agent declares (AM6)
	rules, err := a.rulesFor(r.Context(), in)
	if err != nil {
		a.fail(w, err, "default")
		return
	}
	key := chi.URLParam(r, "key")
	if !rules.known(key) {
		badRequest(w, "BAD_KEY")
		return
	}
	var body struct {
		Value json.RawMessage `json:"value"`
		Label string          `json:"label"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body); err != nil || len(body.Value) == 0 {
		badRequest(w, "BAD_JSON")
		return
	}
	kept, code, problem := rules.check(key, body.Value)
	if !problem.Empty() {
		say(w, http.StatusBadRequest, code, "detail", problem.In(langOfWriter(w)))
		return
	}
	// a declared setting is named as its manifest names it
	l := LangOf(r.Context())
	label := body.Label
	if _, declared := rules.declared[key]; label == "" || declared {
		label = rules.label(l, key)
	}
	if err := a.store.SetDefault(r.Context(), a.store.Pool, in.ID, Default{Key: key, Value: kept, Source: "settings",
		Label: label, Note: T(l, settingNote)}); err != nil {
		a.fail(w, err, "default")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"saved": true})
}

func (a *API) deleteDefault(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	removed, err := a.store.DeleteDefault(r.Context(), in.ID, chi.URLParam(r, "key"))
	if err != nil {
		a.fail(w, err, "default")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"removed": removed})
}

// ── thread + event stream ────────────────────────────────────────────────

func (a *API) thread(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	thread, ok := a.threadParam(w, r, in)
	if !ok {
		return
	}
	after, _ := strconv.ParseInt(r.URL.Query().Get("after"), 10, 64)
	items, err := a.store.ThreadItemsAfter(r.Context(), in.ID, thread, after, 500)
	if err != nil {
		a.fail(w, err, "thread")
		return
	}
	cursor := after
	for _, it := range items {
		if it.Rev > cursor {
			cursor = it.Rev
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": nonNil(items), "cursor": cursor})
}

// events streams thread changes. It holds no run and waits on nothing but
// new rows; a reconnect passes ?after=<last rev> and resumes exactly there.
func (a *API) events(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	thread, ok := a.threadParam(w, r, in)
	if !ok {
		return
	}
	fl, ok := w.(http.Flusher)
	if !ok {
		say(w, http.StatusInternalServerError, "AGENT_MARKET_ERROR")
		return
	}
	after, _ := strconv.ParseInt(r.URL.Query().Get("after"), 10, 64)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	_, _ = fmt.Fprint(w, ": agent-market stream\n\n")
	fl.Flush()
	ctx := r.Context()
	poll := time.NewTicker(a.cfg.StreamPoll)
	defer poll.Stop()
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()
	deadline := time.NewTimer(10 * time.Minute) // the client reconnects with its cursor
	defer deadline.Stop()
	for {
		items, err := a.store.ThreadItemsAfter(ctx, in.ID, thread, after, 200)
		if err != nil {
			return
		}
		for _, it := range items {
			b, _ := json.Marshal(it)
			_, _ = fmt.Fprintf(w, "id: %d\nevent: item\ndata: %s\n\n", it.Rev, b)
			if it.Rev > after {
				after = it.Rev
			}
		}
		if len(items) > 0 {
			fl.Flush()
		}
		select {
		case <-ctx.Done():
			return
		case <-deadline.C:
			_, _ = fmt.Fprintf(w, "event: reconnect\ndata: {\"after\":%d}\n\n", after)
			fl.Flush()
			return
		case <-keepalive.C:
			_, _ = fmt.Fprint(w, ": keepalive\n\n")
			fl.Flush()
		case <-poll.C:
		}
	}
}

// ── upload + message routing ─────────────────────────────────────────────

func (a *API) upload(w http.ResponseWriter, r *http.Request) {
	in, c, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, a.cfg.MaxUploadBytes+(1<<20))
	if err := r.ParseMultipartForm(8 << 20); err != nil {
		say(w, http.StatusRequestEntityTooLarge, "UPLOAD_TOO_LARGE", "mb", strconv.FormatInt(a.cfg.MaxUploadBytes>>20, 10))
		return
	}
	defer r.MultipartForm.RemoveAll() //nolint:errcheck
	fh, hdr, err := r.FormFile("file")
	if err != nil {
		badRequest(w, "NO_FILE")
		return
	}
	defer fh.Close()
	name := SafeName(hdr.Filename)
	low := strings.ToLower(name)
	// The agent's manifest says what it reads (AM2: invoices come as .pdf,
	// e-Tax .xml and photos); an agent that declares nothing takes AM1's set.
	accept := defaultUploadTypes
	if def, err := a.store.DefinitionByID(r.Context(), in.DefinitionID); err == nil {
		if m, err := ParseManifest(def.Manifest); err == nil {
			accept = m.UploadTypes()
			// AM10: an agent that fills forms takes one dropped into a round too
			if len(m.Templates) > 0 {
				for ext := range TemplateKinds {
					if !contains(accept, ext) {
						accept = append(accept, ext)
					}
				}
			}
		}
	}
	ok = false
	for _, ext := range accept {
		if strings.HasSuffix(low, ext) {
			ok = true
		}
	}
	if !ok {
		say(w, http.StatusUnsupportedMediaType, "UNSUPPORTED_FILE", "types", strings.Join(accept, " "))
		return
	}
	if hdr.Size > a.cfg.MaxUploadBytes {
		say(w, http.StatusRequestEntityTooLarge, "UPLOAD_TOO_LARGE", "mb", strconv.FormatInt(a.cfg.MaxUploadBytes>>20, 10))
		return
	}
	tmp, err := os.CreateTemp("", "am-upload-*")
	if err != nil {
		a.fail(w, err, "upload")
		return
	}
	defer os.Remove(tmp.Name())
	defer tmp.Close()
	h := sha256.New()
	size, err := io.Copy(io.MultiWriter(tmp, h), fh)
	if err != nil {
		a.fail(w, err, "upload")
		return
	}
	if _, err := tmp.Seek(0, io.SeekStart); err != nil {
		a.fail(w, err, "upload")
		return
	}
	id := uuid.New()
	key := UploadKey(c.TenantUUID, in.ID, id, name)
	ct := hdr.Header.Get("Content-Type")
	if err := a.files.Upload(r.Context(), key, tmp, ct, size); err != nil {
		a.fail(w, err, "upload")
		return
	}
	var clientID *string
	if v := strings.TrimSpace(r.FormValue("client_id")); v != "" {
		clientID = &v
	}
	f, err := a.store.CreateFile(r.Context(), a.store.Pool, File{ID: id, TenantID: c.TenantUUID, UserID: c.UserID,
		InstallID: in.ID, Name: name, ContentType: ct, Size: size, SHA256: hex.EncodeToString(h.Sum(nil)),
		StorageKey: key, Status: "staged", ClientID: clientID})
	if err != nil {
		a.fail(w, err, "upload")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"file": f})
}

type messageReq struct {
	Text     string      `json:"text"`
	FileIDs  []uuid.UUID `json:"file_ids"`
	ClientID string      `json:"client_id"`
	// AM4: the thread it was typed in (nil = the first). AM6: a message names
	// no model — a round runs with the install's choice; it may name a
	// knowledge base, read only when the message is a question (knowledge.go).
	ThreadID        *uuid.UUID `json:"thread_id"`
	KnowledgeBaseID *uuid.UUID `json:"knowledge_base_id"`
	// AM10: the files among FileIDs the person marked as a form for this round
	FormFileIDs []uuid.UUID `json:"form_file_ids"`
}

// message is the composer. It only routes: a file starts or resumes a run,
// text answers an open card or becomes a reply job. Nothing executes here.
func (a *API) message(w http.ResponseWriter, r *http.Request) {
	in, c, ok := a.ownedInstall(w, r)
	if !ok || !a.agentGate(w, r, in.Slug, false) {
		return
	}
	var req messageReq
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	req.Text = strings.TrimSpace(req.Text)
	if req.Text == "" && len(req.FileIDs) == 0 {
		badRequest(w, "EMPTY")
		return
	}
	if len([]rune(req.Text)) > 4000 {
		say(w, http.StatusBadRequest, "TOO_LONG")
		return
	}
	ctx := r.Context()
	var clientID *string
	if req.ClientID != "" {
		clientID = &req.ClientID
	}
	if !a.threadOf(w, r, in, req.ThreadID) {
		return
	}
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "message")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck

	var files []File
	for _, id := range req.FileIDs {
		f, err := a.store.GetFile(ctx, c.TenantUUID, c.UserID, id)
		if err != nil || f.InstallID != in.ID {
			say(w, http.StatusBadRequest, "BAD_FILE")
			return
		}
		files = append(files, f)
	}
	payload := UserMessage{Text: req.Text, Status: "pending"}
	if len(files) > 0 {
		payload.Files, payload.Status = FileRefs(files), "handled"
	}
	item, created, err := a.store.AddItemIn(ctx, tx, in.ID, req.ThreadID, c.TenantUUID, c.UserID, nil, payload, clientID)
	if err != nil {
		a.fail(w, err, "message")
		return
	}
	if !created { // a resend of the same message: nothing more to do
		writeJSON(w, http.StatusOK, map[string]any{"item": item, "duplicate": true})
		return
	}
	// AM10: a form dropped into the round is told from the round's data
	marked := map[uuid.UUID]bool{}
	for _, id := range req.FormFileIDs {
		marked[id] = true
	}
	var forms []File
	if m, err := a.manifestOf(ctx, in); err == nil {
		forms, files = SplitForms(m, files, marked, req.Text)
	}
	o := roundOpts{thread: req.ThreadID}
	if len(files) == 0 {
		o.knowledge = req.KnowledgeBaseID
	}
	route, runID := "", (*uuid.UUID)(nil)
	if len(files) > 0 || len(forms) == 0 || !ReadsAsFormRequest(req.Text) {
		// the data (or the words) take their usual route; a form request with
		// nothing but the form is the form's alone
		if len(files) > 0 || len(forms) == 0 {
			route, runID, err = a.route(ctx, tx, in, c, item, req.Text, files, o)
			if err != nil {
				a.fail(w, err, "message")
				return
			}
		}
	}
	if len(forms) > 0 {
		target, err := a.formTarget(ctx, in, o, runID)
		if err != nil {
			a.fail(w, err, "message")
			return
		}
		for _, f := range forms {
			if _, err := a.roundForm(ctx, tx, in, c, o, target, f); err != nil {
				a.fail(w, err, "message")
				return
			}
		}
		if route == "" {
			route = "form"
			if target != nil {
				runID = &target.ID
			}
			if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_thread_items SET payload = payload || jsonb_build_object('status', 'handled', 'handled_as', 'form'),
				rev = nextval('agent_market.thread_rev') WHERE id = $1`, item.ID); err != nil {
				a.fail(w, err, "message")
				return
			}
		}
	}
	out := map[string]any{"item": item, "route": route, "run_id": runID}
	if len(files) > 0 && req.KnowledgeBaseID != nil {
		// AM6: a knowledge base answers questions; it never feeds a round's figures
		note := Text("ข้อความนี้แนบไฟล์ จึงไม่ได้ใช้คลังความรู้ — คลังความรู้ใช้ตอบคำถามเท่านั้น ตัวเลขในเอกสารมาจากไฟล์และการคำนวณของ agent")
		// in the language of the run it is said about, when there is one
		if _, _, err := a.store.AddItemIn(a.store.InLangOfRun(ctx, tx, runID), tx, in.ID, req.ThreadID, c.TenantUUID, c.UserID, runID,
			Notice{Text: note}, nil); err != nil {
			a.fail(w, err, "message")
			return
		}
		out["notice"] = note
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "message")
		return
	}
	writeJSON(w, http.StatusOK, out)
}

// roundOpts: the thread a message was typed in, whether the person already
// said "start a new round" (the chip, the button), and the knowledge base a
// question was asked with (AM6: read only if the message becomes a reply).
type roundOpts struct {
	thread    *uuid.UUID
	newRound  bool
	knowledge *uuid.UUID
}

func (a *API) route(ctx context.Context, tx pgx.Tx, in Install, c middleware.Caller, item ThreadItem, text string,
	files []File, o roundOpts) (string, *uuid.UUID, error) {
	latest, lerr := a.store.LatestRunIn(ctx, in.ID, o.thread)
	hasLatest := lerr == nil
	// AM4: after a finished round, a new one starts only when the person says
	// so — the chip, or "เริ่มรอบใหม่" pressed since that round.
	finished := hasLatest && latest.Status == RunSucceeded
	if finished && !o.newRound {
		started, err := a.store.RoundStartedSince(ctx, tx, in.ID, o.thread, latest.CreatedAt)
		if err != nil {
			return "", nil, err
		}
		o.newRound = started
	}
	ids := make([]uuid.UUID, 0, len(files))
	for _, f := range files {
		ids = append(ids, f.ID)
	}
	link := func(run uuid.UUID, handledAs string) error {
		_, err := tx.Exec(ctx, `UPDATE agent_market.agent_thread_items SET run_id = $2,
			payload = payload || jsonb_build_object('status', 'handled', 'run_id', $4::text, 'handled_as', $3::text),
			rev = nextval('agent_market.thread_rev') WHERE id = $1`, item.ID, run, handledAs, run.String())
		return err
	}
	if len(files) > 0 {
		switch {
		case hasLatest && latest.Status == RunAwaitingInput:
			// A parked run waiting for files: the upload resumes the same run.
			if _, err := a.store.AttachFiles(ctx, tx, c.TenantUUID, c.UserID, latest.ID, ids, "active"); err != nil {
				return "", nil, err
			}
			if text != "" {
				if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_runs SET message = trim(message || ' ' || $2) WHERE id = $1`,
					latest.ID, text); err != nil {
					return "", nil, err
				}
			}
			if _, err := a.store.Resume(ctx, tx, latest.ID); err != nil {
				return "", nil, err
			}
			return "resumed", &latest.ID, link(latest.ID, "files_resume")
		case hasLatest && (latest.Status == RunQueued || latest.Status == RunRunning):
			// Numbers not reported yet: ask once, include now or keep for a new round.
			if _, err := a.store.AttachFiles(ctx, tx, c.TenantUUID, c.UserID, latest.ID, ids, "pending_decision"); err != nil {
				return "", nil, err
			}
			if err := a.lateFileCard(ctx, tx, latest, files); err != nil {
				return "", nil, err
			}
			return "late_file", &latest.ID, link(latest.ID, "late_file")
		case finished && !o.newRound:
			// AM4: files after a finished round — never slipped into that round,
			// never silently a new one: ask (the files wait, unused, until then).
			return "intent", nil, a.intentChip(ctx, tx, in, c, item, latest, text, files, o)
		default:
			run, err := a.newRun(ctx, tx, in, c, text, ids, o)
			if err != nil {
				return "", nil, err
			}
			return "new_run", &run.ID, link(run.ID, "new_run")
		}
	}
	// Text only.
	if hasLatest && !IsTerminal(latest.Status) {
		cards, err := a.store.RunCards(ctx, latest.ID)
		if err != nil {
			return "", nil, err
		}
		for i := len(cards) - 1; i >= 0; i-- {
			cd := cards[i]
			if cd.Status != "open" || cd.Kind == "late_file" {
				continue
			}
			// The user typed a sentence instead of touching the card.
			if _, changed, err := a.store.MarkInterpreting(ctx, tx, cd.ID, text, nil); err != nil {
				return "", nil, err
			} else if changed {
				_ = a.store.SyncCardItem(ctx, tx, Card{ID: cd.ID, InstallID: cd.InstallID, Status: "interpreting", Attempts: cd.Attempts})
				if _, err := a.store.Resume(ctx, tx, latest.ID); err != nil {
					return "", nil, err
				}
				return "answer_text", &latest.ID, link(latest.ID, "answer_text")
			}
		}
	}
	if finished {
		switch {
		case o.newRound && !ReadsAsAboutLastRound(text):
			run, err := a.newRun(ctx, tx, in, c, text, nil, o)
			if err != nil {
				return "", nil, err
			}
			return "new_run", &run.ID, link(run.ID, "new_run")
		case !o.newRound && !ReadsAsAboutLastRound(text) && !SocialOnly(text):
			// AM4: an instruction after a finished round: ask, never guess.
			return "intent", nil, a.intentChip(ctx, tx, in, c, item, latest, text, nil, o)
		}
	} else if !hasLatest || latest.Status == RunFailed || latest.Status == RunCancelled {
		if o.newRound || wantsNewRound(text) {
			run, err := a.newRun(ctx, tx, in, c, text, nil, o)
			if err != nil {
				return "", nil, err
			}
			return "new_run", &run.ID, link(run.ID, "new_run")
		}
	}
	// Anything else is a reply job (format request, a question about the numbers…).
	var parent *uuid.UUID
	if hasLatest {
		parent = &latest.ID
	}
	// AM6: only here is a knowledge base read — the message is a question the
	// agent answers, not a round's input
	var k *KnowledgeContext
	if o.knowledge != nil && len(files) == 0 {
		k = a.knowledgeFor(ctx, c, *o.knowledge, text)
	}
	job, err := a.replyJob(ctx, tx, in, c, item.ID, text, parent, o.thread, k)
	if err != nil {
		return "", nil, err
	}
	return "reply", &job.ID, link(job.ID, "reply")
}

// newRoundWords: what a typed message has in it when it asks for a new run, in either language (words.go:
// a Thai word anywhere, an English one as whole words). They read what a person typed; nothing here is
// said to anyone. "month-end close" is the example a failed run's own sentence gives (failure.go).
var newRoundWords = []string{"รอบใหม่", "ทำใหม่", "เริ่มใหม่", "new run", "ปิดงบเดือน", "ปิดงบ",
	// AM11
	"another run", "do it again", "redo", "run it again", "start again", "start over", "restart", "month-end close",
	"month end close", "close the books", "close the month"}

func wantsNewRound(text string) bool { return HasWord(text, newRoundWords...) }

// newRun starts a round. It carries the install's settings and remembered
// answers (the runner reads them) and nothing of an earlier round: no file,
// no figure, no answer, no typed instruction (AM4).
func (a *API) newRun(ctx context.Context, tx pgx.Tx, in Install, c middleware.Caller, text string, fileIDs []uuid.UUID,
	o roundOpts) (Run, error) {
	def, err := a.store.DefinitionByID(ctx, in.DefinitionID)
	if err != nil {
		return Run{}, err
	}
	formats := json.RawMessage(`["docx","pptx"]`)
	if defs, err := a.store.ListDefaults(ctx, in.ID); err == nil {
		for _, d := range defs {
			if d.Key == "formats" {
				formats = d.Value
			}
		}
	}
	// A new round supersedes every card still open on an older run — a
	// question or an "include this file?" left on a finished round included.
	if _, err := a.store.SupersedeInstallCards(ctx, tx, in.ID, "superseded"); err != nil {
		return Run{}, err
	}
	// AM6: the model is the install's choice, as it is when the round starts
	model := in.ModelChoice
	if model == "" {
		model = ModelAuto
	}
	if prev, err := a.store.LatestRunIn(ctx, in.ID, o.thread); err == nil && !IsTerminal(prev.Status) {
		if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_runs SET cancel_requested = true,
			status = CASE WHEN status IN ('queued','awaiting_input') THEN 'cancelled' ELSE status END,
			status_reason = 'superseded_by_new_round', finished_at = CASE WHEN status IN ('queued','awaiting_input') THEN now() ELSE finished_at END
		WHERE id = $1`, prev.ID); err != nil {
			return Run{}, err
		}
		// AM6: a round that was stopped delivered nothing, and is charged nothing
		if err := a.store.Settle(ctx, tx, prev.ID); err != nil {
			return Run{}, err
		}
	}
	run, _, err := a.store.CreateRun(ctx, tx, Run{InstallID: in.ID, TenantID: c.TenantUUID, UserID: c.UserID, Kind: KindRun,
		Message: text, DefinitionVersion: def.Version, Checksum: def.Checksum, EngineChecksum: def.EngineChecksum,
		BindingVersion: in.BindingVersion, Formats: formats, ThreadID: o.thread, ModelChoice: &model}, "")
	if err != nil {
		return run, err
	}
	if len(fileIDs) > 0 {
		if _, err := a.store.AttachFiles(ctx, tx, c.TenantUUID, c.UserID, run.ID, fileIDs, "active"); err != nil {
			return run, err
		}
	}
	rid := run.ID
	_, _, err = a.store.AddItem(ctx, tx, in.ID, c.TenantUUID, c.UserID, &rid, QueuedProgress(run), nil)
	return run, err
}

// lateFileCard asks about files that arrived while a round was still working:
// include them in this round, or keep them for the next one. (Files after a
// round has finished get the round-boundary chip instead — rounds.go.)
func (a *API) lateFileCard(ctx context.Context, tx pgx.Tx, run Run, files []File) error {
	names := []string{}
	for _, f := range files {
		names = append(names, f.Name)
	}
	// AM11: the card is the run's, so it is in the run's language — a card's questions are kept as they
	// were asked, in one language
	l := run.Lang()
	ctx = InRunLang(ctx, run)
	q := Question{Key: "late_file", Code: "late_file", Severity: "blocking", Header: T(l, "ไฟล์เพิ่ม"),
		Question: Text("ได้รับ %s ระหว่างที่กำลังทำรอบนี้ จะให้รวมในรอบนี้ หรือเก็บไว้ทำรอบใหม่ครับ", FileNames(names)).In(l),
		Options: []Option{
			{Label: T(l, "รวมในรอบนี้"), Description: T(l, "ตรวจไฟล์ใหม่แล้วคำนวณก่อนส่งผล"), Value: json.RawMessage(`"include"`)},
			{Label: T(l, "เก็บไว้ทำรอบใหม่"), Description: T(l, "รอบนี้ใช้ไฟล์เดิม รอบใหม่เริ่มต่อทันทีด้วยไฟล์นี้เท่านั้น"), Value: json.RawMessage(`"next_round"`)},
		},
		Default: json.RawMessage(`"include"`), DefaultLabel: T(l, "รวมในรอบนี้")}
	qs, _ := json.Marshal([]Question{q})
	card, err := a.store.CreateCard(ctx, tx, Card{RunID: run.ID, InstallID: run.InstallID, TenantID: run.TenantID,
		UserID: run.UserID, Moment: "run", Kind: "late_file", Blocking: false, Questions: qs})
	if err != nil {
		return err
	}
	p := CardItemPayload(card, NoText, NoText)
	p.Files = FileRefs(files)
	rid := run.ID
	_, _, err = a.store.AddItem(ctx, tx, run.InstallID, run.TenantID, run.UserID, &rid, p, nil)
	return err
}

// ── answering a card ─────────────────────────────────────────────────────

type answerReq struct {
	Answers map[string]Submitted `json:"answers"`
	Text    string               `json:"text"`
	Upload  []uuid.UUID          `json:"file_ids"`
}

func (a *API) answer(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var req answerReq
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	card, err := a.store.GetCard(ctx, c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "card")
		return
	}
	if card.Status != "open" {
		// Idempotent and stale-safe: a second submit, or a submit of a card
		// that was superseded, returns what is recorded and changes nothing.
		writeJSON(w, http.StatusOK, map[string]any{"card": card, "changed": false})
		return
	}
	var questions []Question
	if err := json.Unmarshal(card.Questions, &questions); err != nil {
		a.fail(w, err, "card")
		return
	}
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "card")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	run, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, card.RunID)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if in, err := a.store.InstallByID(ctx, run.InstallID); err == nil && !a.agentGate(w, r, in.Slug, false) {
		return
	}
	// AM6: what the person clicked is a label as they were sent it — by the rule of the card's language,
	// which is its run's (AM11)
	questions = ShownQuestions(run.Lang(), questions)
	// AM11: what an answer writes for its run — an assumption, a remembered default, a notice — is in
	// the run's language; what is said back to this request is in the request's
	ctx = InRunLang(ctx, run)
	answers, texts, err := ResolveSubmission(questions, req.Answers, card.Moment, card.ID.String())
	if err != nil {
		say(w, http.StatusUnprocessableEntity, "BAD_ANSWER", "detail", answerProblem(langOfWriter(w), err))
		return
	}
	if strings.TrimSpace(req.Text) != "" && len(answers) == 0 {
		for _, q := range questions {
			texts[q.Key] = strings.TrimSpace(req.Text)
		}
	}
	if len(texts) > 0 {
		partial, _ := json.Marshal(answers)
		all := []string{}
		for _, t := range texts {
			all = append(all, t)
		}
		cd, changed, err := a.store.MarkInterpreting(ctx, tx, card.ID, strings.Join(dedupe(all), " / "), partial)
		if err != nil {
			a.fail(w, err, "card")
			return
		}
		if changed {
			_ = a.store.SyncCardItem(ctx, tx, cd)
			if _, err := a.store.Resume(ctx, tx, run.ID); err != nil {
				a.fail(w, err, "run")
				return
			}
		}
		if err := tx.Commit(ctx); err != nil {
			a.fail(w, err, "card")
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"card": cd, "changed": changed, "status": "interpreting"})
		return
	}
	if card.Blocking {
		for _, q := range questions {
			if _, ok := answers[q.Key]; !ok && q.Severity != "cosmetic" {
				say(w, http.StatusUnprocessableEntity, "ANSWER_REQUIRED")
				return
			}
		}
	}
	ab, _ := json.Marshal(answers)
	by := c.UserID
	cd, changed, err := a.store.CloseCard(ctx, tx, card.ID, "answered", ab, "card", &by)
	if err != nil {
		a.fail(w, err, "card")
		return
	}
	if !changed {
		writeJSON(w, http.StatusOK, map[string]any{"card": cd, "changed": false})
		return
	}
	if err := a.store.SyncCardItem(ctx, tx, cd); err != nil && !errors.Is(err, ErrNotFound) {
		a.fail(w, err, "card")
		return
	}
	switch card.Kind {
	case "late_file":
		if err := a.applyLateFile(ctx, tx, run, answers["late_file"].Value, c); err != nil {
			a.fail(w, err, "late file")
			return
		}
	case "output_format":
		if err := a.applyFormats(ctx, tx, run, answers, c); err != nil {
			a.fail(w, err, "formats")
			return
		}
	default:
		if err := a.store.MergeAnswers(ctx, tx, run.ID, answers); err != nil {
			a.fail(w, err, "run")
			return
		}
		var skipped []Assumption
		for _, q := range questions {
			if ans, ok := answers[q.Key]; ok && ans.Via == "skip" {
				// printed in the run's documents under "assumptions used": the run's language
				skipped = append(skipped, Assumption{Code: q.Code, Source: "skip", ChangesNumbers: q.Severity != "cosmetic",
					Text: q.Header + T(run.Lang(), ": ใช้ค่าแนะนำ “") + ans.Label + T(run.Lang(), "” เพราะคุณกดข้ามคำถามนี้")})
			}
		}
		if err := a.store.AddAssumptions(ctx, tx, run.ID, skipped); err != nil {
			a.fail(w, err, "run")
			return
		}
		if n, err := a.store.RememberAnswers(ctx, tx, run, card.ID, questions, answers); err != nil {
			a.fail(w, err, "remember")
			return
		} else if n > 0 {
			if err := a.store.AddNotice(ctx, tx, run, Notice{Text: Text("จำไว้เป็นค่าตั้งต้นรอบหน้า · แก้ได้"), Link: "defaults"}); err != nil {
				a.fail(w, err, "notice")
				return
			}
		}
		// AM10: "which files" makes the run's set and the install's setting
		if err := a.store.ApplyOutputSet(ctx, tx, run, answers); err != nil {
			a.fail(w, err, "formats")
			return
		}
		// A question that did not make the round wait, answered after its
		// files were delivered: re-render only. AM10: the round itself stays
		// finished — it used to go back into the queue beside its own
		// re-render, which is claimed first and refused a round that "has not
		// finished" (a red card for an answer about presentation).
		delivered := run.Status == RunSucceeded && !card.Blocking
		if !delivered {
			if _, err := a.store.Resume(ctx, tx, run.ID); err != nil {
				a.fail(w, err, "run")
				return
			}
		}
		if delivered {
			// AM10: an answer about which files alone makes the files the round
			// still lacks — and none when it already has them all.
			if more, only := OutputSetAlone(run, answers); only {
				if len(more) > 0 {
					if _, err := a.renderJob(ctx, tx, run, more, "output_set"); err != nil {
						a.fail(w, err, "render")
						return
					}
				}
			} else if _, err := a.renderJob(ctx, tx, run, nil, "cosmetic_answer"); err != nil {
				a.fail(w, err, "render")
				return
			}
		}
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "card")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"card": cd, "changed": true})
}

func nonNil[T any](s []T) []T {
	if s == nil {
		return []T{}
	}
	return s
}

func dedupe(in []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range in {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	return out
}

func (a *API) applyLateFile(ctx context.Context, tx pgx.Tx, run Run, choice json.RawMessage, c middleware.Caller) error {
	var v string
	_ = json.Unmarshal(choice, &v)
	pending, err := a.store.PendingFiles(ctx, run.ID)
	if err != nil {
		return err
	}
	ids := make([]uuid.UUID, 0, len(pending))
	for _, f := range pending {
		ids = append(ids, f.ID)
	}
	switch v {
	case "include":
		_, err := tx.Exec(ctx, `UPDATE agent_market.agent_files SET status = 'active' WHERE id = ANY($1)`, ids)
		return err
	case "next_round":
		// The runner starts the next round with these files when this run ends.
		_, err := tx.Exec(ctx, `UPDATE agent_market.agent_files SET status = 'next_round' WHERE id = ANY($1)`, ids)
		if err != nil {
			return err
		}
		return a.store.MergeAnswers(ctx, tx, run.ID, map[string]Answer{"late_file": {Value: choice, Via: "card"}})
	case "new_round_with_previous", "new_round_only_new":
		// A card made before AM4. Either answer starts a round with only the
		// new files: a new round never inherits an earlier round's files, nor
		// its typed instruction (a month named there would close the wrong one).
		in, err := a.store.GetInstall(ctx, c.TenantUUID, c.UserID, run.InstallID)
		if err != nil {
			return err
		}
		nr, err := a.newRun(ctx, tx, in, c, "", nil, roundOpts{thread: run.ThreadID})
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `UPDATE agent_market.agent_files SET status = 'active', run_id = $2 WHERE id = ANY($1)`, ids, nr.ID)
		return err
	}
	return fmt.Errorf("unknown late-file choice %q", v)
}

func (a *API) applyFormats(ctx context.Context, tx pgx.Tx, run Run, answers map[string]Answer, c middleware.Caller) error {
	ans, ok := answers["output_format"]
	if !ok {
		return nil
	}
	var formats []string
	if err := json.Unmarshal(ans.Value, &formats); err != nil {
		var one string
		if json.Unmarshal(ans.Value, &one) == nil {
			formats = []string{one}
		}
	}
	_, err := a.renderJob(ctx, tx, run, formats, "format_card")
	_ = c
	return err
}

// renderJob queues a re-render of a finished run. It reads the run's stored
// computed table: no compute step runs again.
func (a *API) renderJob(ctx context.Context, q Querier, run Run, formats []string, why string) (Run, error) {
	params, _ := json.Marshal(map[string]any{"formats": formats, "why": why})
	parent := run.ID
	return func() (Run, error) {
		job, _, err := a.store.CreateRun(ctx, q, Run{InstallID: run.InstallID, ParentRunID: &parent, TenantID: run.TenantID,
			UserID: run.UserID, Kind: KindRender, Params: params, DefinitionVersion: run.DefinitionVersion,
			Checksum: run.Checksum, EngineChecksum: run.EngineChecksum, BindingVersion: run.BindingVersion}, "")
		return job, err
	}()
}

// ── runs ─────────────────────────────────────────────────────────────────

func (a *API) listRuns(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	runs, err := a.store.ListRuns(r.Context(), in.ID, 50)
	if err != nil {
		a.fail(w, err, "runs")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"runs": nonNil(runs)})
}

func (a *API) getRun(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	ctx := r.Context()
	run, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	out, what, err := a.runDetail(ctx, run)
	if err != nil {
		a.fail(w, err, what)
		return
	}
	// AM4: the owner sees every time an admin opened this run, and why.
	log, err := a.store.AccessLog(ctx, run.ID)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	out["access_log"] = log
	writeJSON(w, http.StatusOK, out)
}

// runDetail is a run's page: its steps, questions, files in and out.
func (a *API) runDetail(ctx context.Context, run Run) (map[string]any, string, error) {
	steps, err := a.store.ListSteps(ctx, run.ID)
	if err != nil {
		return nil, "steps", err
	}
	cards, err := a.store.RunCards(ctx, run.ID)
	if err != nil {
		return nil, "cards", err
	}
	arts, err := a.store.RunArtifacts(ctx, run.ID)
	if err != nil {
		return nil, "artifacts", err
	}
	files, err := a.store.RunFiles(ctx, run.ID)
	if err != nil {
		return nil, "files", err
	}
	children, _ := a.store.ChildRuns(ctx, run.ID)
	for _, ch := range children {
		s2, _ := a.store.ListSteps(ctx, ch.ID)
		steps = append(steps, s2...)
	}
	// Empty lists are [] on the wire, never null.
	steps, cards, arts, files, children = nonNil(steps), nonNil(cards), nonNil(arts), nonNil(files), nonNil(children)
	in, _ := a.store.InstallByID(ctx, run.InstallID)
	spent, _ := a.store.SpentThisMonth(ctx, run.InstallID)
	return map[string]any{"run": run, "steps": steps, "clarifications": cards, "artifacts": arts,
		"files": files, "children": children, "install": map[string]any{"id": in.ID, "title": in.Title, "slug": in.Slug},
		"budget": map[string]any{"credits": in.BudgetCredits, "spent_this_month": spent}}, "", nil
}

// computed serves the run's computed table (the evidence view's numbers).
func (a *API) computed(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	ctx := r.Context()
	run, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	a.serveComputed(w, r, run)
}

// serveComputed writes a run's computed table (the owner's page; an admin's opened view).
func (a *API) serveComputed(w http.ResponseWriter, r *http.Request, run Run) {
	ctx := r.Context()
	var key string
	err := a.store.Pool.QueryRow(ctx, `SELECT output->>'computed_key' FROM agent_market.agent_run_steps
		WHERE run_id = $1 AND name = 'compute' AND status = 'succeeded' ORDER BY seq DESC LIMIT 1`, run.ID).Scan(&key)
	if err != nil || key == "" {
		say(w, http.StatusNotFound, "NOT_COMPUTED")
		return
	}
	body, _, _, err := a.files.Download(ctx, key)
	if err != nil {
		a.fail(w, err, "computed")
		return
	}
	defer body.Close()
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = io.Copy(w, body)
}

func (a *API) cancel(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	run, err := a.store.RequestCancel(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if run.Status == RunCancelled {
		_, _ = a.store.SupersedeOpenCards(r.Context(), a.store.Pool, run.ID, "cancelled")
		if it, err := a.store.ItemFor(r.Context(), run.InstallID, "progress", "run_id", run.ID.String()); err == nil {
			_ = a.store.PatchItem(r.Context(), a.store.Pool, it.ID, ItemPatch{Status: RunCancelled, Outcome: OutcomeCancelled})
		}
	}
	// Cancelling twice returns the same state, never an error.
	writeJSON(w, http.StatusOK, map[string]any{"run": run})
}

// runnerOffers reports whether the latest healthy runner binds a capability.
func (a *API) runnerOffers(ctx context.Context, capability string) bool {
	st, err := a.store.LatestRunner(ctx, a.cfg.RunnerStaleAfter)
	if err != nil || !st.Healthy {
		return false
	}
	var caps map[string]json.RawMessage
	_ = json.Unmarshal(st.Capabilities, &caps)
	_, ok := caps[capability]
	return ok
}

func (a *API) formats(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var body struct {
		Formats []string `json:"formats"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil || len(body.Formats) == 0 {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	run, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if run.Status != RunSucceeded {
		say(w, http.StatusConflict, "RUN_NOT_FINISHED")
		return
	}
	if in, err := a.store.InstallByID(ctx, run.InstallID); err == nil && !a.agentGate(w, r, in.Slug, false) {
		return
	}
	offered := map[string]bool{"docx": true, "pptx": true, "xlsx": true}
	if in, err := a.store.GetInstall(ctx, c.TenantUUID, c.UserID, run.InstallID); err == nil {
		if def, err := a.store.DefinitionByID(ctx, in.DefinitionID); err == nil {
			if m, err := ParseManifest(def.Manifest); err == nil {
				offered = map[string]bool{}
				for _, o := range m.Outputs {
					offered[o.Format] = true
				}
			}
		}
	}
	for _, f := range body.Formats {
		if !offered[f] {
			say(w, http.StatusBadRequest, "BAD_FORMAT", "format", f)
			return
		}
		// A PDF is the report converted by LibreOffice (AM2): only while a
		// runner offers the converter.
		if f == "pdf" && !a.runnerOffers(ctx, "deliverable.pdf") {
			say(w, http.StatusConflict, "PDF_UNAVAILABLE")
			return
		}
	}
	job, err := a.renderJob(ctx, a.store.Pool, run, body.Formats, "api")
	if err != nil {
		a.fail(w, err, "render")
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"job": job})
}

// ── mapping preview (install wizard) ─────────────────────────────────────

func (a *API) mappingPreview(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	var body struct {
		InstallID uuid.UUID `json:"install_id"`
		FileID    uuid.UUID `json:"file_id"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	in, err := a.store.GetInstall(ctx, c.TenantUUID, c.UserID, body.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if !a.agentGate(w, r, in.Slug, false) {
		return
	}
	if _, err := a.store.GetFile(ctx, c.TenantUUID, c.UserID, body.FileID); err != nil {
		a.fail(w, err, "file")
		return
	}
	params, _ := json.Marshal(map[string]any{"file_id": body.FileID})
	job, _, err := a.store.CreateRun(ctx, a.store.Pool, Run{InstallID: in.ID, TenantID: c.TenantUUID, UserID: c.UserID,
		Kind: KindMappingPreview, Params: params, DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum,
		BindingVersion: in.BindingVersion}, "")
	if err != nil {
		a.fail(w, err, "preview")
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"job": job})
}

// ── downloads ────────────────────────────────────────────────────────────

func contentDisposition(name string) string {
	ascii := strings.Map(func(r rune) rune {
		if r < 32 || r > 126 || r == '"' || r == '\\' {
			return '_'
		}
		return r
	}, name)
	return fmt.Sprintf(`attachment; filename="%s"; filename*=UTF-8''%s`, ascii, url.PathEscape(name))
}

var mimeByFormat = map[string]string{
	"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	"pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
	"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
	"pdf":  "application/pdf",
}

// artifactMime: a filled customer template (format "template") is served as
// what its extension says.
func artifactMime(art Artifact) string {
	if art.Format == "template" {
		ext := strings.ToLower(filepath.Ext(art.Name))
		return mimeByFormat[strings.TrimPrefix(ext, ".")]
	}
	return mimeByFormat[art.Format]
}

func (a *API) downloadArtifact(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	art, err := a.store.GetArtifact(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "artifact")
		return
	}
	a.serveArtifact(w, r, art)
}

func (a *API) downloadFile(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	f, err := a.store.GetFile(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "file")
		return
	}
	body, _, _, err := a.files.Download(r.Context(), f.StorageKey)
	if err != nil {
		a.fail(w, err, "file")
		return
	}
	defer body.Close()
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", contentDisposition(f.Name))
	_, _ = io.Copy(w, body)
}

// ── theme registry ───────────────────────────────────────────────────────

func (a *API) getTheme(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	theme, version, _, err := a.store.LatestTheme(r.Context(), c.TenantUUID)
	if err != nil {
		a.fail(w, err, "theme")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"theme": theme, "version": version,
		"presets": []map[string]any{{"id": "navy", "name": Text("กรมท่า")}, {"id": "teal", "name": Text("เขียวหยก")},
			{"id": "charcoal", "name": Text("ถ่านหิน")}}})
}

func (a *API) putTheme(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	var body json.RawMessage
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	v, err := a.store.SaveTheme(r.Context(), c.TenantUUID, c.UserID, body, nil)
	if err != nil {
		a.fail(w, err, "theme")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"version": v})
}
