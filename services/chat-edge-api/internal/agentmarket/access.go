package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── An admin and other people's runs (AM4) ───────────────────────────────
//
// An admin always sees what operating the system needs — each run's status,
// cost, model and the step where it stopped — and never its content. The
// files, figures and answers of someone else's run (other people's payroll
// and supplier data) open one run at a time: the admin states a reason, the
// opening is written to run_access_log before anything is shown, it lasts an
// hour, and the run's owner is told in their own thread. There is no global
// read.

// AccessGrantTTL is how long one opening lasts.
const AccessGrantTTL = time.Hour

// AccessEntry is one opening, as the run's owner sees it.
type AccessEntry struct {
	AdminName  string    `json:"admin_name"`
	AdminEmail string    `json:"admin_email"`
	Reason     string    `json:"reason"`
	OpenedAt   time.Time `json:"opened_at"`
	At         string    `json:"at_label"`
}

// AccessLog lists every opening of a run by an admin, newest first.
func (s *Store) AccessLog(ctx context.Context, run uuid.UUID) ([]AccessEntry, error) {
	rows, err := s.Pool.Query(ctx, `SELECT admin_name, admin_email, reason, opened_at FROM agent_market.run_access_log
		WHERE run_id = $1 ORDER BY opened_at DESC`, run)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []AccessEntry{}
	for rows.Next() {
		var e AccessEntry
		if err := rows.Scan(&e.AdminName, &e.AdminEmail, &e.Reason, &e.OpenedAt); err != nil {
			return nil, err
		}
		// AM11: said when the log is read, so in the language of the request that reads it
		e.At = DateTimeIn(LangOf(ctx), e.OpenedAt)
		out = append(out, e)
	}
	return out, rows.Err()
}

// HasAccess: this admin opened this run (with a reason) and the hour has not passed.
func (s *Store) HasAccess(ctx context.Context, run, admin uuid.UUID) (bool, error) {
	var ok bool
	err := s.Pool.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM agent_market.run_access_log
		WHERE run_id = $1 AND admin_user_id = $2 AND expires_at > now())`, run, admin).Scan(&ok)
	return ok, err
}

// orgRun reads a run of the admin's organisation (any owner).
func (s *Store) orgRun(ctx context.Context, org string, id uuid.UUID) (Run, error) {
	x, err := scanRun(s.Pool.QueryRow(ctx, `SELECT `+prefixed("r.", runCols)+` FROM agent_market.agent_runs r
		JOIN agent_market.agent_installs i ON i.id = r.install_id
		WHERE r.id = $1 AND i.org_id = $2 AND i.status <> 'catalog'`, id, orgOr(org)))
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	return x, err
}

func prefixed(p, cols string) string {
	parts := strings.Split(cols, ",")
	for i, c := range parts {
		parts[i] = p + strings.TrimSpace(c)
	}
	return strings.Join(parts, ", ")
}

// RunMeta is what an admin sees of any run: never its files, figures or answers.
type RunMeta struct {
	ID          uuid.UUID  `json:"id"`
	Agent       string     `json:"agent"`
	Slug        string     `json:"slug"`
	Person      string     `json:"person"`
	Email       string     `json:"email"`
	Status      string     `json:"status"`
	Outcome     string     `json:"outcome"` // AM6: completed | completed_open | failed | cancelled, or the status while it runs
	Reason      string     `json:"reason"`
	Period      string     `json:"period_label"`
	Credits     float64    `json:"credits"` // what the person was charged
	Cost        float64    `json:"cost"`    // AM6: what the platform paid (more than Credits for a round that delivered nothing)
	Model       string     `json:"model"`
	SelfHosted  *bool      `json:"model_self_hosted"`
	StoppedAt   string     `json:"stopped_at_step,omitempty"`
	CreatedAt   time.Time  `json:"created_at"`
	FinishedAt  *time.Time `json:"finished_at,omitempty"`
	OpenedByMe  bool       `json:"open_now"`
	TimesOpened int        `json:"times_opened"`
}

// OrgRuns lists the organisation's runs as metadata, newest first.
func (s *Store) OrgRuns(ctx context.Context, org string, admin uuid.UUID, status string, limit int) ([]RunMeta, error) {
	q := `SELECT r.id, i.title, i.slug, COALESCE(m.display_name, ''), COALESCE(m.email, ''), r.status, r.status_reason,
			COALESCE(r.period, ''), r.credits_spent::float8, r.credits_cost::float8, r.gate_open,
			COALESCE(r.model_used, ''), r.model_self_hosted,
			COALESCE((SELECT st.name FROM agent_market.agent_run_steps st WHERE st.run_id = r.id AND st.status = 'failed'
				ORDER BY st.seq DESC LIMIT 1), ''),
			r.created_at, r.finished_at,
			EXISTS (SELECT 1 FROM agent_market.run_access_log l WHERE l.run_id = r.id AND l.admin_user_id = $2 AND l.expires_at > now()),
			(SELECT count(*) FROM agent_market.run_access_log l WHERE l.run_id = r.id)::int
		FROM agent_market.agent_runs r
		JOIN agent_market.agent_installs i ON i.id = r.install_id
		LEFT JOIN agent_market.org_members m ON m.org_id = i.org_id AND m.user_id = r.user_id
		WHERE i.org_id = $1 AND i.status <> 'catalog' AND r.kind = 'run'`
	args := []any{orgOr(org), admin}
	if status != "" && status != "all" {
		q += ` AND r.status = $3`
		args = append(args, status)
	}
	if limit <= 0 || limit > 500 {
		limit = 200
	}
	q += ` ORDER BY r.created_at DESC LIMIT ` + strconv.Itoa(limit)
	rows, err := s.Pool.Query(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []RunMeta{}
	for rows.Next() {
		var m RunMeta
		var period string
		var open bool
		if err := rows.Scan(&m.ID, &m.Agent, &m.Slug, &m.Person, &m.Email, &m.Status, &m.Reason, &period, &m.Credits, &m.Cost, &open,
			&m.Model, &m.SelfHosted, &m.StoppedAt, &m.CreatedAt, &m.FinishedAt, &m.OpenedByMe, &m.TimesOpened); err != nil {
			return nil, err
		}
		m.Outcome = OutcomeOf(m.Status, open)
		if period != "" {
			m.Period = PeriodLabelIn(LangOf(ctx), period)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

func (a *API) adminRuns(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	c, _ := caller(r)
	list, err := a.store.OrgRuns(r.Context(), accessOf(r).OrgID, c.UserID, r.URL.Query().Get("status"), 200)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"runs": list, "grant_minutes": int(AccessGrantTTL.Minutes())})
}

// adminOpenRun records the opening (with the admin's reason) and tells the
// run's owner, before anything of the run is shown.
func (a *API) adminOpenRun(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
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
		Reason string `json:"reason"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	reason := strings.TrimSpace(body.Reason)
	if len([]rune(reason)) < 8 || len([]rune(reason)) > 500 {
		say(w, http.StatusBadRequest, "ACCESS_REASON_REQUIRED")
		return
	}
	ctx := r.Context()
	run, err := a.store.orgRun(ctx, acc.OrgID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	name := a.store.DisplayName(ctx, acc.OrgID, c.UserID, c.Email)
	if act, ok := adminActorOf(ctx); ok && name == "" {
		name = act.Name // AM7: an admin-ui account by the name it signs in with
	}
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	expires := time.Now().Add(AccessGrantTTL)
	if _, err := tx.Exec(ctx, `INSERT INTO agent_market.run_access_log (run_id, org_id, admin_user_id, admin_email, admin_name, reason, expires_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7)`, run.ID, orgOr(acc.OrgID), c.UserID, normEmail(c.Email), name, reason, expires); err != nil {
		a.fail(w, err, "run")
		return
	}
	if run.UserID != c.UserID {
		who := name
		if who == "" {
			who = normEmail(c.Email)
		}
		// the run named by its period, glued to its word in Thai as it always was; the English brings its
		// own space ("your run September 2026"), and a run with no period yet is just "your run"
		named := Each(func(l Lang) string {
			p := readableIn(l, periodIn(l, run), 120)
			if l == English && p != "" {
				return " " + p
			}
			return p
		})
		text := Text("ผู้ดูแล %s เปิดดูรอบ%s ของคุณ เมื่อ %s เหตุผล: “%s” — ทุกครั้งที่ผู้ดูแลเปิดดูรอบของคุณ ระบบจะแจ้งที่นี่ "+
			"และหน้าที่มาของตัวเลขของรอบนั้นมีรายการไว้ทั้งหมด", Name(who), named, When(time.Now()), Typed(reason, 500))
		if err := a.store.AddNotice(ctx, tx, run, Notice{Text: text, Tone: "warning", Access: true}); err != nil {
			a.fail(w, err, "run")
			return
		}
	}
	// AM7: the opening is in the admin audit log too, or it does not happen
	if err := a.record(ctx, []AuditEntry{entry(r, "agent_market.run.opened", "agent_market_run", run.ID.String(),
		map[string]any{"reason": reason, "agent": run.InstallID.String(), "period": periodIn(LangOf(ctx), run), "owner_told": run.UserID != c.UserID,
			"expires_at": expires.UTC().Format(time.RFC3339)})}); err != nil {
		a.fail(w, err, "run")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "run")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"opened": true, "expires_at": expires})
}

// adminRunGrant reads the run behind an open grant (403 without one).
func (a *API) adminRunGrant(w http.ResponseWriter, r *http.Request) (Run, bool) {
	if !a.requireAdmin(w, r) {
		return Run{}, false
	}
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return Run{}, false
	}
	ctx := r.Context()
	run, err := a.store.orgRun(ctx, accessOf(r).OrgID, id)
	if err != nil {
		a.fail(w, err, "run")
		return Run{}, false
	}
	if ok, err := a.store.HasAccess(ctx, run.ID, c.UserID); err != nil {
		a.fail(w, err, "run")
		return Run{}, false
	} else if !ok {
		say(w, http.StatusForbidden, "ACCESS_REASON_NEEDED")
		return Run{}, false
	}
	return run, true
}

func (a *API) adminRunContent(w http.ResponseWriter, r *http.Request) {
	run, ok := a.adminRunGrant(w, r)
	if !ok {
		return
	}
	out, what, err := a.runDetail(r.Context(), run)
	if err != nil {
		a.fail(w, err, what)
		return
	}
	log, _ := a.store.AccessLog(r.Context(), run.ID)
	out["access_log"] = log
	writeJSON(w, http.StatusOK, out)
}

func (a *API) adminRunComputed(w http.ResponseWriter, r *http.Request) {
	run, ok := a.adminRunGrant(w, r)
	if !ok {
		return
	}
	a.serveComputed(w, r, run)
}

func (a *API) adminRunArtifact(w http.ResponseWriter, r *http.Request) {
	run, ok := a.adminRunGrant(w, r)
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

func (a *API) adminRunFile(w http.ResponseWriter, r *http.Request) {
	run, ok := a.adminRunGrant(w, r)
	if !ok {
		return
	}
	fid, err := uuid.Parse(chi.URLParam(r, "fid"))
	if err != nil {
		badRequest(w, "BAD_ID")
		return
	}
	files, err := a.store.RunFiles(r.Context(), run.ID)
	if err != nil {
		a.fail(w, err, "file")
		return
	}
	for _, f := range files {
		if f.ID == fid {
			body, _, _, err := a.files.Download(r.Context(), f.StorageKey)
			if err != nil {
				a.fail(w, err, "file")
				return
			}
			defer body.Close()
			w.Header().Set("Content-Type", "application/octet-stream")
			w.Header().Set("Content-Disposition", contentDisposition(f.Name))
			_, _ = io.Copy(w, body)
			return
		}
	}
	a.fail(w, ErrNotFound, "file")
}

// adminRemoveEvidence removes a finished round's files (in and out) before
// the organisation's retention ends — an admin's decision, with a reason,
// told to the run's owner. The run's row stays as the record that it happened.
func (a *API) adminRemoveEvidence(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
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
		Reason string `json:"reason"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	reason := strings.TrimSpace(body.Reason)
	if len([]rune(reason)) < 8 || len([]rune(reason)) > 500 {
		say(w, http.StatusBadRequest, "REMOVE_REASON_REQUIRED")
		return
	}
	ctx := r.Context()
	run, err := a.store.orgRun(ctx, acc.OrgID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if !IsTerminal(run.Status) {
		say(w, http.StatusConflict, "ROUND_IN_PROGRESS")
		return
	}
	// AM7: files cannot be put back, so the removal is logged before anything is deleted
	logged := []AuditEntry{entry(r, "agent_market.run.evidence_removed", "agent_market_run", run.ID.String(),
		map[string]any{"reason": reason, "period": periodIn(LangOf(ctx), run)})}
	if err := a.record(ctx, logged); err != nil {
		a.fail(w, err, "run")
		return
	}
	n, err := a.store.RemoveRunEvidence(ctx, a.files, run, normEmail(c.Email), reason)
	if err != nil {
		a.recordFailed(ctx, logged)
		a.fail(w, err, "run")
		return
	}
	_ = a.store.AddNotice(ctx, a.store.Pool, run, Notice{Tone: "warning",
		Text: Text("ผู้ดูแล %s ลบไฟล์ของรอบนี้ (ไฟล์ที่ส่งเข้าและไฟล์ผลลัพธ์) ก่อนครบระยะเก็บ เหตุผล: “%s” — บันทึกว่ารอบนี้เคยทำยังอยู่",
			Email(normEmail(c.Email)), Typed(reason, 500))})
	writeJSON(w, http.StatusOK, map[string]any{"removed_objects": n})
}
