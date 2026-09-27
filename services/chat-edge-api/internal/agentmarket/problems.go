package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── "Report a problem with this run" (AM3) ───────────────────────────────
//
// One button: the person writes one sentence; we capture the run, where it
// was (its status and the step it stopped at, with that step's error), the
// agent and version, and the page they were on. It lands in
// agent_market.problem_reports, on the admins' page, and — through the
// runner's gauge of open reports and its Prometheus alert — on the Agent
// Market pager topic. A wrong number finally has somewhere to go.

// ProblemReport is one report.
type ProblemReport struct {
	ID        uuid.UUID  `json:"id"`
	Ref       string     `json:"ref"`
	Email     string     `json:"email"`
	InstallID *uuid.UUID `json:"install_id,omitempty"`
	RunID     *uuid.UUID `json:"run_id,omitempty"`
	Slug      string     `json:"slug"`
	Version   string     `json:"version"`
	RunStatus string     `json:"run_status"`
	RunReason string     `json:"run_reason"`
	Step      string     `json:"step"`
	StepError string     `json:"step_error"`
	Page      string     `json:"page"`
	Text      string     `json:"text"`
	Status    string     `json:"status"`
	AdminNote string     `json:"admin_note"`
	ClosedBy  string     `json:"closed_by"`
	CreatedAt time.Time  `json:"created_at"`
	ClosedAt  *time.Time `json:"closed_at,omitempty"`
}

const problemCols = `id, email, install_id, run_id, slug, version, run_status, run_reason, step, step_error, page, text, status,
	admin_note, closed_by, created_at, closed_at`

func scanProblem(r pgx.Row) (ProblemReport, error) {
	var p ProblemReport
	err := r.Scan(&p.ID, &p.Email, &p.InstallID, &p.RunID, &p.Slug, &p.Version, &p.RunStatus, &p.RunReason, &p.Step, &p.StepError,
		&p.Page, &p.Text, &p.Status, &p.AdminNote, &p.ClosedBy, &p.CreatedAt, &p.ClosedAt)
	p.Ref = strings.ToUpper(p.ID.String()[:6])
	return p, err
}

type problemReq struct {
	Text string `json:"text"`
	Page string `json:"page"`
}

func readProblem(w http.ResponseWriter, r *http.Request) (problemReq, bool) {
	var body problemReq
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<15)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return body, false
	}
	body.Text = strings.TrimSpace(body.Text)
	if body.Text == "" {
		say(w, http.StatusBadRequest, "EMPTY_PROBLEM")
		return body, false
	}
	if r := []rune(body.Text); len(r) > 2000 {
		body.Text = string(r[:2000])
	}
	if r := []rune(body.Page); len(r) > 300 {
		body.Page = string(r[:300])
	}
	return body, true
}

// fileProblem captures the run's context and stores the report.
func (s *Store) fileProblem(ctx context.Context, org string, user uuid.UUID, email string, in *Install, run *Run, body problemReq) (ProblemReport, error) {
	var p ProblemReport
	var installID, runID *uuid.UUID
	slug, version, status, reason, step, stepErr := "", "", "", "", "", ""
	if in != nil {
		id := in.ID
		installID = &id
		slug, version = in.Slug, in.DefinitionVersion
	}
	if run != nil {
		id := run.ID
		runID = &id
		status, reason, version = run.Status, run.StatusReason, run.DefinitionVersion
		// the step it stopped at: the last one that failed, else the last one
		_ = s.Pool.QueryRow(ctx, `SELECT name, error FROM agent_market.agent_run_steps WHERE run_id = $1
			ORDER BY (status = 'failed') DESC, seq DESC LIMIT 1`, run.ID).Scan(&step, &stepErr)
		if r := []rune(stepErr); len(r) > 1500 {
			stepErr = string(r[:1500])
		}
	}
	p, err := scanProblem(s.Pool.QueryRow(ctx, `INSERT INTO agent_market.problem_reports (org_id, user_id, email, install_id, run_id,
		slug, version, run_status, run_reason, step, step_error, page, text)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13) RETURNING `+problemCols,
		orgOr(org), user, normEmail(email), installID, runID, slug, version, status, reason, step, stepErr, body.Page, body.Text))
	return p, err
}

// problemNotice is what the person sees in their thread (and why it matters).
func problemNotice(p ProblemReport) string {
	return "ส่งเรื่องถึงผู้ดูแล Agent Market แล้ว (เลขอ้างอิง #" + p.Ref + ") พร้อมรอบงาน ขั้นตอนที่หยุด และข้อความของคุณ " +
		"— ผู้ดูแลจะเห็นในหน้าผู้ดูแลและได้รับแจ้งเตือน ข้อมูลและไฟล์ของคุณยังอยู่ครบ ไม่มีอะไรถูกเปลี่ยน"
}

func (a *API) runProblem(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	run, ok := a.ownedRun(w, r)
	if !ok {
		return
	}
	body, ok := readProblem(w, r)
	if !ok {
		return
	}
	ctx := r.Context()
	in, err := a.store.InstallByID(ctx, run.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	p, err := a.store.fileProblem(ctx, in.OrgID, c.UserID, c.Email, &in, &run, body)
	if err != nil {
		a.fail(w, err, "problem")
		return
	}
	_ = a.store.PostNotice(ctx, run, problemNotice(p), "")
	writeJSON(w, http.StatusOK, map[string]any{"report": p, "message": problemNotice(p)})
}

func (a *API) installProblem(w http.ResponseWriter, r *http.Request) {
	in, c, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	body, ok := readProblem(w, r)
	if !ok {
		return
	}
	ctx := r.Context()
	var run *Run
	if lr, err := a.store.LatestRun(ctx, in.ID); err == nil {
		run = &lr
	} else if !errors.Is(err, ErrNotFound) {
		a.fail(w, err, "run")
		return
	}
	p, err := a.store.fileProblem(ctx, in.OrgID, c.UserID, c.Email, &in, run, body)
	if err != nil {
		a.fail(w, err, "problem")
		return
	}
	if _, _, err := a.store.AddItem(ctx, a.store.Pool, in.ID, in.TenantID, in.UserID, nil, "notice",
		map[string]any{"text": problemNotice(p)}, nil); err != nil {
		a.log.Warn("problem notice not posted", "err", err)
	}
	writeJSON(w, http.StatusOK, map[string]any{"report": p, "message": problemNotice(p)})
}

// sharedProblem: a colleague reading a shared run can report it too.
func (a *API) sharedProblem(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	_, run, ok := a.openShare(w, r)
	if !ok {
		return
	}
	body, ok := readProblem(w, r)
	if !ok {
		return
	}
	ctx := r.Context()
	in, err := a.store.InstallByID(ctx, run.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	body.Page = strings.TrimSpace("(แชร์) " + body.Page)
	p, err := a.store.fileProblem(ctx, in.OrgID, c.UserID, c.Email, &in, &run, body)
	if err != nil {
		a.fail(w, err, "problem")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"report": p, "message": problemNotice(p)})
}

// OpenProblems counts the open reports (the runner's gauge → the pager).
func (s *Store) OpenProblems(ctx context.Context) (int64, error) {
	var n int64
	err := s.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.problem_reports WHERE status = 'open'`).Scan(&n)
	return n, err
}
