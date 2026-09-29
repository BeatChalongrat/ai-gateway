package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── Sharing a run inside the organisation (AM3) ──────────────────────────
//
// The owner of a run names a colleague (their email, in the organisation);
// the colleague opens /chat/agents/shared/<id> signed in as that person and
// sees the run's files, its exceptions and decisions, and its sign-offs —
// read only, or allowed to sign it off. Nothing is public: chat's share link
// is readable by anyone holding it, but a run's files are accounting data,
// so a run share names its reader, needs their sign-in, and stays inside the
// organisation. Every share, open and revocation is recorded.

// Share is one grant.
type Share struct {
	ID             uuid.UUID  `json:"id"`
	RunID          uuid.UUID  `json:"run_id"`
	RecipientEmail string     `json:"recipient_email"`
	CanReview      bool       `json:"can_review"`
	CreatedAt      time.Time  `json:"created_at"`
	ExpiresAt      *time.Time `json:"expires_at"`
	RevokedAt      *time.Time `json:"revoked_at"`
	LastOpenedAt   *time.Time `json:"last_opened_at"`
	Opens          int        `json:"opens"`
	OwnerUserID    uuid.UUID  `json:"-"`
	OrgID          string     `json:"-"`
}

// Active: not revoked and not expired.
func (s Share) Active(now time.Time) bool {
	return s.RevokedAt == nil && (s.ExpiresAt == nil || s.ExpiresAt.After(now))
}

const shareCols = `id, run_id, recipient_email, can_review, created_at, expires_at, revoked_at, last_opened_at, opens, owner_user_id, org_id`

func scanShare(r pgx.Row) (Share, error) {
	var s Share
	err := r.Scan(&s.ID, &s.RunID, &s.RecipientEmail, &s.CanReview, &s.CreatedAt, &s.ExpiresAt, &s.RevokedAt, &s.LastOpenedAt,
		&s.Opens, &s.OwnerUserID, &s.OrgID)
	return s, err
}

// ShareByID reads one share.
func (s *Store) ShareByID(ctx context.Context, id uuid.UUID) (Share, error) {
	x, err := scanShare(s.Pool.QueryRow(ctx, `SELECT `+shareCols+` FROM agent_market.run_shares WHERE id = $1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	return x, err
}

// InOrg reports whether an email belongs to the organisation: on its access
// list, or at one of its email domains.
func (s *Store) InOrg(ctx context.Context, org, email string) (bool, []string, error) {
	var domains []string
	if err := s.Pool.QueryRow(ctx, `SELECT domains FROM agent_market.org_settings WHERE org_id = $1`, orgOr(org)).Scan(&domains); err != nil {
		return false, nil, err
	}
	d := emailDomain(email)
	for _, x := range domains {
		if strings.EqualFold(x, d) {
			return true, domains, nil
		}
	}
	var n int
	if err := s.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.org_members WHERE org_id = $1 AND email = $2`,
		orgOr(org), normEmail(email)).Scan(&n); err != nil {
		return false, domains, err
	}
	return n > 0, domains, nil
}

func (a *API) ownedRun(w http.ResponseWriter, r *http.Request) (Run, bool) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return Run{}, false
	}
	run, err := a.store.GetRun(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return Run{}, false
	}
	return run, true
}

func (a *API) createShare(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	acc := accessOf(r)
	run, ok := a.ownedRun(w, r)
	if !ok {
		return
	}
	var body struct {
		Email       string `json:"email"`
		CanReview   bool   `json:"can_review"`
		ExpiresDays int    `json:"expires_days"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<14)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	email := normEmail(body.Email)
	if !validEmail(email) {
		say(w, http.StatusBadRequest, "BAD_EMAIL")
		return
	}
	if email == normEmail(c.Email) {
		say(w, http.StatusBadRequest, "SHARE_SELF")
		return
	}
	if run.Kind != KindRun {
		a.fail(w, ErrNotFound, "run")
		return
	}
	ctx := r.Context()
	in, err := a.store.InstallByID(ctx, run.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	inOrg, domains, err := a.store.InOrg(ctx, in.OrgID, email)
	if err != nil {
		a.fail(w, err, "share")
		return
	}
	if !inOrg {
		d := "@" + strings.Join(domains, " หรือ @")
		if len(domains) == 0 {
			d = "โดเมนที่ผู้ดูแลกำหนด"
		}
		say(w, http.StatusBadRequest, "NOT_IN_ORG", "email", email, "domains", d)
		return
	}
	var expires *time.Time
	if body.ExpiresDays > 0 {
		t := time.Now().Add(time.Duration(body.ExpiresDays) * 24 * time.Hour)
		expires = &t
	}
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "share")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	// One live grant per colleague and run: sharing again updates it.
	sh, err := scanShare(tx.QueryRow(ctx, `UPDATE agent_market.run_shares SET can_review = $3, expires_at = $4
		WHERE run_id = $1 AND recipient_email = $2 AND revoked_at IS NULL RETURNING `+shareCols, run.ID, email, body.CanReview, expires))
	if errors.Is(err, pgx.ErrNoRows) {
		sh, err = scanShare(tx.QueryRow(ctx, `INSERT INTO agent_market.run_shares (run_id, org_id, owner_user_id, recipient_email,
			can_review, expires_at) VALUES ($1, $2, $3, $4, $5, $6) RETURNING `+shareCols,
			run.ID, orgOr(in.OrgID), c.UserID, email, body.CanReview, expires))
	}
	if err != nil {
		a.fail(w, err, "share")
		return
	}
	text := Text("แชร์รอบนี้ให้ %s (ดูได้อย่างเดียว) — ผู้รับต้องเข้าสู่ระบบด้วยอีเมลนี้จึงจะเปิดได้ ยกเลิกได้ทุกเมื่อ", Email(email))
	if body.CanReview {
		text = Text("แชร์รอบนี้ให้ %s (ดูและตรวจทานได้) — ผู้รับต้องเข้าสู่ระบบด้วยอีเมลนี้จึงจะเปิดได้ ยกเลิกได้ทุกเมื่อ", Email(email))
	}
	if err := a.store.AddNotice(ctx, tx, run, Notice{Text: text, ShareID: sh.ID.String()}); err != nil {
		a.fail(w, err, "share")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "share")
		return
	}
	_ = acc
	writeJSON(w, http.StatusOK, map[string]any{"share": sh, "path": "/chat/agents/shared/" + sh.ID.String()})
}

func (a *API) listShares(w http.ResponseWriter, r *http.Request) {
	run, ok := a.ownedRun(w, r)
	if !ok {
		return
	}
	rows, err := a.store.Pool.Query(r.Context(), `SELECT `+shareCols+` FROM agent_market.run_shares WHERE run_id = $1
		ORDER BY created_at DESC`, run.ID)
	if err != nil {
		a.fail(w, err, "share")
		return
	}
	defer rows.Close()
	out := []Share{}
	for rows.Next() {
		sh, err := scanShare(rows)
		if err != nil {
			a.fail(w, err, "share")
			return
		}
		out = append(out, sh)
	}
	writeJSON(w, http.StatusOK, map[string]any{"shares": out})
}

func (a *API) revokeShare(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	ctx := r.Context()
	sh, err := a.store.ShareByID(ctx, id)
	if err != nil || sh.OwnerUserID != c.UserID {
		a.fail(w, ErrNotFound, "share")
		return
	}
	run, err := a.store.RunByID(ctx, sh.RunID)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if sh.RevokedAt == nil {
		if _, err := a.store.Pool.Exec(ctx, `UPDATE agent_market.run_shares SET revoked_at = now() WHERE id = $1`, sh.ID); err != nil {
			a.fail(w, err, "share")
			return
		}
		_ = a.store.PostNotice(ctx, run, Text("ยกเลิกการแชร์ให้ %s แล้ว ลิงก์เดิมเปิดไม่ได้อีก", Email(sh.RecipientEmail)), "")
	}
	writeJSON(w, http.StatusOK, map[string]any{"revoked": true})
}

// people suggests colleagues to share with: the access list, then (a read of
// the core's user table) signed-up users at the organisation's domains.
func (a *API) people(w http.ResponseWriter, r *http.Request) {
	acc := accessOf(r)
	c, _ := caller(r)
	q := normEmail(r.URL.Query().Get("q"))
	type person struct {
		Email string `json:"email"`
		Name  string `json:"name"`
	}
	out := []person{}
	if len([]rune(q)) < 2 {
		writeJSON(w, http.StatusOK, map[string]any{"people": out})
		return
	}
	ctx := r.Context()
	seen := map[string]bool{normEmail(c.Email): true}
	like := "%" + strings.NewReplacer("%", "", "_", "").Replace(q) + "%"
	rows, err := a.store.Pool.Query(ctx, `SELECT email, display_name FROM agent_market.org_members
		WHERE org_id = $1 AND (email LIKE $2 OR lower(display_name) LIKE $2) ORDER BY email LIMIT 8`, acc.OrgID, like)
	if err == nil {
		for rows.Next() {
			var p person
			if rows.Scan(&p.Email, &p.Name) == nil && !seen[p.Email] {
				seen[p.Email] = true
				out = append(out, p)
			}
		}
		rows.Close()
	}
	var domains []string
	_ = a.store.Pool.QueryRow(ctx, `SELECT domains FROM agent_market.org_settings WHERE org_id = $1`, acc.OrgID).Scan(&domains)
	if len(out) < 8 && len(domains) > 0 {
		rows, err := a.store.Pool.Query(ctx, `SELECT lower(email), name FROM identity.users
			WHERE is_active AND (lower(email) LIKE $1 OR lower(name) LIKE $1) AND split_part(lower(email), '@', 2) = ANY($2)
			ORDER BY email LIMIT 8`, like, domains)
		if err == nil {
			for rows.Next() {
				var p person
				if rows.Scan(&p.Email, &p.Name) == nil && !seen[p.Email] && len(out) < 8 {
					seen[p.Email] = true
					out = append(out, p)
				}
			}
			rows.Close()
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"people": out})
}

// ── the recipient's side ─────────────────────────────────────────────────

// openShare checks that this signed-in person is the named recipient of a
// live share in their own organisation.
func (a *API) openShare(w http.ResponseWriter, r *http.Request) (Share, Run, bool) {
	c, _ := caller(r)
	acc := accessOf(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return Share{}, Run{}, false
	}
	ctx := r.Context()
	sh, err := a.store.ShareByID(ctx, id)
	if err != nil || !strings.EqualFold(sh.RecipientEmail, normEmail(c.Email)) || !sh.Active(time.Now()) ||
		orgOr(sh.OrgID) != orgOr(acc.OrgID) {
		say(w, http.StatusForbidden, "NOT_SHARED")
		return Share{}, Run{}, false
	}
	run, err := a.store.RunByID(ctx, sh.RunID)
	if err != nil {
		a.fail(w, err, "run")
		return Share{}, Run{}, false
	}
	return sh, run, true
}

func (a *API) sharedWithMe(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	rows, err := a.store.Pool.Query(r.Context(), `
		SELECT s.id, s.can_review, s.created_at, s.expires_at, s.last_opened_at, r.id, r.status, r.gate_open, r.period, r.finished_at,
			i.title, i.slug, s.owner_user_id, i.org_id
		FROM agent_market.run_shares s
		JOIN agent_market.agent_runs r ON r.id = s.run_id
		JOIN agent_market.agent_installs i ON i.id = r.install_id
		WHERE s.recipient_email = $1 AND s.revoked_at IS NULL AND (s.expires_at IS NULL OR s.expires_at > now())
		ORDER BY s.created_at DESC LIMIT 100`, normEmail(c.Email))
	if err != nil {
		a.fail(w, err, "share")
		return
	}
	defer rows.Close()
	type item struct {
		ID           uuid.UUID  `json:"id"`
		CanReview    bool       `json:"can_review"`
		CreatedAt    time.Time  `json:"created_at"`
		ExpiresAt    *time.Time `json:"expires_at"`
		LastOpenedAt *time.Time `json:"last_opened_at"`
		RunID        uuid.UUID  `json:"run_id"`
		RunStatus    string     `json:"run_status"`
		RunOutcome   string     `json:"run_outcome"` // AM6: what the run came to
		Period       *string    `json:"period"`
		FinishedAt   *time.Time `json:"finished_at"`
		Agent        string     `json:"agent"`
		Slug         string     `json:"slug"`
		Owner        string     `json:"owner"`
		owner        uuid.UUID
		org          string
	}
	var list []item
	for rows.Next() {
		var x item
		var open bool
		if err := rows.Scan(&x.ID, &x.CanReview, &x.CreatedAt, &x.ExpiresAt, &x.LastOpenedAt, &x.RunID, &x.RunStatus, &open, &x.Period,
			&x.FinishedAt, &x.Agent, &x.Slug, &x.owner, &x.org); err != nil {
			a.fail(w, err, "share")
			return
		}
		x.RunOutcome = OutcomeOf(x.RunStatus, open)
		list = append(list, x)
	}
	rows.Close()
	for i := range list {
		list[i].Owner = a.store.DisplayName(r.Context(), list[i].org, list[i].owner, "")
	}
	if list == nil {
		list = []item{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"shared": list})
}

func (a *API) sharedRun(w http.ResponseWriter, r *http.Request) {
	sh, run, ok := a.openShare(w, r)
	if !ok {
		return
	}
	c, _ := caller(r)
	ctx := r.Context()
	_, _ = a.store.Pool.Exec(ctx, `UPDATE agent_market.run_shares SET opens = opens + 1, last_opened_at = now(),
		recipient_user_id = COALESCE(recipient_user_id, $2) WHERE id = $1`, sh.ID, c.UserID)
	in, err := a.store.InstallByID(ctx, run.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	arts, err := a.store.RunArtifacts(ctx, run.ID)
	if err != nil {
		a.fail(w, err, "artifact")
		return
	}
	exc, err := a.store.RunExceptions(ctx, run.ID)
	if err != nil {
		a.fail(w, err, "exception")
		return
	}
	reviews, err := a.store.RunReviews(ctx, run.ID)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	var summary json.RawMessage
	_ = a.store.Pool.QueryRow(ctx, `SELECT payload FROM agent_market.agent_thread_items WHERE run_id = $1 AND kind = 'outputs'
		ORDER BY rev DESC LIMIT 1`, run.ID).Scan(&summary)
	if len(summary) > 0 {
		summary = ForClient("outputs", summary) // the files card, as the run's owner is sent it
	}
	owner := a.store.DisplayName(ctx, in.OrgID, in.UserID, "")
	var agentName string
	if d, err := a.store.DefinitionByID(ctx, in.DefinitionID); err == nil {
		agentName = d.Name
	}
	writeJSON(w, http.StatusOK, map[string]any{"share": sh, "owner": owner, "agent": map[string]any{"title": in.Title,
		"name": agentName, "slug": in.Slug, "version": run.DefinitionVersion},
		"run": map[string]any{"id": run.ID, "status": run.Status, "outcome": run.Outcome(), "status_reason": run.StatusReason, "period": run.Period,
			"created_at": run.CreatedAt, "finished_at": run.FinishedAt, "assumptions": run.Assumptions, "model_used": run.ModelUsed,
			"model_self_hosted": run.ModelSelfHosted},
		"artifacts": nonNil(latestArtifacts(arts)), "exceptions": nonNil(exc), "reviews": nonNil(reviews), "outputs": summary})
}

func (a *API) sharedArtifact(w http.ResponseWriter, r *http.Request) {
	_, run, ok := a.openShare(w, r)
	if !ok {
		return
	}
	aid, err := uuid.Parse(chi.URLParam(r, "aid"))
	if err != nil {
		badRequest(w, "BAD_ID")
		return
	}
	arts, err := a.store.RunArtifacts(r.Context(), run.ID)
	if err != nil {
		a.fail(w, err, "artifact")
		return
	}
	for _, x := range arts {
		if x.ID == aid {
			a.serveArtifact(w, r, x)
			return
		}
	}
	a.fail(w, ErrNotFound, "artifact")
}
