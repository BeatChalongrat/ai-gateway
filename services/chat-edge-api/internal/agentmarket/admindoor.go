package agentmarket

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
)

// ── The admin app's door (AM7) ───────────────────────────────────────────
//
// The chat app and the desktop app are for the people who use agents.
// Everything that affects anyone else — who may use Agent Market, budgets,
// spend, whether documents may leave the organisation, retention, the
// catalog, the problems queue and other people's runs — is kept in the admin
// app (admin-ui).
//
// admin-ui reaches these handlers through its server-side proxy
// (/api/chat-edge/*): the proxy checks the admin's session with the gateway,
// keeps chat-edge's admin bearer off the browser, and names the signed-in
// admin in the four headers below. chat-edge mounts AdminRoutes under
// /api/v1/admin/agent-market behind that bearer (router.go), so only the
// proxy can name an admin; this file decides, on every request, what the
// named admin may do:
//
//	admin, super_admin → everything (Access role admin)
//	agent_publisher    → the catalog and the problems queue (Access role publisher)
//	anything else      → nothing
//
// The handlers are the ones the chat app's page used: one org_settings row,
// read and written by the same code whichever app asks.

// The headers admin-ui's proxy sets from the gateway's /auth/session/me.
const (
	HeaderAdminUserID = "X-Admin-User-Id"
	HeaderAdminEmail  = "X-Admin-Email"
	HeaderAdminName   = "X-Admin-Name" // path-escaped UTF-8
	HeaderAdminRole   = "X-Admin-Role"
)

// Admin-ui roles that open this door.
const (
	UIRoleAdmin      = "admin"
	UIRoleSuperAdmin = "super_admin"
	UIRolePublisher  = "agent_publisher"
)

// adminUIRole is what an admin-ui role may do here, as an Access role ("" = nothing).
func adminUIRole(role string) string {
	switch strings.TrimSpace(role) {
	case UIRoleAdmin, UIRoleSuperAdmin:
		return RoleAdmin
	case UIRolePublisher:
		return RolePublisher
	}
	return ""
}

// AdminActor is the admin-ui account a request came from.
type AdminActor struct {
	ID    string `json:"id"`
	Email string `json:"email"`
	Name  string `json:"name"`
	Role  string `json:"role"` // the admin-ui role
}

type adminActorKey struct{}

func adminActorOf(ctx context.Context) (AdminActor, bool) {
	a, ok := ctx.Value(adminActorKey{}).(AdminActor)
	return a, ok
}

// adminActorNS names the uuid an admin-ui account acts under here (an
// opening of someone's run is recorded against it; it never collides with a
// chat user's id).
var adminActorNS = uuid.MustParse("6f2c3a51-7a4e-4f0b-9d2e-a4d7a3b1c0de")

// AdminActorUserID is the uuid an admin-ui account acts under.
func AdminActorUserID(id string) uuid.UUID {
	return uuid.NewSHA1(adminActorNS, []byte("admin-ui:"+id))
}

// adminIdentity reads the admin the proxy named and what they may do.
func (a *API) adminIdentity(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		actor := AdminActor{
			ID:    strings.TrimSpace(r.Header.Get(HeaderAdminUserID)),
			Email: normEmail(r.Header.Get(HeaderAdminEmail)),
			Role:  strings.TrimSpace(r.Header.Get(HeaderAdminRole)),
		}
		if n, err := url.PathUnescape(r.Header.Get(HeaderAdminName)); err == nil {
			actor.Name = strings.Join(strings.Fields(n), " ")
		}
		role := adminUIRole(actor.Role)
		if actor.ID == "" || actor.Email == "" || role == "" {
			say(w, http.StatusForbidden, "NOT_ADMIN")
			return
		}
		ctx := r.Context()
		acc, err := a.store.adminAccess(ctx, a.cfg, actor.Email, role)
		if err != nil {
			a.fail(w, err, "access")
			return
		}
		c := middleware.Caller{UserID: AdminActorUserID(actor.ID), Email: actor.Email, Role: actor.Role}
		ctx = middleware.WithCaller(withAccess(ctx, acc), c)
		ctx = context.WithValue(ctx, adminActorKey{}, actor)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// adminAccess is an admin-ui account's access: the organisation its email
// domain belongs to (else the default one) and the role its admin-ui role
// gives it here.
func (s *Store) adminAccess(ctx context.Context, cfg Config, email, role string) (Access, error) {
	a := Access{Email: email, Role: role, Member: true, Allowed: true}
	q := `SELECT org_id, access, contact_name, contact_email FROM agent_market.org_settings
		WHERE org_id = COALESCE((SELECT org_id FROM agent_market.org_settings
			WHERE org_id <> 'default' AND $1 = ANY(domains) ORDER BY org_id LIMIT 1), 'default')`
	err := s.Pool.QueryRow(ctx, q, emailDomain(email)).Scan(&a.OrgID, &a.Mode, &a.Contact.Name, &a.Contact.Email)
	if errors.Is(err, pgx.ErrNoRows) {
		if _, err := s.EnsureOrg(ctx, cfg); err != nil {
			return a, err
		}
		err = s.Pool.QueryRow(ctx, q, emailDomain(email)).Scan(&a.OrgID, &a.Mode, &a.Contact.Name, &a.Contact.Email)
	}
	if err != nil {
		return a, err
	}
	if a.Contact.Email == "" && a.Contact.Name == "" {
		a.Contact = s.firstAdmin(ctx, a.OrgID)
	}
	return a, nil
}

// Sections of the admin app's Agent Market page, in its order.
var (
	adminSections     = []string{"access", "departments", "spend", "models", "catalog", "problems", "runs", "mail"}
	publisherSections = []string{"catalog", "problems"}
)

// adminMe says who is signed in to the admin app's page and what they may do there.
func (a *API) adminMe(w http.ResponseWriter, r *http.Request) {
	actor, _ := adminActorOf(r.Context())
	acc := accessOf(r)
	sections := publisherSections
	if acc.Admin() {
		sections = adminSections
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"email": actor.Email, "name": actor.Name, "admin_role": actor.Role,
		"role": acc.Role, "org_id": acc.OrgID, "sections": sections,
		// disabling an agent for everyone (or lifting that) stays an admin's decision
		"can_disable": acc.Admin(),
	})
}

// AdminRoutes mounts the admin app's door (router.go puts it behind chat-edge's
// admin bearer, under /api/v1/admin/agent-market). Each handler checks the
// role it needs: requireAdmin, or requirePublisher for the catalog and the
// problems queue.
func (a *API) AdminRoutes(r chi.Router) {
	r.Use(a.adminIdentity)
	r.Get("/me", a.adminMe)
	r.Get("/org", a.adminOrg)
	r.Put("/org", a.adminPutOrg)
	r.Put("/members/{email}", a.adminPutMember)
	r.Delete("/members/{email}", a.adminDeleteMember)
	r.Put("/departments/{name}", a.adminPutDepartment)
	r.Delete("/departments/{name}", a.adminDeleteDepartment)
	r.Get("/spend", a.adminSpend)
	r.Get("/problems", a.adminProblems)
	r.Post("/problems/{id}/close", a.adminCloseProblem)
	r.Get("/catalog", a.adminCatalog)
	r.Put("/catalog/{slug}", a.adminPutCatalog)
	r.Post("/catalog/{slug}/sample", a.adminSample)
	r.Get("/runs", a.adminRuns)
	r.Post("/runs/{id}/open", a.adminOpenRun)
	r.Get("/runs/{id}", a.adminRunContent)
	r.Get("/runs/{id}/computed", a.adminRunComputed)
	r.Get("/runs/{id}/artifacts/{aid}/download", a.adminRunArtifact)
	r.Get("/runs/{id}/files/{fid}/download", a.adminRunFile)
	r.Post("/runs/{id}/remove-evidence", a.adminRemoveEvidence)
	// phase 5: the organisation's mail relay (mail.go)
	r.Get("/mail", a.adminMail)
	r.Put("/mail", a.adminPutMail)
	r.Post("/mail/test", a.adminTestMail)
}
