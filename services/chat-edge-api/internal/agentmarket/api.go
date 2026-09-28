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
	return &API{cfg: cfg, store: store, files: files, log: log.With("component", "agent-market"), closeFn: pool.Close}, nil
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
	return &API{cfg: cfg, store: store, files: files, log: log, closeFn: func() {}}
}

// Close releases the pool.
func (a *API) Close() {
	if a != nil && a.closeFn != nil {
		a.closeFn()
	}
}

// Routes mounts the control plane (inside chat-edge's authenticated group).
func (a *API) Routes(r chi.Router) {
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
		r.Get("/admin/org", a.adminOrg)
		r.Put("/admin/org", a.adminPutOrg)
		r.Put("/admin/members/{email}", a.adminPutMember)
		r.Delete("/admin/members/{email}", a.adminDeleteMember)
		r.Put("/admin/departments/{name}", a.adminPutDepartment)
		r.Delete("/admin/departments/{name}", a.adminDeleteDepartment)
		r.Get("/admin/spend", a.adminSpend)
		r.Get("/admin/problems", a.adminProblems)
		r.Post("/admin/problems/{id}/close", a.adminCloseProblem)
		r.Get("/admin/catalog", a.adminCatalog)
		r.Put("/admin/catalog/{slug}", a.adminPutCatalog)
		r.Post("/admin/catalog/{slug}/sample", a.adminSample)
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
		r.Post("/mapping-preview", a.mappingPreview)
		r.Post("/cards/{id}/answer", a.answer)
		r.Get("/runs/{id}", a.getRun)
		r.Get("/runs/{id}/computed", a.computed)
		r.Post("/runs/{id}/cancel", a.cancel)
		r.Post("/runs/{id}/retry", a.retry)     // AM5: a failed round, tried again with the files it had
		r.Post("/runs/{id}/restore", a.restore) // AM5: a document that was left out, brought back
		r.Post("/runs/{id}/formats", a.formats)
		r.Get("/artifacts/{id}/download", a.downloadArtifact)
		r.Get("/files/{id}/download", a.downloadFile)
		r.Get("/theme", a.getTheme)
		r.Put("/theme", a.putTheme)
	})
}

// ── helpers ──────────────────────────────────────────────────────────────

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// writeErr sends a message a person reads. AM5: whatever a caller put into
// it, nothing technical leaves (the last line of defence; the callers name
// things in words).
func writeErr(w http.ResponseWriter, status int, code, msg string) {
	if LooksLikeError(msg) {
		if msg = ForPerson(msg); msg == "" {
			msg = userMessages["AGENT_MARKET_ERROR"]
		}
	}
	writeJSON(w, status, map[string]any{"error": map[string]string{"code": code, "message": msg}})
}

func (a *API) fail(w http.ResponseWriter, err error, what string) {
	if errors.Is(err, ErrNotFound) {
		name, ok := whatThai[what]
		if !ok {
			name = "รายการ"
		}
		say(w, http.StatusNotFound, "NOT_FOUND", "what", name)
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
	// A setting the person gave that we know and that is wrong is refused, not dropped.
	for k, v := range req.Settings {
		if validSettingKey(k) && !validSettingValue(k, v) {
			say(w, http.StatusBadRequest, "BAD_VALUE", "detail", settingProblem(k, v))
			return
		}
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
		a.sayTo(w, r, http.StatusConflict, "CAPABILITY_MISSING", "detail", missingThai(err))
		return
	}
	bindings, _ := json.Marshal(res)
	title := m.ShortName
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
			if !validSettingKey(k) || !validSettingValue(k, v) {
				continue
			}
			if err := a.store.SetDefault(ctx, tx, in.ID, Default{Key: k, Value: v, Source: "install",
				Label: settingLabel(k), Note: "ตั้งตอนติดตั้ง"}); err != nil {
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
		if _, _, err := a.store.AddItem(ctx, tx, in.ID, c.TenantUUID, c.UserID, nil, "greeting", GreetingFor(m), nil); err != nil {
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
	return strings.HasPrefix(k, "answers.")
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
	case "policy":
		// the structured policy table: limits come from here only, never from a document
		return len(PolicyProblems(raw)) == 0
	case "holidays":
		return len(HolidayProblems(raw)) == 0
	}
	return true
}

// settingProblem says what is wrong with a refused setting (for a table, which entries).
func settingProblem(key string, v json.RawMessage) string {
	msg := "ค่าไม่ถูกต้อง: " + settingLabel(key)
	switch key {
	case "policy":
		msg += " — " + strings.Join(PolicyProblems(v), " · ")
	case "holidays":
		msg += " — " + strings.Join(HolidayProblems(v), " · ")
	}
	return msg
}

func settingLabel(k string) string {
	switch k {
	case "company":
		return "ชื่อบริษัทบนเอกสาร"
	case "formats":
		return "ชุดไฟล์ที่ต้องการ"
	case "currency":
		return "สกุลเงินหลัก"
	case "language":
		return "ภาษาของเอกสาร"
	case "materiality":
		return "เกณฑ์ความมีสาระสำคัญ (บาท)"
	case "mapping":
		return "การจับคู่คอลัมน์"
	case "dc_convention":
		return "การอ่านเดบิต/เครดิต"
	case "theme":
		return "ธีมเอกสาร"
	case "fiscal_year_start":
		return "เดือนเริ่มปีบัญชี"
	case "company_tax_id":
		return "เลขประจำตัวผู้เสียภาษีของบริษัท"
	case "company_branch":
		return "สาขาของบริษัท"
	case "policy":
		return "ตารางนโยบายการเบิก"
	case "holidays":
		return "ปฏิทินวันหยุด"
	case "policy_kb":
		return "เอกสารระเบียบ (ใช้เป็นบริบทเท่านั้น)"
	}
	return k
}

func (a *API) startSelfTest(ctx context.Context, in Install, def Definition) (Run, error) {
	run, _, err := a.store.CreateRun(ctx, a.store.Pool, Run{
		InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindSelfTest,
		Message: "ทดสอบกับข้อมูลตัวอย่าง", DefinitionVersion: def.Version, Checksum: def.Checksum,
		EngineChecksum: def.EngineChecksum, BindingVersion: in.BindingVersion,
	}, "")
	if err != nil {
		return run, err
	}
	if err := a.store.SetSelfTestRun(ctx, in.ID, run.ID); err != nil {
		return run, err
	}
	rid := run.ID
	_, _, err = a.store.AddItem(ctx, a.store.Pool, in.ID, in.TenantID, in.UserID, &rid, "progress",
		Progress{RunID: run.ID.String(), Kind: KindSelfTest, Status: RunQueued}, nil)
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
		Icon      string `json:"icon"`
		LastRun   *Run   `json:"last_run,omitempty"`
		Attention bool   `json:"attention"`
	}
	out := []row{}
	for _, i := range list {
		x := row{Install: i}
		if d, err := a.store.DefinitionByID(r.Context(), i.DefinitionID); err == nil {
			if m, err := ParseManifest(d.Manifest); err == nil {
				x.Icon = m.Icon
			}
		}
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
	spent, _ := a.store.SpentThisMonth(r.Context(), in.ID)
	writeJSON(w, http.StatusOK, map[string]any{"install": in, "manifest": def.Manifest, "defaults": nonNil(defaults),
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
	writeJSON(w, http.StatusOK, map[string]any{"defaults": nonNil(list)})
}

func (a *API) putDefault(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	key := chi.URLParam(r, "key")
	if !validSettingKey(key) {
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
	if !validSettingValue(key, body.Value) {
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", settingProblem(key, body.Value))
		return
	}
	label := body.Label
	if label == "" {
		label = settingLabel(key)
	}
	if err := a.store.SetDefault(r.Context(), a.store.Pool, in.ID, Default{Key: key, Value: body.Value, Source: "settings",
		Label: label, Note: "แก้ในหน้าค่าที่จำไว้"}); err != nil {
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
	// AM4: the thread it was typed in (nil = the first), and the model for
	// this round only (empty = the install's own choice).
	ThreadID    *uuid.UUID `json:"thread_id"`
	ModelChoice string     `json:"model_choice"`
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
	if req.ModelChoice != "" && !a.modelChoiceAllowed(w, r, in, req.ModelChoice) {
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
	kind := "user_text"
	payload := map[string]any{"text": req.Text, "status": "pending"}
	if len(files) > 0 {
		kind = "user_files"
		fl := []map[string]any{}
		for _, f := range files {
			fl = append(fl, map[string]any{"id": f.ID, "name": f.Name, "size": f.Size})
		}
		payload["files"] = fl
		payload["status"] = "handled"
	}
	item, created, err := a.store.AddItemIn(ctx, tx, in.ID, req.ThreadID, c.TenantUUID, c.UserID, nil, kind, payload, clientID)
	if err != nil {
		a.fail(w, err, "message")
		return
	}
	if !created { // a resend of the same message: nothing more to do
		writeJSON(w, http.StatusOK, map[string]any{"item": item, "duplicate": true})
		return
	}
	route, runID, err := a.route(ctx, tx, in, c, item, req.Text, files, roundOpts{thread: req.ThreadID, model: req.ModelChoice})
	if err != nil {
		a.fail(w, err, "message")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "message")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"item": item, "route": route, "run_id": runID})
}

// roundOpts: the thread a message was typed in, the model for this round, and
// whether the person already said "start a new round" (the chip, the button).
type roundOpts struct {
	thread   *uuid.UUID
	model    string
	newRound bool
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
	job, err := a.replyJob(ctx, tx, in, c, item.ID, text, parent, o.thread)
	if err != nil {
		return "", nil, err
	}
	return "reply", &job.ID, link(job.ID, "reply")
}

func wantsNewRound(text string) bool {
	t := strings.ToLower(text)
	for _, w := range []string{"รอบใหม่", "ทำใหม่", "เริ่มใหม่", "new run", "ปิดงบเดือน", "ปิดงบ"} {
		if strings.Contains(t, w) {
			return true
		}
	}
	return false
}

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
	model := o.model
	if model == "" {
		model = in.ModelChoice
	}
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
	_, _, err = a.store.AddItem(ctx, tx, in.ID, c.TenantUUID, c.UserID, &rid, "progress",
		Progress{RunID: run.ID.String(), Kind: KindRun, Status: RunQueued}, nil)
	return run, err
}

// lateFileCard asks about files that arrived while a round was still working:
// include them in this round, or keep them for the next one. (Files after a
// round has finished get the round-boundary chip instead — rounds.go.)
func (a *API) lateFileCard(ctx context.Context, tx pgx.Tx, run Run, files []File) error {
	names := []string{}
	fl := []map[string]any{}
	for _, f := range files {
		names = append(names, f.Name)
		fl = append(fl, map[string]any{"id": f.ID, "name": f.Name, "size": f.Size})
	}
	q := Question{Key: "late_file", Code: "late_file", Severity: "blocking", Header: "ไฟล์เพิ่ม",
		Question: fmt.Sprintf("ได้รับ %s ระหว่างที่กำลังทำรอบนี้ จะให้รวมในรอบนี้ หรือเก็บไว้ทำรอบใหม่ครับ", strings.Join(names, ", ")),
		Options: []Option{
			{Label: "รวมในรอบนี้", Description: "ตรวจไฟล์ใหม่แล้วคำนวณก่อนส่งผล", Value: json.RawMessage(`"include"`)},
			{Label: "เก็บไว้ทำรอบใหม่", Description: "รอบนี้ใช้ไฟล์เดิม รอบใหม่เริ่มต่อทันทีด้วยไฟล์นี้เท่านั้น", Value: json.RawMessage(`"next_round"`)},
		},
		Default: json.RawMessage(`"include"`), DefaultLabel: "รวมในรอบนี้"}
	qs, _ := json.Marshal([]Question{q})
	card, err := a.store.CreateCard(ctx, tx, Card{RunID: run.ID, InstallID: run.InstallID, TenantID: run.TenantID,
		UserID: run.UserID, Moment: "run", Kind: "late_file", Blocking: false, Questions: qs})
	if err != nil {
		return err
	}
	p := CardItemPayload(card, "", "")
	fb, _ := json.Marshal(fl)
	p.Files = fb
	rid := run.ID
	_, _, err = a.store.AddItem(ctx, tx, run.InstallID, run.TenantID, run.UserID, &rid, "card", p, nil)
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
	answers, texts, err := ResolveSubmission(questions, req.Answers, card.Moment, card.ID.String())
	if err != nil {
		say(w, http.StatusUnprocessableEntity, "BAD_ANSWER", "detail", answerProblem(err))
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
				skipped = append(skipped, Assumption{Code: q.Code, Source: "skip", ChangesNumbers: q.Severity != "cosmetic",
					Text: q.Header + ": ใช้ค่าแนะนำ “" + ans.Label + "” เพราะคุณกดข้ามคำถามนี้"})
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
			rid := run.ID
			if _, _, err := a.store.AddItem(ctx, tx, run.InstallID, run.TenantID, run.UserID, &rid, "notice",
				map[string]any{"text": "จำไว้เป็นค่าตั้งต้นรอบหน้า · แก้ได้", "link": "defaults", "run_id": run.ID.String()}, nil); err != nil {
				a.fail(w, err, "notice")
				return
			}
		}
		if _, err := a.store.Resume(ctx, tx, run.ID); err != nil {
			a.fail(w, err, "run")
			return
		}
		if run.Status == RunSucceeded && !card.Blocking {
			// A cosmetic answer after the files were delivered: re-render only.
			if _, err := a.renderJob(ctx, tx, run, nil, "cosmetic_answer"); err != nil {
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
			_ = a.store.PatchItem(r.Context(), a.store.Pool, it.ID, map[string]any{"status": RunCancelled})
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
		"presets": []map[string]string{{"id": "navy", "name": "กรมท่า"}, {"id": "teal", "name": "เขียวหยก"}, {"id": "charcoal", "name": "ถ่านหิน"}}})
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
