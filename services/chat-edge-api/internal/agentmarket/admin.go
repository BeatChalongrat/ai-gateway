package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── The organisation's admins (AM3) ──────────────────────────────────────
//
// Admins keep the access list (the org-wide switch, the people, their
// departments and roles), the budgets (per person, per department), see
// runs and spend per person and per agent, and own the catalog and its
// problem reports with the publishers: a publisher publishes or unpublishes
// an agent and reads and closes what people reported; only an admin disables
// one for everyone (new runs stop at once, history stays). Every change
// names who made it.

func (a *API) requireAdmin(w http.ResponseWriter, r *http.Request) bool {
	if !accessOf(r).Admin() {
		a.sayTo(w, r, http.StatusForbidden, "NOT_ADMIN")
		return false
	}
	return true
}

func (a *API) requirePublisher(w http.ResponseWriter, r *http.Request) bool {
	if !accessOf(r).Publisher() {
		a.sayTo(w, r, http.StatusForbidden, "NOT_PUBLISHER")
		return false
	}
	return true
}

// ── org, members, departments ────────────────────────────────────────────

func (a *API) adminOrg(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	acc := accessOf(r)
	ctx := r.Context()
	o, err := a.store.OrgSettingsFor(ctx, acc.OrgID)
	if err != nil {
		a.fail(w, err, "member")
		return
	}
	members, err := a.store.Members(ctx, acc.OrgID)
	if err != nil {
		a.fail(w, err, "member")
		return
	}
	listed := map[string]bool{}
	for i := range members {
		listed[members[i].Email] = true
		if a.cfg.IsEnvAdmin(members[i].Email) {
			members[i].EnvAdmin = true
			members[i].Role = RoleAdmin
		}
	}
	for _, e := range a.cfg.AdminEmails {
		if !listed[e] {
			members = append(members, Member{Email: e, Role: RoleAdmin, EnvAdmin: true})
		}
	}
	depts, err := a.store.Departments(ctx, acc.OrgID)
	if err != nil {
		a.fail(w, err, "department")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"org": o, "members": nonNil(members), "departments": nonNil(depts),
		"pricing": map[string]float64{"credits_per_usd": a.cfg.CreditsPerUSD, "thb_per_usd": a.cfg.THBPerUSD}})
}

var domainRe = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$`)

// optCredits reads a budget field: absent = unchanged, null = "use the
// default / no limit", a number ≥ 0 = that many credits.
func optCredits(raw json.RawMessage) (set bool, val *float64, ok bool) {
	if raw == nil {
		return false, nil, true
	}
	if string(raw) == "null" {
		return true, nil, true
	}
	var v float64
	if err := json.Unmarshal(raw, &v); err != nil {
		var s string
		if json.Unmarshal(raw, &s) != nil {
			return false, nil, false
		}
		s = strings.TrimSpace(strings.ReplaceAll(s, ",", ""))
		if s == "" {
			return true, nil, true
		}
		f, err := strconv.ParseFloat(s, 64)
		if err != nil {
			return false, nil, false
		}
		v = f
	}
	if v < 0 || v > 1e12 {
		return false, nil, false
	}
	return true, &v, true
}

func (a *API) adminPutOrg(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	c, _ := caller(r)
	acc := accessOf(r)
	var body struct {
		Name               *string         `json:"name"`
		Access             *string         `json:"access"`
		Domains            []string        `json:"domains"`
		UserMonthlyCredits json.RawMessage `json:"user_monthly_credits"`
		ContactName        *string         `json:"contact_name"`
		ContactEmail       *string         `json:"contact_email"`
		// AM4
		DocumentsMayLeave *bool `json:"documents_may_leave"`
		RetentionYears    *int  `json:"retention_years"`
		StorageWarnMB     *int  `json:"storage_warn_mb"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	// AM7: the whole request is checked before the row is read, so a refusal
	// changes nothing and logs nothing.
	if body.Access != nil {
		switch *body.Access {
		case AccessOff, AccessList, AccessEveryone:
		default:
			say(w, http.StatusBadRequest, "BAD_ACCESS")
			return
		}
	}
	var domains []string
	if body.Domains != nil {
		seen := map[string]bool{}
		for _, d := range body.Domains {
			d = strings.ToLower(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(d), "@")))
			if d == "" || seen[d] {
				continue
			}
			if !domainRe.MatchString(d) {
				say(w, http.StatusBadRequest, "BAD_EMAIL")
				return
			}
			seen[d] = true
			domains = append(domains, d)
		}
		sort.Strings(domains)
		if domains == nil {
			domains = []string{}
		}
	}
	budgetSet, budget, ok := optCredits(body.UserMonthlyCredits)
	if !ok || (budgetSet && budget == nil) {
		say(w, http.StatusBadRequest, "BAD_BUDGET") // the organisation's per-person default cannot be "none"
		return
	}
	contactEmail := ""
	if body.ContactEmail != nil {
		contactEmail = normEmail(*body.ContactEmail)
		if contactEmail != "" && !validEmail(contactEmail) {
			say(w, http.StatusBadRequest, "BAD_EMAIL")
			return
		}
	}
	if body.RetentionYears != nil && (*body.RetentionYears < 1 || *body.RetentionYears > 30) {
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", forReq(w, "ระยะเวลาเก็บต้องอยู่ระหว่าง 1–30 ปี"))
		return
	}
	if body.StorageWarnMB != nil && (*body.StorageWarnMB < 50 || *body.StorageWarnMB > 1_000_000) {
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", forReq(w, "ขนาดที่เตือนต้องอยู่ระหว่าง 50 MB ถึง 1,000,000 MB"))
		return
	}
	ctx := r.Context()
	budgetChanged := false
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		before, err := a.store.orgSettingsQ(ctx, tx, acc.OrgID, true)
		if err != nil {
			return nil, err
		}
		o := before
		if body.Name != nil {
			o.Name = strings.TrimSpace(*body.Name)
		}
		if body.Access != nil {
			o.Access = *body.Access
		}
		if body.Domains != nil {
			o.Domains = domains
		}
		if budgetSet {
			budgetChanged = *budget != o.UserMonthlyCredits
			o.UserMonthlyCredits = *budget
		}
		if body.ContactName != nil {
			o.ContactName = strings.TrimSpace(*body.ContactName)
		}
		if body.ContactEmail != nil {
			o.ContactEmail = contactEmail
		}
		if body.DocumentsMayLeave != nil {
			o.DocumentsMayLeave = *body.DocumentsMayLeave
		}
		if body.RetentionYears != nil {
			o.RetentionYears = *body.RetentionYears
		}
		if body.StorageWarnMB != nil {
			o.StorageWarnMB = *body.StorageWarnMB
		}
		if _, err := tx.Exec(ctx, `UPDATE agent_market.org_settings SET name = $2, access = $3, domains = $4,
			user_monthly_credits = $5, contact_name = $6, contact_email = $7, updated_by = $8, updated_at = now(),
			documents_may_leave = $9, retention_years = $10, storage_warn_mb = $11 WHERE org_id = $1`,
			acc.OrgID, o.Name, o.Access, o.Domains, o.UserMonthlyCredits, o.ContactName, o.ContactEmail, normEmail(c.Email),
			o.DocumentsMayLeave, o.RetentionYears, o.StorageWarnMB); err != nil {
			return nil, err
		}
		return orgChanges(r, before, o), nil
	})
	if err != nil {
		a.failChange(w, r, err, "member")
		return
	}
	resumed := 0
	if budgetChanged {
		resumed, _ = a.store.ResumeBudgetParked(ctx, acc.OrgID)
	}
	o, _ := a.store.OrgSettingsFor(ctx, acc.OrgID)
	writeJSON(w, http.StatusOK, map[string]any{"org": o, "resumed": resumed})
}

// adminCount is how many admins the organisation would have (break-glass ones included).
func (a *API) adminCount(ctx context.Context, org string, except string) (int, error) {
	rows, err := a.store.Pool.Query(ctx, `SELECT email FROM agent_market.org_members WHERE org_id = $1 AND role = 'admin'`, org)
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	set := map[string]bool{}
	for rows.Next() {
		var e string
		if err := rows.Scan(&e); err != nil {
			return 0, err
		}
		set[e] = true
	}
	for _, e := range a.cfg.AdminEmails {
		set[e] = true
	}
	delete(set, except)
	return len(set), rows.Err()
}

// memberView is a person on the list as the log records it.
func memberView(m Member) map[string]any {
	return map[string]any{"role": m.Role, "department": m.Department, "display_name": m.DisplayName, "monthly_credits": m.MonthlyCredits}
}

// memberForUpdate reads one person on the list inside a change.
func memberForUpdate(ctx context.Context, tx pgx.Tx, org, email string) (Member, error) {
	m, err := scanMember(tx.QueryRow(ctx, `SELECT `+memberCols+` FROM agent_market.org_members WHERE org_id = $1 AND email = $2 FOR UPDATE`, org, email))
	if errors.Is(err, pgx.ErrNoRows) {
		return m, ErrNotFound
	}
	return m, err
}

func (a *API) adminPutMember(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	c, _ := caller(r)
	acc := accessOf(r)
	email := normEmail(chi.URLParam(r, "email"))
	if !validEmail(email) {
		say(w, http.StatusBadRequest, "BAD_EMAIL")
		return
	}
	var body struct {
		DisplayName    *string         `json:"display_name"`
		Department     *string         `json:"department"`
		Role           *string         `json:"role"`
		MonthlyCredits json.RawMessage `json:"monthly_credits"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	if body.Role != nil {
		switch *body.Role {
		case RoleMember, RolePublisher, RoleAdmin:
		default:
			say(w, http.StatusBadRequest, "BAD_ROLE")
			return
		}
	}
	creditsSet, credits, ok := optCredits(body.MonthlyCredits)
	if !ok {
		say(w, http.StatusBadRequest, "BAD_BUDGET")
		return
	}
	ctx := r.Context()
	isNew, budgetChanged := false, false
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		m, err := memberForUpdate(ctx, tx, acc.OrgID, email)
		isNew = errors.Is(err, ErrNotFound)
		if err != nil && !isNew {
			return nil, err
		}
		if isNew {
			m = Member{Email: email, Role: RoleMember}
		}
		before := memberView(m)
		wasAdmin := m.Role == RoleAdmin
		if body.DisplayName != nil {
			m.DisplayName = strings.Join(strings.Fields(*body.DisplayName), " ")
		}
		if body.Department != nil {
			m.Department = strings.Join(strings.Fields(*body.Department), " ")
		}
		if body.Role != nil {
			m.Role = *body.Role
		}
		budgetChanged = isNew || creditsSet || body.Department != nil
		if creditsSet {
			m.MonthlyCredits = credits
		}
		if wasAdmin && m.Role != RoleAdmin && !a.cfg.IsEnvAdmin(email) {
			if n, err := a.adminCount(ctx, acc.OrgID, email); err != nil {
				return nil, err
			} else if n == 0 {
				return nil, refusal{status: http.StatusConflict, code: "LAST_ADMIN"}
			}
		}
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.org_members (org_id, email, display_name, department, role,
			monthly_credits, added_by) VALUES ($1, $2, $3, $4, $5, $6, $7)
			ON CONFLICT (org_id, email) DO UPDATE SET display_name = EXCLUDED.display_name, department = EXCLUDED.department,
			role = EXCLUDED.role, monthly_credits = EXCLUDED.monthly_credits, updated_at = now()`,
			acc.OrgID, email, m.DisplayName, m.Department, m.Role, m.MonthlyCredits, normEmail(c.Email)); err != nil {
			return nil, err
		}
		after := memberView(m)
		if isNew {
			return []AuditEntry{entry(r, "agent_market.member.added", "agent_market_member", acc.OrgID,
				map[string]any{"email": email, "to": after})}, nil
		}
		if jsonText(before) == jsonText(after) {
			return nil, nil
		}
		return []AuditEntry{entry(r, "agent_market.member.changed", "agent_market_member", acc.OrgID,
			map[string]any{"email": email, "from": before, "to": after})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "member")
		return
	}
	resumed := 0
	if budgetChanged {
		resumed, _ = a.store.ResumeBudgetParked(ctx, acc.OrgID)
	}
	m, _ := a.store.MemberByEmail(ctx, acc.OrgID, email)
	m.EnvAdmin = a.cfg.IsEnvAdmin(email)
	writeJSON(w, http.StatusOK, map[string]any{"member": m, "created": isNew, "resumed": resumed})
}

func (a *API) adminDeleteMember(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	acc := accessOf(r)
	email := normEmail(chi.URLParam(r, "email"))
	ctx := r.Context()
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		m, err := memberForUpdate(ctx, tx, acc.OrgID, email)
		if err != nil {
			return nil, err
		}
		if m.Role == RoleAdmin {
			if n, err := a.adminCount(ctx, acc.OrgID, email); err != nil {
				return nil, err
			} else if n == 0 {
				return nil, refusal{status: http.StatusConflict, code: "LAST_ADMIN"}
			}
		}
		if _, err := tx.Exec(ctx, `DELETE FROM agent_market.org_members WHERE org_id = $1 AND email = $2`, acc.OrgID, email); err != nil {
			return nil, err
		}
		return []AuditEntry{entry(r, "agent_market.member.removed", "agent_market_member", acc.OrgID,
			map[string]any{"email": email, "from": memberView(m)})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "member")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"removed": true})
}

// departmentView is a department as the log records it (nil = there was none).
func departmentView(d *Department) any {
	if d == nil {
		return nil
	}
	return map[string]any{"monthly_credits": d.MonthlyCredits, "contact_name": d.ContactName, "contact_email": d.ContactEmail}
}

// departmentForUpdate reads one department inside a change (nil = none).
func departmentForUpdate(ctx context.Context, tx pgx.Tx, org, name string) (*Department, error) {
	d := Department{Name: name}
	err := tx.QueryRow(ctx, `SELECT monthly_credits::float8, contact_name, contact_email FROM agent_market.org_departments
		WHERE org_id = $1 AND name = $2 FOR UPDATE`, org, name).Scan(&d.MonthlyCredits, &d.ContactName, &d.ContactEmail)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &d, nil
}

func (a *API) adminPutDepartment(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	acc := accessOf(r)
	name := strings.Join(strings.Fields(chi.URLParam(r, "name")), " ")
	if name == "" || len([]rune(name)) > 80 {
		badRequest(w, "BAD_NAME")
		return
	}
	var body struct {
		MonthlyCredits json.RawMessage `json:"monthly_credits"`
		ContactName    *string         `json:"contact_name"`
		ContactEmail   *string         `json:"contact_email"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	creditsSet, credits, ok := optCredits(body.MonthlyCredits)
	if !ok {
		say(w, http.StatusBadRequest, "BAD_BUDGET")
		return
	}
	contactEmail := ""
	if body.ContactEmail != nil {
		contactEmail = normEmail(*body.ContactEmail)
		if contactEmail != "" && !validEmail(contactEmail) {
			say(w, http.StatusBadRequest, "BAD_EMAIL")
			return
		}
	}
	ctx := r.Context()
	d := Department{Name: name}
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		old, err := departmentForUpdate(ctx, tx, acc.OrgID, name)
		if err != nil {
			return nil, err
		}
		if old != nil {
			d = *old
		}
		if creditsSet {
			d.MonthlyCredits = credits
		}
		if body.ContactName != nil {
			d.ContactName = strings.TrimSpace(*body.ContactName)
		}
		if body.ContactEmail != nil {
			d.ContactEmail = contactEmail
		}
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.org_departments (org_id, name, monthly_credits, contact_name, contact_email)
			VALUES ($1, $2, $3, $4, $5) ON CONFLICT (org_id, name) DO UPDATE SET monthly_credits = EXCLUDED.monthly_credits,
			contact_name = EXCLUDED.contact_name, contact_email = EXCLUDED.contact_email, updated_at = now()`,
			acc.OrgID, name, d.MonthlyCredits, d.ContactName, d.ContactEmail); err != nil {
			return nil, err
		}
		if old != nil && jsonText(departmentView(old)) == jsonText(departmentView(&d)) {
			return nil, nil
		}
		return []AuditEntry{entry(r, "agent_market.department.set", "agent_market_department", acc.OrgID,
			map[string]any{"department": name, "from": departmentView(old), "to": departmentView(&d)})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "department")
		return
	}
	resumed, _ := a.store.ResumeBudgetParked(ctx, acc.OrgID)
	writeJSON(w, http.StatusOK, map[string]any{"department": d, "resumed": resumed})
}

func (a *API) adminDeleteDepartment(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	acc := accessOf(r)
	name := strings.Join(strings.Fields(chi.URLParam(r, "name")), " ")
	ctx := r.Context()
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		old, err := departmentForUpdate(ctx, tx, acc.OrgID, name)
		if err != nil || old == nil {
			return nil, err
		}
		if _, err := tx.Exec(ctx, `DELETE FROM agent_market.org_departments WHERE org_id = $1 AND name = $2`, acc.OrgID, name); err != nil {
			return nil, err
		}
		return []AuditEntry{entry(r, "agent_market.department.removed", "agent_market_department", acc.OrgID,
			map[string]any{"department": name, "from": departmentView(old)})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "department")
		return
	}
	resumed, _ := a.store.ResumeBudgetParked(ctx, acc.OrgID)
	writeJSON(w, http.StatusOK, map[string]any{"removed": true, "resumed": resumed})
}

// ── spend ────────────────────────────────────────────────────────────────

// SpendRow is one person or one agent over the window.
type SpendRow struct {
	Key        string `json:"key"` // user id or slug
	Email      string `json:"email,omitempty"`
	Name       string `json:"name,omitempty"`
	Department string `json:"department,omitempty"`
	Runs       int64  `json:"runs"`
	Succeeded  int64  `json:"succeeded"`
	// AM6: CompletedOpen are the rounds that delivered their draft while the
	// agent's gate did not pass; Failed the ones that delivered nothing.
	CompletedOpen int64   `json:"completed_open"`
	Failed        int64   `json:"failed"`
	Waiting       int64   `json:"waiting"`
	Users         int64   `json:"users,omitempty"`
	Credits       float64 `json:"credits"` // what was charged
	// CostNotCharged (AM6): what the platform paid for rounds that delivered nothing.
	CostNotCharged float64    `json:"cost_not_charged"`
	MonthSpent     float64    `json:"month_spent"`
	Limit          *float64   `json:"monthly_limit,omitempty"`
	LastRunAt      *time.Time `json:"last_run_at"`
}

func (a *API) adminSpend(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	acc := accessOf(r)
	ctx := r.Context()
	days, _ := strconv.Atoi(r.URL.Query().Get("days"))
	if days <= 0 || days > 366 {
		days = 30
	}
	since := time.Now().Add(-time.Duration(days) * 24 * time.Hour)
	org := acc.OrgID
	users := map[string]*SpendRow{}
	agents := map[string]*SpendRow{}
	get := func(m map[string]*SpendRow, k string) *SpendRow {
		if m[k] == nil {
			m[k] = &SpendRow{Key: k}
		}
		return m[k]
	}
	// runs (the person's work: kind run; the catalog's samples are counted apart)
	rows, err := a.store.Pool.Query(ctx, `
		SELECT i.user_id::text, i.slug, count(*),
			count(*) FILTER (WHERE r.status = 'succeeded'), count(*) FILTER (WHERE r.status = 'failed' AND NOT r.gate_open),
			count(*) FILTER (WHERE r.status = 'failed' AND r.gate_open),
			count(*) FILTER (WHERE r.status = 'awaiting_input'), max(r.created_at)
		FROM agent_market.agent_runs r JOIN agent_market.agent_installs i ON i.id = r.install_id
		WHERE i.org_id = $1 AND i.status <> 'catalog' AND r.kind = 'run' AND r.created_at >= $2
		GROUP BY 1, 2`, org, since)
	if err != nil {
		a.fail(w, err, "spend")
		return
	}
	userSlugs := map[string]map[string]bool{}
	for rows.Next() {
		var uid, slug string
		var n, ok, fail, open, wait int64
		var last *time.Time
		if err := rows.Scan(&uid, &slug, &n, &ok, &fail, &open, &wait, &last); err != nil {
			rows.Close()
			a.fail(w, err, "spend")
			return
		}
		for _, x := range []*SpendRow{get(users, uid), get(agents, slug)} {
			x.Runs += n
			x.Succeeded += ok
			x.Failed += fail
			x.CompletedOpen += open
			x.Waiting += wait
			if last != nil && (x.LastRunAt == nil || last.After(*x.LastRunAt)) {
				l := *last
				x.LastRunAt = &l
			}
		}
		if userSlugs[slug] == nil {
			userSlugs[slug] = map[string]bool{}
		}
		userSlugs[slug][uid] = true
	}
	rows.Close()
	// credits: every job (a self-test, a re-render and a sign-off cost too),
	// over the window and over this (Bangkok) month. AM6: what was charged is
	// the rows that are not voided; the voided ones are what the platform paid
	// for rounds that delivered nothing, shown apart.
	rows, err = a.store.Pool.Query(ctx, `
		SELECT i.user_id::text, i.slug,
			COALESCE(sum(l.credits) FILTER (WHERE l.voided_at IS NULL AND l.created_at >= $2), 0)::float8,
			COALESCE(sum(l.credits) FILTER (WHERE l.voided_at IS NULL AND l.created_at >= `+monthStart+`), 0)::float8,
			COALESCE(sum(l.credits) FILTER (WHERE l.voided_at IS NOT NULL AND l.created_at >= $2), 0)::float8
		FROM agent_market.agent_budget_ledger l JOIN agent_market.agent_installs i ON i.id = l.install_id
		WHERE i.org_id = $1 AND i.status <> 'catalog' AND l.created_at >= LEAST($2, `+monthStart+`)
		GROUP BY 1, 2`, org, since)
	if err != nil {
		a.fail(w, err, "spend")
		return
	}
	for rows.Next() {
		var uid, slug string
		var credits, month, free float64
		if err := rows.Scan(&uid, &slug, &credits, &month, &free); err != nil {
			rows.Close()
			a.fail(w, err, "spend")
			return
		}
		for _, x := range []*SpendRow{get(users, uid), get(agents, slug)} {
			x.Credits += credits
			x.MonthSpent += month
			x.CostNotCharged += free
		}
	}
	rows.Close()
	var samples float64
	_ = a.store.Pool.QueryRow(ctx, `SELECT COALESCE(sum(l.credits), 0)::float8 FROM agent_market.agent_budget_ledger l
		JOIN agent_market.agent_installs i ON i.id = l.install_id WHERE i.status = 'catalog' AND l.created_at >= $1`, since).Scan(&samples)
	// who the users are, and their limits
	o, _ := a.store.OrgSettingsFor(ctx, org)
	byUser, _ := a.store.memberLabels(ctx, org)
	for uid, x := range users {
		id, _ := uuid.Parse(uid)
		if m, ok := byUser[id]; ok {
			x.Email, x.Name, x.Department = m.Email, m.DisplayName, m.Department
			lim := o.UserMonthlyCredits
			if m.MonthlyCredits != nil {
				lim = *m.MonthlyCredits
			}
			x.Limit = &lim
		} else {
			var e, n string
			if a.store.Pool.QueryRow(ctx, `SELECT email, name FROM identity.users WHERE id = $1`, id).Scan(&e, &n) == nil {
				x.Email, x.Name = strings.ToLower(e), n
			}
			lim := o.UserMonthlyCredits
			x.Limit = &lim
		}
	}
	defs, _ := a.store.ListDefinitions(ctx)
	names := map[string]string{}
	for _, d := range defs {
		names[d.Slug] = d.Name
	}
	for slug, x := range agents {
		x.Name = names[slug]
		x.Users = int64(len(userSlugs[slug]))
	}
	flat := func(m map[string]*SpendRow) []SpendRow {
		out := make([]SpendRow, 0, len(m))
		for _, x := range m {
			out = append(out, *x)
		}
		sort.Slice(out, func(i, j int) bool {
			if out[i].Credits != out[j].Credits {
				return out[i].Credits > out[j].Credits
			}
			return out[i].Key < out[j].Key
		})
		return out
	}
	var total, free float64
	for _, x := range users {
		total += x.Credits
		free += x.CostNotCharged
	}
	writeJSON(w, http.StatusOK, map[string]any{"days": days, "since": since, "by_user": flat(users), "by_agent": flat(agents),
		"total_credits": total, "total_cost_not_charged": free, "catalog_samples_credits": samples,
		"pricing": map[string]float64{"credits_per_usd": a.cfg.CreditsPerUSD, "thb_per_usd": a.cfg.THBPerUSD}})
}

// memberLabels maps user id → member row (for the members seen at least once).
func (s *Store) memberLabels(ctx context.Context, org string) (map[uuid.UUID]Member, error) {
	list, err := s.Members(ctx, org)
	if err != nil {
		return nil, err
	}
	out := map[uuid.UUID]Member{}
	for _, m := range list {
		if m.UserID != nil {
			out[*m.UserID] = m
		}
	}
	return out, nil
}

// ── problem reports ──────────────────────────────────────────────────────

// The problems queue is the catalog's (AM7): the people who publish agents
// read and close what people reported about them.
func (a *API) adminProblems(w http.ResponseWriter, r *http.Request) {
	if !a.requirePublisher(w, r) {
		return
	}
	acc := accessOf(r)
	status := r.URL.Query().Get("status")
	q := `SELECT ` + problemCols + ` FROM agent_market.problem_reports WHERE org_id = $1`
	switch status {
	case "open", "closed":
		q += ` AND status = '` + status + `'`
	}
	q += ` ORDER BY (status = 'open') DESC, created_at DESC LIMIT 200`
	rows, err := a.store.Pool.Query(r.Context(), q, acc.OrgID)
	if err != nil {
		a.fail(w, err, "problem")
		return
	}
	defer rows.Close()
	out := []ProblemReport{}
	for rows.Next() {
		p, err := scanProblem(rows)
		if err != nil {
			a.fail(w, err, "problem")
			return
		}
		out = append(out, p)
	}
	writeJSON(w, http.StatusOK, map[string]any{"problems": out})
}

func (a *API) adminCloseProblem(w http.ResponseWriter, r *http.Request) {
	if !a.requirePublisher(w, r) {
		return
	}
	c, _ := caller(r)
	acc := accessOf(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var body struct {
		Note string `json:"note"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body)
	ctx := r.Context()
	var p ProblemReport
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		var err error
		p, err = scanProblem(tx.QueryRow(ctx, `UPDATE agent_market.problem_reports SET status = 'closed',
			admin_note = $3, closed_by = $4, closed_at = now() WHERE id = $1 AND org_id = $2 RETURNING `+problemCols,
			id, acc.OrgID, strings.TrimSpace(body.Note), normEmail(c.Email)))
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		if err != nil {
			return nil, err
		}
		return []AuditEntry{entry(r, "agent_market.problem.closed", "agent_market_problem", p.ID.String(),
			map[string]any{"ref": p.Ref, "note": p.AdminNote, "reported_by": p.Email, "agent": p.Slug})}, nil
	})
	if err != nil {
		a.failChange(w, r, err, "problem")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"problem": p})
}

// ── the catalog's owner ──────────────────────────────────────────────────

func (a *API) adminCatalog(w http.ResponseWriter, r *http.Request) {
	if !a.requirePublisher(w, r) {
		return
	}
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
	type row struct {
		catalogEntry
		ChangedBy string     `json:"changed_by"`
		ChangedAt *time.Time `json:"changed_at"`
		Installs  int64      `json:"installs"`
	}
	out := []row{}
	for _, d := range defs {
		e := a.entryFor(ctx, acc, d, states, nil)
		x := row{catalogEntry: e}
		if st, ok := states[d.Slug]; ok {
			x.ChangedBy = st.ChangedBy
			at := st.ChangedAt
			x.ChangedAt = &at
		}
		_ = a.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_installs WHERE org_id = $1 AND slug = $2
			AND status = 'active'`, acc.OrgID, d.Slug).Scan(&x.Installs)
		out = append(out, x)
	}
	writeJSON(w, http.StatusOK, map[string]any{"agents": out, "admin": acc.Admin()})
}

func (a *API) adminPutCatalog(w http.ResponseWriter, r *http.Request) {
	if !a.requirePublisher(w, r) {
		return
	}
	c, _ := caller(r)
	acc := accessOf(r)
	slug := chi.URLParam(r, "slug")
	var body struct {
		Status string `json:"status"`
		Reason string `json:"reason"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	if _, err := a.store.DefinitionBySlug(ctx, slug); err != nil {
		a.fail(w, err, "agent")
		return
	}
	cur, err := a.store.CatalogStateFor(ctx, acc.OrgID, slug)
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	switch body.Status {
	case StatusPublished, StatusUnpublished, StatusDisabled:
	default:
		say(w, http.StatusBadRequest, "BAD_STATUS")
		return
	}
	// disabling for everyone, or lifting that, is an admin's decision
	if (body.Status == StatusDisabled || cur.Status == StatusDisabled) && !acc.Admin() {
		a.sayTo(w, r, http.StatusForbidden, "NOT_ADMIN")
		return
	}
	reason := trimReason(body.Reason)
	if body.Status == StatusDisabled && reason == "" {
		say(w, http.StatusBadRequest, "REASON_REQUIRED")
		return
	}
	// AM7: the change commits only after the audit log took it
	stopped, err := a.store.SetCatalogState(ctx, acc.OrgID, slug, body.Status, reason, normEmail(c.Email), func(stopped int) error {
		return a.record(ctx, []AuditEntry{entry(r, "agent_market.catalog."+body.Status, "agent_market_agent", slug,
			map[string]any{"agent": slug, "from": cur.Status, "to": body.Status, "reason": reason, "stopped_runs": stopped})})
	})
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	st, _ := a.store.CatalogStateFor(ctx, acc.OrgID, slug)
	writeJSON(w, http.StatusOK, map[string]any{"state": st, "stopped_runs": stopped})
}

// adminSample makes the catalog's sample of an agent's newest version again.
func (a *API) adminSample(w http.ResponseWriter, r *http.Request) {
	if !a.requirePublisher(w, r) {
		return
	}
	ctx := r.Context()
	d, err := a.store.DefinitionBySlug(ctx, chi.URLParam(r, "slug"))
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	m, err := ParseManifest(d.Manifest)
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	st, err := a.store.LatestRunner(ctx, a.cfg.RunnerStaleAfter)
	if err != nil || !st.Healthy {
		a.sayTo(w, r, http.StatusServiceUnavailable, "RUNNER_DOWN")
		return
	}
	var caps map[string]Binding
	_ = json.Unmarshal(st.Capabilities, &caps)
	res, err := Resolve(m, caps, 1)
	if err != nil {
		a.log.Warn("catalog sample refused", "slug", m.ID, "err", err)
		a.sayTo(w, r, http.StatusConflict, "CAPABILITY_MISSING", "detail", missingWords(langOfWriter(w), err))
		return
	}
	b, _ := json.Marshal(res)
	run, err := a.store.CreateCatalogSample(ctx, d, b, res.Version)
	if err != nil {
		a.fail(w, err, "sample")
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"run": run})
}
