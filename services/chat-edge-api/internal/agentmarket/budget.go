package agentmarket

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/google/uuid"
)

// ── Budgets (AM3) ────────────────────────────────────────────────────────
//
// Three monthly limits, checked before every step that costs credits: the
// person's (their own, or the organisation's per-person default), their
// department's (when the admins set one), and the install's (AM1). The month
// is Bangkok's. A limit that is reached never fails a run: the run waits
// (awaiting_input, status_reason budget_<which>) with a notice that names
// who to ask, and goes on by itself once there is budget again — raised by
// an admin, or a new month.

// monthStart is the first instant of the current month in Bangkok.
const monthStart = `(date_trunc('month', now() AT TIME ZONE 'Asia/Bangkok') AT TIME ZONE 'Asia/Bangkok')`

// Which budget ran out.
const (
	BudgetUser       = "user"
	BudgetDepartment = "department"
	BudgetInstall    = "install"
)

// BudgetState is one install's three limits this month.
type BudgetState struct {
	UserLimit    float64  `json:"user_limit"`
	UserSpent    float64  `json:"user_spent"`
	UserOwn      bool     `json:"user_own_limit"` // a personal limit, not the organisation's default
	Department   string   `json:"department,omitempty"`
	DeptLimit    *float64 `json:"department_limit,omitempty"`
	DeptSpent    float64  `json:"department_spent"`
	InstallLimit float64  `json:"install_limit"`
	InstallSpent float64  `json:"install_spent"`
	Exhausted    string   `json:"exhausted"` // "" | user | department | install
	Contact      Contact  `json:"contact"`   // who to ask (for the one that ran out, else the organisation's)
}

// Remaining is the smallest room left under any limit.
func (b BudgetState) Remaining() float64 {
	left := b.UserLimit - b.UserSpent
	if v := b.InstallLimit - b.InstallSpent; v < left {
		left = v
	}
	if b.DeptLimit != nil {
		if v := *b.DeptLimit - b.DeptSpent; v < left {
			left = v
		}
	}
	return left
}

// Budget measures an install's limits (its owner's organisation).
func (s *Store) Budget(ctx context.Context, in Install) (BudgetState, error) {
	org := orgOr(in.OrgID)
	var st BudgetState
	var orgContact Contact
	if err := s.Pool.QueryRow(ctx, `SELECT user_monthly_credits::float8, contact_name, contact_email
		FROM agent_market.org_settings WHERE org_id = $1`, org).Scan(&st.UserLimit, &orgContact.Name, &orgContact.Email); err != nil {
		// no organisation row (a runner ahead of the control plane): AM1's install limit only
		st.UserLimit = 0
	}
	if orgContact.Name == "" && orgContact.Email == "" {
		orgContact = s.firstAdmin(ctx, org)
	}
	st.Contact = orgContact
	var deptContact Contact
	if m, err := s.MemberByUser(ctx, org, in.UserID); err == nil {
		if m.MonthlyCredits != nil {
			st.UserLimit = *m.MonthlyCredits
			st.UserOwn = true
		}
		st.Department = m.Department
	} else if !errors.Is(err, ErrNotFound) {
		return st, err
	}
	if st.Department != "" {
		var lim *float64
		err := s.Pool.QueryRow(ctx, `SELECT monthly_credits::float8, contact_name, contact_email FROM agent_market.org_departments
			WHERE org_id = $1 AND name = $2`, org, st.Department).Scan(&lim, &deptContact.Name, &deptContact.Email)
		if err == nil {
			st.DeptLimit = lim
		}
		if st.DeptLimit != nil {
			if err := s.Pool.QueryRow(ctx, `SELECT COALESCE(sum(l.credits), 0)::float8 FROM agent_market.agent_budget_ledger l
				JOIN agent_market.agent_installs i ON i.id = l.install_id
				JOIN agent_market.org_members m ON m.user_id = i.user_id AND m.org_id = i.org_id
				WHERE i.org_id = $1 AND m.department = $2 AND l.voided_at IS NULL AND l.created_at >= `+monthStart,
				org, st.Department).Scan(&st.DeptSpent); err != nil {
				return st, err
			}
		}
	}
	if err := s.Pool.QueryRow(ctx, `SELECT COALESCE(sum(l.credits), 0)::float8 FROM agent_market.agent_budget_ledger l
		JOIN agent_market.agent_installs i ON i.id = l.install_id
		WHERE i.user_id = $1 AND i.org_id = $2 AND l.voided_at IS NULL AND l.created_at >= `+monthStart, in.UserID, org).Scan(&st.UserSpent); err != nil {
		return st, err
	}
	st.InstallLimit = in.BudgetCredits
	spent, err := s.SpentThisMonth(ctx, in.ID)
	if err != nil {
		return st, err
	}
	st.InstallSpent = spent
	switch {
	case in.UserID == uuid.Nil:
		// the catalog's own sample install: nobody's budget
	case st.UserLimit > 0 && st.UserSpent >= st.UserLimit:
		st.Exhausted = BudgetUser
	case st.DeptLimit != nil && st.DeptSpent >= *st.DeptLimit:
		st.Exhausted = BudgetDepartment
		if deptContact.Name != "" || deptContact.Email != "" {
			st.Contact = deptContact
		}
	case st.InstallLimit > 0 && st.InstallSpent >= st.InstallLimit:
		st.Exhausted = BudgetInstall
	}
	return st, nil
}

// budgetWhich names the budget inside a sentence, in l. The Thai glues a
// department's name to its word; the English says it after a space — which is
// why it is one sentence with the name in it, not a word and a name.
func budgetWhich(l Lang, st BudgetState) string {
	switch st.Exhausted {
	case BudgetDepartment:
		return fmt.Sprintf(T(l, "ของแผนก%s"), st.Department)
	case BudgetInstall:
		return T(l, "ของ agent ตัวนี้")
	}
	return T(l, "ของคุณ")
}

// BudgetNotice is the thread's notice when a run waits for budget. AM11b: it
// says whose budget and who can raise it — never how many credits were used
// or allowed; those are on the admin's Spend page.
func BudgetNotice(st BudgetState) PersonText {
	return Text("หยุดรอไว้ก่อนครับ: งบ%sเดือนนี้ใช้หมดแล้ว — ขอเพิ่มได้จาก %s "+
		"เมื่อมีงบแล้ว รอบนี้จะทำต่อจากจุดเดิมเอง (หรือกด “ลองต่อ”) ไฟล์และคำตอบที่ให้ไว้ยังอยู่ครบ และแชทปกติยังใช้ได้ตามเดิม",
		EachNamed(120, func(l Lang) string { return budgetWhich(l, st) }), ContactFact(st.Contact))
}

// groupDigits prints a credit amount with thousands separators.
func groupDigits(v float64) string {
	s := fmt.Sprintf("%.0f", v)
	neg := strings.HasPrefix(s, "-")
	s = strings.TrimPrefix(s, "-")
	var b strings.Builder
	for i, ch := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(ch)
	}
	if neg {
		return "-" + b.String()
	}
	return b.String()
}

// BudgetParked lists the runs waiting for budget (in one organisation, or all when org is "").
func (s *Store) BudgetParked(ctx context.Context, org string) ([]Run, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+prefixCols("r.", runCols)+` FROM agent_market.agent_runs r
		JOIN agent_market.agent_installs i ON i.id = r.install_id
		WHERE r.status = 'awaiting_input' AND r.status_reason LIKE 'budget_%' AND NOT r.cancel_requested
			AND ($1 = '' OR i.org_id = $1) ORDER BY r.created_at`, org)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Run
	for rows.Next() {
		x, err := scanRun(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

// ResumeBudgetParked puts back in the queue every budget-parked run whose
// limits have room again (an admin raised one, or a new month began).
func (s *Store) ResumeBudgetParked(ctx context.Context, org string) (int, error) {
	runs, err := s.BudgetParked(ctx, org)
	if err != nil {
		return 0, err
	}
	n := 0
	for _, run := range runs {
		in, err := s.InstallByID(ctx, run.InstallID)
		if err != nil {
			continue
		}
		st, err := s.Budget(ctx, in)
		if err != nil || st.Exhausted != "" {
			continue
		}
		if ok, err := s.Resume(ctx, s.Pool, run.ID); err == nil && ok {
			_ = s.PostNotice(ctx, run, Text("มีงบแล้ว รอบนี้ทำต่อจากจุดเดิมครับ"), "")
			n++
		}
	}
	return n, nil
}

// prefixCols qualifies a column list with a table alias.
func prefixCols(prefix, cols string) string {
	parts := strings.Split(cols, ",")
	for i, p := range parts {
		parts[i] = prefix + strings.TrimSpace(p)
	}
	return strings.Join(parts, ", ")
}

// ── handlers ─────────────────────────────────────────────────────────────

// installBudget: the workspace's banner (how much is left, and whom to ask).
func (a *API) installBudget(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	st, err := a.store.Budget(r.Context(), in)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"budget": st, "remaining": st.Remaining()})
}

// resumeBudget is the notice's "ลองต่อ": re-check, and go on if there is room.
func (a *API) resumeBudget(w http.ResponseWriter, r *http.Request) {
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
	if run.Status != RunAwaitingInput || !strings.HasPrefix(run.StatusReason, "budget_") {
		say(w, http.StatusConflict, "NOT_BUDGET_PARKED")
		return
	}
	in, err := a.store.InstallByID(ctx, run.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if !a.agentGate(w, r, in.Slug, false) {
		return
	}
	st, err := a.store.Budget(ctx, in)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if st.Exhausted != "" {
		l := langOfWriter(w)
		say(w, http.StatusConflict, "BUDGET_STILL_EXHAUSTED", "which", budgetWhich(l, st), "contact", st.Contact.LabelIn(l))
		return
	}
	if _, err := a.store.Resume(ctx, a.store.Pool, run.ID); err != nil {
		a.fail(w, err, "run")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"resumed": true})
}
