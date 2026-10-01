package agentmarket

import (
	"context"
	"errors"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── The organisation (AM3) ───────────────────────────────────────────────
//
// Who may use Agent Market is the organisation's decision, kept in the
// database by its admins: an org-wide switch (access: off | list |
// everyone) plus the list of people (org_members) with their department,
// role and optional personal budget. The single-email allowlist of the
// pilot (AGENT_MARKET_ALLOWED_EMAILS) only seeds that list the day the
// organisation row is created.
//
// One organisation today ("default"); a second one is a row whose email
// domains its people sign in with — every AM3 table carries org_id.

const DefaultOrg = "default"

// Access modes and roles.
const (
	AccessOff      = "off"
	AccessList     = "list"
	AccessEveryone = "everyone"

	RoleMember    = "member"
	RolePublisher = "publisher"
	RoleAdmin     = "admin"
)

// OrgSettings is the organisation's own row.
type OrgSettings struct {
	OrgID              string    `json:"org_id"`
	Name               string    `json:"name"`
	Access             string    `json:"access"`
	Domains            []string  `json:"domains"`
	UserMonthlyCredits float64   `json:"user_monthly_credits"`
	ContactName        string    `json:"contact_name"`
	ContactEmail       string    `json:"contact_email"`
	UpdatedBy          string    `json:"updated_by"`
	UpdatedAt          time.Time `json:"updated_at"`
	// AM4: may documents go to a model outside the organisation (off by
	// default); how many years accounting records are kept; when a person's
	// storage use gets a warning. (AM6: the organisation keeps no list of
	// models any more — the column agent_models stays where it is, unread.)
	DocumentsMayLeave bool `json:"documents_may_leave"`
	RetentionYears    int  `json:"retention_years"`
	StorageWarnMB     int  `json:"storage_warn_mb"`
}

// Member is one person on the access list.
type Member struct {
	Email          string     `json:"email"`
	UserID         *uuid.UUID `json:"user_id,omitempty"`
	DisplayName    string     `json:"display_name"`
	Department     string     `json:"department"`
	Role           string     `json:"role"`
	MonthlyCredits *float64   `json:"monthly_credits"`
	AddedBy        string     `json:"added_by"`
	CreatedAt      time.Time  `json:"created_at"`
	UpdatedAt      time.Time  `json:"updated_at"`
	// EnvAdmin: named in AGENT_MARKET_ADMIN_EMAILS (always an admin).
	EnvAdmin bool `json:"env_admin,omitempty"`
}

// Department is a budget line and the person to ask when it is used up.
type Department struct {
	Name           string   `json:"name"`
	MonthlyCredits *float64 `json:"monthly_credits"`
	ContactName    string   `json:"contact_name"`
	ContactEmail   string   `json:"contact_email"`
	Members        int      `json:"members"`
}

// Contact is who a person should ask (a budget, an agent that was switched off).
type Contact struct {
	Name  string `json:"name"`
	Email string `json:"email"`
}

// Label is how a message names the contact.
func (c Contact) Label() string {
	switch {
	case c.Name != "" && c.Email != "":
		return c.Name + " (" + c.Email + ")"
	case c.Name != "":
		return c.Name
	case c.Email != "":
		return c.Email
	}
	return "ผู้ดูแล Agent Market ขององค์กร"
}

// Access is what one signed-in person may do.
type Access struct {
	OrgID   string  `json:"org_id"`
	Mode    string  `json:"access"`
	Member  bool    `json:"member"`
	Role    string  `json:"role"`
	Allowed bool    `json:"available"`
	Contact Contact `json:"contact"`
	Email   string  `json:"-"`
}

// Admin may change access, budgets and the catalog, and see spend.
func (a Access) Admin() bool { return a.Role == RoleAdmin }

// Publisher may publish an agent to the organisation's catalog.
func (a Access) Publisher() bool { return a.Role == RolePublisher || a.Role == RoleAdmin }

func normEmail(e string) string { return strings.ToLower(strings.TrimSpace(e)) }

func emailDomain(e string) string {
	if i := strings.LastIndexByte(e, '@'); i >= 0 {
		return strings.ToLower(e[i+1:])
	}
	return ""
}

// validEmail is a plain shape check (the identity provider owns the rest).
func validEmail(e string) bool {
	i := strings.IndexByte(e, '@')
	return i > 0 && i == strings.LastIndexByte(e, '@') && i < len(e)-3 && strings.Contains(e[i:], ".") &&
		!strings.ContainsAny(e, " ,;<>\"'")
}

// EnsureOrg creates the default organisation the first time the control
// plane starts with AM3: access "list" seeded from AGENT_MARKET_ALLOWED_EMAILS
// (or "everyone" when that list is empty — AM1's meaning of an empty list),
// its admins from AGENT_MARKET_ADMIN_EMAILS (or, when unset, the allowed
// emails), its domains from AGENT_MARKET_ORG_DOMAINS (or those emails'
// domains). It never touches an organisation that already exists: from then
// on the admins' settings win.
func (s *Store) EnsureOrg(ctx context.Context, cfg Config) (bool, error) {
	access := AccessList
	if len(cfg.AllowedEmails) == 0 {
		access = AccessEveryone
	}
	admins := cfg.AdminEmails
	if len(admins) == 0 {
		admins = cfg.AllowedEmails
	}
	domains := cfg.OrgDomains
	if len(domains) == 0 {
		seen := map[string]bool{}
		for _, e := range append(append([]string{}, cfg.AllowedEmails...), cfg.AdminEmails...) {
			if d := emailDomain(e); d != "" && !seen[d] {
				seen[d] = true
				domains = append(domains, d)
			}
		}
		sort.Strings(domains)
	}
	if domains == nil {
		domains = []string{}
	}
	contact := ""
	if len(admins) > 0 {
		contact = admins[0]
	}
	budget := cfg.UserBudgetCredits
	if budget <= 0 {
		budget = 3000000
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	tag, err := tx.Exec(ctx, `INSERT INTO agent_market.org_settings (org_id, access, domains, user_monthly_credits, contact_email, updated_by)
		VALUES ($1, $2, $3, $4, $5, 'bootstrap') ON CONFLICT DO NOTHING`, DefaultOrg, access, domains, budget, contact)
	if err != nil {
		return false, err
	}
	if tag.RowsAffected() == 0 {
		return false, nil
	}
	for _, e := range cfg.AllowedEmails {
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.org_members (org_id, email, role, added_by)
			VALUES ($1, $2, 'member', 'bootstrap') ON CONFLICT DO NOTHING`, DefaultOrg, e); err != nil {
			return false, err
		}
	}
	for _, e := range admins {
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.org_members (org_id, email, role, added_by)
			VALUES ($1, $2, 'admin', 'bootstrap') ON CONFLICT (org_id, email) DO UPDATE SET role = 'admin'`, DefaultOrg, e); err != nil {
			return false, err
		}
	}
	return true, tx.Commit(ctx)
}

// AccessFor resolves a signed-in person: their organisation (a
// non-default organisation whose domains hold their email domain, else the
// default one), whether the organisation lets them in, and their role.
func (s *Store) AccessFor(ctx context.Context, cfg Config, email string, user uuid.UUID) (Access, error) {
	return s.accessFor(ctx, cfg, email, user, false)
}

func (s *Store) accessFor(ctx context.Context, cfg Config, email string, user uuid.UUID, retried bool) (Access, error) {
	email = normEmail(email)
	a := Access{Email: email}
	var role *string
	var memberUser *uuid.UUID
	err := s.Pool.QueryRow(ctx, `
		SELECT s.org_id, s.access, s.contact_name, s.contact_email, m.role, m.user_id
		FROM agent_market.org_settings s
		LEFT JOIN agent_market.org_members m ON m.org_id = s.org_id AND m.email = $1
		WHERE s.org_id = COALESCE((SELECT org_id FROM agent_market.org_settings
			WHERE org_id <> 'default' AND $2 = ANY(domains) ORDER BY org_id LIMIT 1), 'default')`,
		email, emailDomain(email)).Scan(&a.OrgID, &a.Mode, &a.Contact.Name, &a.Contact.Email, &role, &memberUser)
	if errors.Is(err, pgx.ErrNoRows) && !retried {
		if _, err := s.EnsureOrg(ctx, cfg); err != nil {
			return a, err
		}
		return s.accessFor(ctx, cfg, email, user, true)
	}
	if err != nil {
		return a, err
	}
	if role != nil {
		a.Member = true
		a.Role = *role
		if memberUser == nil && user != uuid.Nil {
			// the first time this person is seen: their spend and department line up by user id
			_, _ = s.Pool.Exec(ctx, `UPDATE agent_market.org_members SET user_id = $3, updated_at = now()
				WHERE org_id = $1 AND email = $2 AND user_id IS NULL`, a.OrgID, email, user)
		}
	}
	if cfg.IsEnvAdmin(email) {
		a.Role = RoleAdmin
	}
	switch {
	case a.Role == RoleAdmin:
		a.Allowed = true
	case a.Mode == AccessEveryone:
		a.Allowed = true
	case a.Mode == AccessList:
		a.Allowed = a.Member
	}
	if a.Contact.Email == "" && a.Contact.Name == "" {
		a.Contact = s.firstAdmin(ctx, a.OrgID)
	}
	return a, nil
}

// firstAdmin is the fallback contact: the organisation's longest-standing admin.
func (s *Store) firstAdmin(ctx context.Context, org string) Contact {
	var c Contact
	_ = s.Pool.QueryRow(ctx, `SELECT display_name, email FROM agent_market.org_members
		WHERE org_id = $1 AND role = 'admin' ORDER BY created_at, email LIMIT 1`, org).Scan(&c.Name, &c.Email)
	return c
}

// OrgSettingsFor reads one organisation.
func (s *Store) OrgSettingsFor(ctx context.Context, org string) (OrgSettings, error) {
	return s.orgSettingsQ(ctx, s.Pool, org, false)
}

// orgSettingsQ reads one organisation on q; lock = FOR UPDATE (AM7: a change
// and its audit entry see the same row).
func (s *Store) orgSettingsQ(ctx context.Context, q Querier, org string, lock bool) (OrgSettings, error) {
	var o OrgSettings
	sql := `SELECT org_id, name, access, domains, user_monthly_credits::float8, contact_name, contact_email,
		updated_by, updated_at, documents_may_leave, retention_years, storage_warn_mb
		FROM agent_market.org_settings WHERE org_id = $1`
	if lock {
		sql += ` FOR UPDATE`
	}
	err := q.QueryRow(ctx, sql, org).
		Scan(&o.OrgID, &o.Name, &o.Access, &o.Domains, &o.UserMonthlyCredits, &o.ContactName, &o.ContactEmail, &o.UpdatedBy,
			&o.UpdatedAt, &o.DocumentsMayLeave, &o.RetentionYears, &o.StorageWarnMB)
	if errors.Is(err, pgx.ErrNoRows) {
		return o, ErrNotFound
	}
	return o, err
}

const memberCols = `email, user_id, display_name, department, role, monthly_credits::float8, added_by, created_at, updated_at`

func scanMember(r pgx.Row) (Member, error) {
	var m Member
	err := r.Scan(&m.Email, &m.UserID, &m.DisplayName, &m.Department, &m.Role, &m.MonthlyCredits, &m.AddedBy, &m.CreatedAt, &m.UpdatedAt)
	return m, err
}

// Members lists the organisation's access list.
func (s *Store) Members(ctx context.Context, org string) ([]Member, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+memberCols+` FROM agent_market.org_members WHERE org_id = $1 ORDER BY email`, org)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Member
	for rows.Next() {
		m, err := scanMember(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// MemberByUser is the member row of a user (their department and personal budget).
func (s *Store) MemberByUser(ctx context.Context, org string, user uuid.UUID) (Member, error) {
	m, err := scanMember(s.Pool.QueryRow(ctx, `SELECT `+memberCols+` FROM agent_market.org_members
		WHERE org_id = $1 AND user_id = $2`, org, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return m, ErrNotFound
	}
	return m, err
}

// MemberByEmail reads one member row.
func (s *Store) MemberByEmail(ctx context.Context, org, email string) (Member, error) {
	m, err := scanMember(s.Pool.QueryRow(ctx, `SELECT `+memberCols+` FROM agent_market.org_members
		WHERE org_id = $1 AND email = $2`, org, normEmail(email)))
	if errors.Is(err, pgx.ErrNoRows) {
		return m, ErrNotFound
	}
	return m, err
}

// Departments lists the organisation's departments with how many people each has.
func (s *Store) Departments(ctx context.Context, org string) ([]Department, error) {
	rows, err := s.Pool.Query(ctx, `
		SELECT n.name, d.monthly_credits::float8, COALESCE(d.contact_name, ''), COALESCE(d.contact_email, ''),
			(SELECT count(*) FROM agent_market.org_members m WHERE m.org_id = $1 AND m.department = n.name)::int
		FROM (SELECT name FROM agent_market.org_departments WHERE org_id = $1
			UNION SELECT DISTINCT department FROM agent_market.org_members WHERE org_id = $1 AND department <> '') n
		LEFT JOIN agent_market.org_departments d ON d.org_id = $1 AND d.name = n.name
		ORDER BY n.name`, org)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Department
	for rows.Next() {
		var d Department
		if err := rows.Scan(&d.Name, &d.MonthlyCredits, &d.ContactName, &d.ContactEmail, &d.Members); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// DisplayName is how a person is named on a document: their member name,
// else the name the identity service holds, else their email.
func (s *Store) DisplayName(ctx context.Context, org string, user uuid.UUID, email string) string {
	var n string
	if err := s.Pool.QueryRow(ctx, `SELECT display_name FROM agent_market.org_members WHERE org_id = $1 AND (user_id = $2 OR email = $3)
		ORDER BY (user_id = $2) DESC LIMIT 1`, org, user, normEmail(email)).Scan(&n); err == nil && strings.TrimSpace(n) != "" {
		return strings.TrimSpace(n)
	}
	// A read of the core's user row (no key crosses schemas; absent table = email).
	if err := s.Pool.QueryRow(ctx, `SELECT name FROM identity.users WHERE id = $1`, user).Scan(&n); err == nil && strings.TrimSpace(n) != "" {
		return strings.TrimSpace(n)
	}
	return email
}

// UserEmail is a person's e-mail from the core's user row ("" when there is
// none): what a document names when the person has no display name (AM8).
func (s *Store) UserEmail(ctx context.Context, user uuid.UUID) string {
	var e string
	if err := s.Pool.QueryRow(ctx, `SELECT email FROM identity.users WHERE id = $1`, user).Scan(&e); err != nil {
		return ""
	}
	return strings.TrimSpace(e)
}

// ── the request's access, carried in its context ────────────────────────

type accessKey struct{}

func withAccess(ctx context.Context, a Access) context.Context {
	return context.WithValue(ctx, accessKey{}, a)
}

// accessOf is the Access the allowed middleware resolved for this request.
func accessOf(r *http.Request) Access {
	a, _ := r.Context().Value(accessKey{}).(Access)
	return a
}
