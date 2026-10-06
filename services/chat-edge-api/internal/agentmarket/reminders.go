package agentmarket

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── Reminders (AM6 section B) ────────────────────────────────────────────
//
// A reminder is a schedule on an install that tells its owner a round is
// waiting for their files. Nothing runs without a person: when a reminder
// falls due, the runner's minute pass opens a round directly in
// awaiting_input with a blocking request_files card listing the agent's
// inputs, says in the thread why the round is there, and adds one
// notification. No sandbox, no engine, no model, no charge — the round starts
// only when the person sends the files on that card (the ordinary
// request_files path). There is no agent principal and no unattended
// execution.
//
// One follow-up three days later if the round still has no file, then
// silence. The next firing closes a round that never got a file (charged
// nothing) and opens the next period's. Snooze moves the firing, or while a
// round waits its one follow-up; skip leaves a period out (and closes its
// waiting round); stop ends firings and follow-ups and leaves the round as it
// is. Every write is guarded (a row lock taken with SKIP LOCKED, the period a
// reminder last fired for, the round's idempotency key, the notification's
// dedupe key), so a restart or a second runner never doubles a round or a
// notification.

// thaiTime is the fixed Thai clock the rules are written in (UTC+7).
var thaiTime = time.FixedZone("ICT", 7*3600)

// ReminderRule is when a reminder falls due, in Thai time:
// {"every":"month","day":1..31,"time":"HH:MM"} or
// {"every":"week","weekday":1..7,"time":"HH:MM"} (1 = Monday). A month
// shorter than the day falls due on its last day (31 → 30 September, 28 or
// 29 February).
//
// AM11: a reminder fires with no screen behind it — the runner's minute pass
// opens its run — so the rule keeps the language of the screen that set or
// last changed it ("lang": "en"; none = Thai, which is every reminder from
// before). The run it opens, that run's card and its notifications are in it.
type ReminderRule struct {
	Every   string `json:"every"`
	Day     int    `json:"day,omitempty"`
	Weekday int    `json:"weekday,omitempty"`
	Time    string `json:"time"`
	Lang    string `json:"lang,omitempty"`
}

// lang is the language the reminder speaks.
func (r ReminderRule) lang() Lang { return ParseLang(r.Lang) }

var clockShape = regexp.MustCompile(`^([01][0-9]|2[0-3]):([0-5][0-9])$`)

var errBadRule = errors.New("agentmarket: a reminder rule outside the contract")

// ParseReminderRule reads a rule and refuses anything the contract does not
// name: another period, a day past the 28th, a field it does not know.
func ParseReminderRule(raw json.RawMessage) (ReminderRule, error) {
	var r ReminderRule
	if len(bytes.TrimSpace(raw)) == 0 {
		return r, errBadRule
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&r); err != nil {
		return ReminderRule{}, errBadRule
	}
	switch r.Every {
	case "month":
		if r.Day < 1 || r.Day > 31 || r.Weekday != 0 {
			return ReminderRule{}, errBadRule
		}
	case "week":
		if r.Weekday < 1 || r.Weekday > 7 || r.Day != 0 {
			return ReminderRule{}, errBadRule
		}
	default:
		return ReminderRule{}, errBadRule
	}
	if !clockShape.MatchString(r.Time) {
		return ReminderRule{}, errBadRule
	}
	// the language is the request's to say (createReminder, putReminder), not the body's: what a client
	// sends back of a rule it was sent is read, and then written over
	switch r.Lang {
	case "", string(Thai), string(English):
		r.Lang = ParseLang(r.Lang).mark()
	default:
		return ReminderRule{}, errBadRule
	}
	return r, nil
}

func (r ReminderRule) clock() (int, int) {
	m := clockShape.FindStringSubmatch(r.Time)
	if m == nil {
		return 9, 0
	}
	var h, mm int
	_, _ = fmt.Sscanf(m[1]+" "+m[2], "%d %d", &h, &mm)
	return h, mm
}

// NextDue is the rule's first occurrence strictly after a moment.
func (r ReminderRule) NextDue(after time.Time) time.Time {
	h, m := r.clock()
	t := after.In(thaiTime)
	switch r.Every {
	case "month":
		c := r.inMonth(t.Year(), t.Month(), h, m)
		if !c.After(after) {
			next := time.Date(t.Year(), t.Month()+1, 1, 0, 0, 0, 0, thaiTime)
			c = r.inMonth(next.Year(), next.Month(), h, m)
		}
		return c
	case "week":
		want := time.Weekday(r.Weekday % 7) // 7 = Sunday
		c := time.Date(t.Year(), t.Month(), t.Day(), h, m, 0, 0, thaiTime)
		for i := 0; i < 8; i++ {
			if c.Weekday() == want && c.After(after) {
				return c
			}
			c = c.AddDate(0, 0, 1)
		}
	}
	return time.Time{}
}

// inMonth is the rule's day in a month: its last day when the month is shorter.
func (r ReminderRule) inMonth(y int, month time.Month, h, m int) time.Time {
	day := r.Day
	if last := time.Date(y, month+1, 0, 0, 0, 0, 0, thaiTime).Day(); day > last {
		day = last
	}
	return time.Date(y, month, day, h, m, 0, 0, thaiTime)
}

// PeriodOf is the period an occurrence is for: closing work is for what has
// ended — a month's is the month before ("2026-09"), a week's the seven days
// ending the day before (their first day, "2026-09-28").
func (r ReminderRule) PeriodOf(due time.Time) string {
	d := due.In(thaiTime)
	if r.Every == "week" {
		return time.Date(d.Year(), d.Month(), d.Day()-7, 0, 0, 0, 0, thaiTime).Format("2006-01-02")
	}
	return time.Date(d.Year(), d.Month()-1, 1, 0, 0, 0, 0, thaiTime).Format("2006-01")
}

// ReminderPeriodLabel is a period as a Thai reader says it: "ก.ย. 2569",
// "28 ก.ย. – 4 ต.ค. 2569".
func ReminderPeriodLabel(period string) string {
	if t, err := time.ParseInLocation("2006-01", period, thaiTime); err == nil && len(period) == 7 {
		return fmt.Sprintf("%s %d", thaiMonthsShort[t.Month()-1], t.Year()+543)
	}
	start, err := time.ParseInLocation("2006-01-02", period, thaiTime)
	if err != nil {
		return ""
	}
	end := start.AddDate(0, 0, 6)
	ms, me := thaiMonthsShort[start.Month()-1], thaiMonthsShort[end.Month()-1]
	switch {
	case start.Year() != end.Year():
		return fmt.Sprintf("%d %s %d – %d %s %d", start.Day(), ms, start.Year()+543, end.Day(), me, end.Year()+543)
	case start.Month() != end.Month():
		return fmt.Sprintf("%d %s – %d %s %d", start.Day(), ms, end.Day(), me, end.Year()+543)
	}
	return fmt.Sprintf("%d–%d %s %d", start.Day(), end.Day(), me, end.Year()+543)
}

// thaiWeekdays are the days of the week as a Thai rule says them, Monday first (their English is
// lang.go's table, not a translation: WeekdayIn, Weekday).
var thaiWeekdays = []string{"จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"}

// Text is the rule as a reader says it (rule_text).
func (r ReminderRule) Text() PersonText {
	if r.Every == "week" {
		return Text("ทุกวัน%s เวลา %s น. · สำหรับงาน 7 วันก่อนหน้า", Weekday(r.Weekday), Typed(r.Time, 5))
	}
	if r.Day > 28 {
		return Text("ทุกวันที่ %s ของเดือน เวลา %s น. (เดือนที่สั้นกว่าใช้วันสุดท้ายของเดือน) · สำหรับงานของเดือนก่อนหน้า",
			Count(r.Day), Typed(r.Time, 5))
	}
	return Text("ทุกวันที่ %s ของเดือน เวลา %s น. · สำหรับงานของเดือนก่อนหน้า", Count(r.Day), Typed(r.Time, 5))
}

// ── the row ──────────────────────────────────────────────────────────────

// Reminder is one reminder on an install.
type Reminder struct {
	ID             uuid.UUID
	InstallID      uuid.UUID
	TenantID       uuid.UUID
	UserID         uuid.UUID
	Label          string
	Rule           ReminderRule
	Status         string
	NextDueAt      time.Time
	SnoozedUntil   *time.Time
	SkippedPeriod  *string
	FiredPeriod    *string
	FiredAt        *time.Time
	WaitingRunID   *uuid.UUID
	FollowupSentAt *time.Time
	CreatedAt      time.Time
	// Waiting: the round it opened still waits for files (read with the row).
	Waiting bool
}

const reminderCols = `m.id, m.install_id, m.tenant_id, m.user_id, m.label, m.rule, m.status, m.next_due_at, m.snoozed_until,
	m.skipped_period, m.fired_period, m.fired_at, m.waiting_run_id, m.followup_sent_at, m.created_at`

// waitingExpr: the reminder's round is still awaiting its files.
const waitingExpr = `COALESCE((SELECT r.status = 'awaiting_input' AND NOT EXISTS (SELECT 1 FROM agent_market.agent_files f WHERE f.run_id = r.id)
	FROM agent_market.agent_runs r WHERE r.id = m.waiting_run_id), false)`

func scanReminder(row pgx.Row) (Reminder, error) {
	var m Reminder
	var rule json.RawMessage
	err := row.Scan(&m.ID, &m.InstallID, &m.TenantID, &m.UserID, &m.Label, &rule, &m.Status, &m.NextDueAt, &m.SnoozedUntil,
		&m.SkippedPeriod, &m.FiredPeriod, &m.FiredAt, &m.WaitingRunID, &m.FollowupSentAt, &m.CreatedAt, &m.Waiting)
	if err == nil {
		_ = json.Unmarshal(rule, &m.Rule)
	}
	return m, err
}

// due is when the reminder next fires: its occurrence, or the end of a snooze.
func (m Reminder) due() time.Time {
	if m.SnoozedUntil != nil && m.SnoozedUntil.After(m.NextDueAt) {
		return *m.SnoozedUntil
	}
	return m.NextDueAt
}

// ReminderView is a reminder as the contract sends it.
type ReminderView struct {
	ID            uuid.UUID    `json:"id"`
	InstallID     uuid.UUID    `json:"install_id"`
	Label         string       `json:"label"`
	Rule          ReminderRule `json:"rule"`
	RuleText      PersonText   `json:"rule_text"`
	Status        string       `json:"status"`
	NextDueAt     time.Time    `json:"next_due_at"`
	NextDueText   PersonText   `json:"next_due_text"`
	SnoozedUntil  *time.Time   `json:"snoozed_until"`
	SkippedPeriod *string      `json:"skipped_period"`
	WaitingRunID  *uuid.UUID   `json:"waiting_run_id"`
	PeriodLabel   *PersonText  `json:"period_label"`
}

// periodLabel is a reminder's period as the list of reminders says it, in the reader's language.
func periodLabel(period string) *PersonText {
	t := InBoth(func(l Lang) string { return ReminderPeriodLabelIn(l, period) })
	return &t
}

func (m Reminder) view() ReminderView {
	v := ReminderView{ID: m.ID, InstallID: m.InstallID, Label: m.Label, Rule: m.Rule, RuleText: m.Rule.Text(), Status: m.Status,
		NextDueAt: m.due(), SnoozedUntil: m.SnoozedUntil, SkippedPeriod: m.SkippedPeriod}
	v.NextDueText = Text("%s", When(v.NextDueAt))
	if m.Status != "active" {
		v.NextDueText = Text("หยุดเตือนไว้ — กด “เตือนต่อ” เพื่อเริ่มเตือนอีกครั้ง")
	}
	switch {
	case m.Waiting && m.FiredPeriod != nil:
		id := *m.WaitingRunID
		v.WaitingRunID, v.PeriodLabel = &id, periodLabel(*m.FiredPeriod)
	case m.Status == "active":
		v.PeriodLabel = periodLabel(m.Rule.PeriodOf(m.NextDueAt))
	}
	return v
}

func (s *Store) reminderOf(ctx context.Context, q Querier, tenant, user, id uuid.UUID, lock bool) (Reminder, error) {
	sql := `SELECT ` + reminderCols + `, ` + waitingExpr + ` FROM agent_market.reminders m WHERE m.id = $1 AND m.tenant_id = $2 AND m.user_id = $3`
	if lock {
		sql += ` FOR UPDATE OF m`
	}
	m, err := scanReminder(q.QueryRow(ctx, sql, id, tenant, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return m, ErrNotFound
	}
	return m, err
}

func (s *Store) remindersOf(ctx context.Context, install uuid.UUID) ([]Reminder, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+reminderCols+`, `+waitingExpr+` FROM agent_market.reminders m
		WHERE m.install_id = $1 ORDER BY m.created_at`, install)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Reminder
	for rows.Next() {
		m, err := scanReminder(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// ── the minute pass ──────────────────────────────────────────────────────

// ReminderPass is what one minute pass did.
type ReminderPass struct {
	Fired, Skipped, Cancelled, FollowUps, Waiting, WaitingFollowUps int
}

// RemindersPass is one minute of the runner's reminders loop: reminders that
// fell due, their one follow-up, and rounds parked on a question. Each
// reminder is handled in a transaction of its own under its row lock (SKIP
// LOCKED: another runner takes another reminder, never the same one).
func (s *Store) RemindersPass(ctx context.Context, now time.Time) (ReminderPass, error) {
	var p ReminderPass
	var errs []error
	for _, step := range []func(context.Context, time.Time, []uuid.UUID, *ReminderPass) (uuid.UUID, bool, error){s.fireOne, s.followUpOne} {
		failed := []uuid.UUID{} // never NULL: "id = ANY(NULL)" is not false
		for i := 0; i < 500; i++ {
			id, did, err := step(ctx, now, failed, &p)
			if err != nil {
				errs = append(errs, err)
				if id == uuid.Nil || ctx.Err() != nil {
					break
				}
				failed = append(failed, id)
				continue
			}
			if !did {
				break
			}
		}
	}
	if err := s.waitingPass(ctx, now, &p); err != nil {
		errs = append(errs, err)
	}
	return p, errors.Join(errs...)
}

func later(a, b time.Time) time.Time {
	if b.After(a) {
		return b
	}
	return a
}

// agentOn: the install's agent is not disabled in its organisation's catalog. A reminder of a
// disabled agent opens nothing and tells nobody (a round of it could not go on); it waits, and
// the first pass after the agent is back takes it.
const agentOn = `NOT EXISTS (SELECT 1 FROM agent_market.catalog_state c
			WHERE c.org_id = i.org_id AND c.slug = i.slug AND c.status = 'disabled')`

// fireOne handles one reminder that fell due. did = there was one.
func (s *Store) fireOne(ctx context.Context, now time.Time, failed []uuid.UUID, p *ReminderPass) (uuid.UUID, bool, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return uuid.Nil, false, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	m, err := scanReminder(tx.QueryRow(ctx, `SELECT `+reminderCols+`, `+waitingExpr+` FROM agent_market.reminders m
		JOIN agent_market.agent_installs i ON i.id = m.install_id AND i.status = 'active' AND `+agentOn+`
		WHERE m.status = 'active' AND GREATEST(m.next_due_at, COALESCE(m.snoozed_until, m.next_due_at)) <= $1 AND NOT (m.id = ANY($2))
		ORDER BY m.next_due_at LIMIT 1 FOR UPDATE OF m SKIP LOCKED`, now, failed))
	if errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, false, nil
	}
	if err != nil {
		return uuid.Nil, false, err
	}
	// AM11: no screen asked for this — what the reminder writes is in the language it was set in
	ctx = WithLang(ctx, m.Rule.lang())
	period := m.Rule.PeriodOf(m.NextDueAt)
	next := m.Rule.NextDue(later(m.NextDueAt, now))
	var fired, skipped, cancelled int
	switch {
	case m.Rule.Every == "":
		// a rule that cannot be read any more fires nothing, ever again
		_, err = tx.Exec(ctx, `UPDATE agent_market.reminders SET status = 'stopped', updated_at = now() WHERE id = $1`, m.ID)
	case m.FiredPeriod != nil && *m.FiredPeriod == period:
		// this period's round is already open (a restart, a clock that went back): only move on
		_, err = tx.Exec(ctx, `UPDATE agent_market.reminders SET next_due_at = $2, snoozed_until = NULL, updated_at = now() WHERE id = $1`, m.ID, next)
	case m.SkippedPeriod != nil && *m.SkippedPeriod == period:
		skipped = 1
		_, err = tx.Exec(ctx, `UPDATE agent_market.reminders SET next_due_at = $2, snoozed_until = NULL, updated_at = now() WHERE id = $1`, m.ID, next)
	default:
		if m.WaitingRunID != nil {
			var closed bool
			closed, err = s.closeWaitingRound(ctx, tx, *m.WaitingRunID, "reminder_next_period",
				Text("มีรอบใหม่ของงวดถัดไปแล้ว รอบของงวด %s ที่ยังไม่ได้รับไฟล์จึงปิดไว้ ไม่คิดเครดิต ส่งไฟล์ของงวดนั้นมาเมื่อไรก็เริ่มรอบใหม่ได้",
					ReminderPeriod(strOr(m.FiredPeriod))))
			if closed {
				cancelled = 1
			}
		}
		var run Run
		if err == nil {
			run, err = s.openReminderRound(ctx, tx, m, period)
		}
		if err == nil {
			fired = 1
			_, err = tx.Exec(ctx, `UPDATE agent_market.reminders SET fired_period = $2, fired_at = $3, waiting_run_id = $4, followup_sent_at = NULL,
				snoozed_until = NULL, skipped_period = NULL, next_due_at = $5, updated_at = now() WHERE id = $1`, m.ID, period, now, run.ID, next)
		}
	}
	if err == nil {
		err = tx.Commit(ctx)
	}
	if err != nil {
		return m.ID, true, fmt.Errorf("agentmarket: reminder %s: %w", m.ID, err)
	}
	p.Fired += fired
	p.Skipped += skipped
	p.Cancelled += cancelled
	return m.ID, true, nil
}

func strOr(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

// closeWaitingRound cancels a round that is still waiting and never got a
// file: charged nothing, its card and progress card closed, the thread told
// why, its notifications no longer news. false = the round had moved on.
func (s *Store) closeWaitingRound(ctx context.Context, q Querier, runID uuid.UUID, reason string, why PersonText) (bool, error) {
	run, err := scanRun(q.QueryRow(ctx, `UPDATE agent_market.agent_runs SET cancel_requested = true, status = 'cancelled', status_reason = $2,
			finished_at = now(), awaiting_ms = awaiting_ms + CASE WHEN awaiting_since IS NOT NULL
				THEN (extract(epoch FROM now() - awaiting_since) * 1000)::bigint ELSE 0 END,
			awaiting_since = NULL, updated_at = now()
		WHERE id = $1 AND status = 'awaiting_input' AND NOT EXISTS (SELECT 1 FROM agent_market.agent_files f WHERE f.run_id = $1)
		RETURNING `+runCols, runID, reason))
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	// a round that delivered nothing is charged nothing (F)
	if err := s.Settle(ctx, q, run.ID); err != nil {
		return false, err
	}
	if _, err := s.SupersedeOpenCards(ctx, q, run.ID, "cancelled"); err != nil {
		return false, err
	}
	if _, err := q.Exec(ctx, `UPDATE agent_market.agent_thread_items SET payload = payload || jsonb_build_object('status', 'cancelled', 'outcome', 'cancelled'),
		rev = nextval('agent_market.thread_rev'), updated_at = now()
		WHERE install_id = $1 AND kind = 'progress' AND payload->>'run_id' = $2`, run.InstallID, run.ID.String()); err != nil {
		return false, err
	}
	// in the language of the run that is closed (AddNotice: a run's notice is in the run's own)
	if err := s.AddNotice(ctx, q, run, Notice{Text: why, Reason: reason}); err != nil {
		return false, err
	}
	_, err = q.Exec(ctx, `UPDATE agent_market.notifications SET dismissed_at = now() WHERE run_id = $1 AND dismissed_at IS NULL`, run.ID)
	return err == nil, err
}

// reminderFacts is what a reminder's round and notifications say of the agent.
type reminderFacts struct {
	in       Install
	def      Definition
	manifest Manifest
}

func (s *Store) factsOf(ctx context.Context, install uuid.UUID) (reminderFacts, error) {
	var f reminderFacts
	var err error
	if f.in, err = s.InstallByID(ctx, install); err != nil {
		return f, err
	}
	if f.def, err = s.DefinitionByID(ctx, f.in.DefinitionID); err != nil {
		return f, err
	}
	f.manifest, err = ParseManifest(f.def.Manifest)
	return f, err
}

// needs are the files the agent's manifest asks for, as the package declares them.
func (f reminderFacts) needs() (all []map[string]any, required []PersonText) {
	for _, in := range f.manifest.Inputs {
		all = append(all, map[string]any{"role": in.Role, "label": in.Label, "why": in.Note, "required": in.Required, "accept": in.Accept})
		if in.Required {
			required = append(required, LabelFromPackage(in.Label, Text("ไฟล์ที่ agent ต้องใช้")))
		}
	}
	return all, required
}

// filesNeeded is a notification's body: "ต้องใช้ 3 ไฟล์นี้: a · b · c".
func (f reminderFacts) filesNeeded() PersonText {
	_, req := f.needs()
	if len(req) == 0 {
		return Text("ส่งไฟล์ของงวดนี้มาได้เลย รอบจะเริ่มเมื่อได้รับไฟล์")
	}
	return Text("ต้องใช้ %s ไฟล์นี้: %s", Count(len(req)), Words(Join(" · ", req...)))
}

func (f reminderFacts) agentName() PersonText {
	name := f.in.Title
	if strings.TrimSpace(name) == "" {
		name = f.def.Name
	}
	return LabelFromPackage(name, Text("agent ของคุณ"))
}

// workspaceLink is the web's path to a round in its thread ("main" = the first thread).
func workspaceLink(install uuid.UUID, thread *uuid.UUID, run uuid.UUID) string {
	t := "main"
	if thread != nil {
		t = thread.String()
	}
	return "/chat/agents/i/" + install.String() + "?thread=" + t + "&run=" + run.String()
}

// openReminderRound opens the period's round, waiting for files. It writes
// rows and nothing else: no step, no sandbox, no model, no charge.
func (s *Store) openReminderRound(ctx context.Context, tx pgx.Tx, m Reminder, period string) (Run, error) {
	f, err := s.factsOf(ctx, m.InstallID)
	if err != nil {
		return Run{}, err
	}
	in := f.in
	// the first thread, unless a round of it is still open or the person deleted
	// it (AM10): then a thread of its own
	var thread *uuid.UUID
	var last string
	var firstGone bool
	_ = tx.QueryRow(ctx, `SELECT first_chat_deleted_at IS NOT NULL FROM agent_market.agent_installs WHERE id = $1`, in.ID).Scan(&firstGone)
	switch err := tx.QueryRow(ctx, `SELECT status FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'run' AND thread_id IS NULL
		ORDER BY created_at DESC LIMIT 1`, in.ID).Scan(&last); {
	case firstGone || (err == nil && !IsTerminal(last)):
		id, _, err := s.CreateThread(ctx, tx, in)
		if err != nil {
			return Run{}, err
		}
		if _, _, err := s.AddItemIn(ctx, tx, in.ID, &id, in.TenantID, in.UserID, nil, GreetingFor(f.manifest), nil); err != nil {
			return Run{}, err
		}
		thread = &id
	case err != nil && !errors.Is(err, pgx.ErrNoRows):
		return Run{}, err
	}
	formats := json.RawMessage(`["docx","pptx"]`)
	if defs, err := s.ListDefaults(ctx, in.ID); err == nil {
		for _, d := range defs {
			if d.Key == "formats" {
				formats = d.Value
			}
		}
	}
	model := in.ModelChoice
	if model == "" {
		model = ModelAuto
	}
	// the round's period is the package's to read from the files: a period
	// guessed from the rule would name the delivered files after the guess
	run, created, err := s.CreateRun(ctx, tx, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: f.def.Version, Checksum: f.def.Checksum, EngineChecksum: f.def.EngineChecksum, BindingVersion: in.BindingVersion,
		Formats: formats, ThreadID: thread, ModelChoice: &model}, "reminder:"+m.ID.String()+":"+period)
	if err != nil || !created {
		return run, err
	}
	if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input', status_reason = 'reminder_waiting_files',
		awaiting_since = now(), updated_at = now() WHERE id = $1`, run.ID); err != nil {
		return run, err
	}
	run.Status = RunAwaitingInput
	// the run is in the reminder's language (CreateRun took it from ctx); so is everything written for it
	l := run.Lang()
	ctx = WithLang(ctx, l)
	label := ReminderPeriod(period)
	all, _ := f.needs()
	req, _ := json.Marshal(all)
	q := Question{Key: "missing_file", Code: "missing_file", Severity: "blocking", Header: T(l, "ขอไฟล์"),
		Question: Text("ถึงเวลา%s %s แล้ว ส่งไฟล์ของงวดนี้มาได้เลย รอบนี้จะเริ่มเมื่อได้รับไฟล์", Name(m.Label), label).In(l),
		Options: []Option{
			{Label: T(l, "รอก่อน จะอัปโหลดให้"), Description: T(l, "รอบนี้รอไฟล์อยู่ และจะเริ่มเมื่อได้รับไฟล์"), Value: json.RawMessage(`"wait"`)},
			{Label: T(l, "ยกเลิกรอบนี้"), Description: T(l, "ปิดรอบนี้ไว้ก่อน ไม่คิดเครดิต"), Value: json.RawMessage(`"cancel"`)},
		},
		Default: json.RawMessage(`"wait"`), DefaultLabel: T(l, "รอก่อน จะอัปโหลดให้"), Request: req}
	qs, _ := json.Marshal([]Question{q})
	card, err := s.CreateCard(ctx, tx, Card{RunID: run.ID, InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Moment: "preflight",
		Kind: "request_files", Blocking: true, Questions: qs})
	if err != nil {
		return run, err
	}
	rid := run.ID
	for _, item := range []ItemPayload{
		Progress{RunID: run.ID.String(), Kind: KindRun, Status: RunAwaitingInput, Outcome: RunAwaitingInput, Reason: "reminder_waiting_files"},
		AgentText{Text: Text("ถึงเวลา%s %s แล้ว — การเตือนที่ตั้งไว้ (%s) เปิดรอบนี้รอไฟล์ไว้ให้ ยังไม่มีอะไรทำงานจนกว่าคุณจะส่งไฟล์ "+
			"รอบนี้ยังไม่ใช้เครดิต", Name(m.Label), label, Words(m.Rule.Text())), RunID: run.ID.String()},
		CardItemPayload(card, NoText, NoText),
	} {
		if _, _, err := s.AddItem(ctx, tx, in.ID, in.TenantID, in.UserID, &rid, item, nil); err != nil {
			return run, err
		}
	}
	rem := m.ID
	_, err = s.addNotification(ctx, tx, notification{TenantID: in.TenantID, UserID: in.UserID, Kind: "reminder_due", Lang: l,
		Title: Text("ถึงเวลา%s %s แล้ว", Name(m.Label), label), Body: f.filesNeeded(),
		Link: workspaceLink(in.ID, thread, run.ID), InstallID: in.ID, RunID: &rid, ReminderID: &rem,
		Dedupe: "reminder_due:" + m.ID.String() + ":" + period})
	return run, err
}

// followUpOne sends the one follow-up of a reminder whose round still has no
// file three days after it fired (or when its snooze ends).
func (s *Store) followUpOne(ctx context.Context, now time.Time, failed []uuid.UUID, p *ReminderPass) (uuid.UUID, bool, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return uuid.Nil, false, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	m, err := scanReminder(tx.QueryRow(ctx, `SELECT `+reminderCols+`, true FROM agent_market.reminders m
		JOIN agent_market.agent_installs i ON i.id = m.install_id AND i.status = 'active' AND `+agentOn+`
		JOIN agent_market.agent_runs r ON r.id = m.waiting_run_id AND r.status = 'awaiting_input'
		WHERE m.status = 'active' AND m.followup_sent_at IS NULL AND m.fired_at IS NOT NULL AND m.fired_period IS NOT NULL
			AND COALESCE(m.snoozed_until, m.fired_at + interval '3 days') <= $1
			AND NOT EXISTS (SELECT 1 FROM agent_market.agent_files f WHERE f.run_id = r.id) AND NOT (m.id = ANY($2))
		ORDER BY m.fired_at LIMIT 1 FOR UPDATE OF m SKIP LOCKED`, now, failed))
	if errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, false, nil
	}
	if err != nil {
		return uuid.Nil, false, err
	}
	sent, err := func() (bool, error) {
		f, err := s.factsOf(ctx, m.InstallID)
		if err != nil {
			return false, err
		}
		run, err := s.RunByID(ctx, *m.WaitingRunID)
		if err != nil {
			return false, err
		}
		rid, rem := run.ID, m.ID
		// AM11: in the language of the run that waits — the one the reminder had when it opened the run,
		// which the first notification was written in and the mail's own lines follow (mail.go)
		ok, err := s.addNotification(ctx, tx, notification{TenantID: m.TenantID, UserID: m.UserID, Kind: "reminder_followup",
			Lang:  run.Lang(),
			Title: Text("ยังรอไฟล์สำหรับ%s %s", Name(m.Label), ReminderPeriod(*m.FiredPeriod)), Body: f.filesNeeded(),
			Link: workspaceLink(m.InstallID, run.ThreadID, run.ID), InstallID: m.InstallID, RunID: &rid, ReminderID: &rem,
			Dedupe: "reminder_followup:" + m.ID.String() + ":" + *m.FiredPeriod})
		if err != nil {
			return false, err
		}
		_, err = tx.Exec(ctx, `UPDATE agent_market.reminders SET followup_sent_at = $2, snoozed_until = NULL, updated_at = now() WHERE id = $1`, m.ID, now)
		return ok, err
	}()
	if err == nil {
		err = tx.Commit(ctx)
	}
	if err != nil {
		return m.ID, true, fmt.Errorf("agentmarket: reminder %s follow-up: %w", m.ID, err)
	}
	if sent {
		p.FollowUps++
	}
	return m.ID, true, nil
}

// ── handlers ─────────────────────────────────────────────────────────────

// reminderNote and reminderReaches are said with every list (the contract's words).
const reminderNote wording = "ไม่มีอะไรทำงานเองโดยไม่มีคน: การเตือนเปิดรอบที่รอไฟล์ไว้ให้ และรอบจะเริ่มเมื่อคุณส่งไฟล์"

var reminderReaches = map[string]bool{"in_app": true, "desktop": true, "browser": true, "email": false}

// reminderMailMessages are the Thai sentences of this card's own errors
// (what happened, what to do next, what did not change).
var reminderMailMessages = map[string]string{
	"BAD_RULE": "วันและเวลาของการเตือนใช้ไม่ได้ — เลือก “ทุกเดือน” วันที่ 1–31 หรือ “ทุกสัปดาห์” วันจันทร์ถึงวันอาทิตย์ " +
		"พร้อมเวลา เช่น 09:00 แล้วบันทึกอีกครั้ง ไม่มีอะไรถูกเปลี่ยน",
	"BAD_LABEL":   "ยังไม่ได้ตั้งชื่อการเตือน (ไม่เกิน 60 ตัวอักษร) — ใส่ชื่อสั้น ๆ เช่น “ปิดงบ” แล้วบันทึกอีกครั้ง ไม่มีอะไรถูกเปลี่ยน",
	"BAD_SNOOZE":  "เลื่อนการเตือนได้ 1 วัน 3 วัน หรือ 7 วัน — เลือกอย่างใดอย่างหนึ่งแล้วกดอีกครั้ง การเตือนยังเป็นเหมือนเดิม",
	"BAD_SURFACE": "คำขอนี้ไม่ได้บอกว่าแสดงบนเว็บหรือบนแอปเดสก์ท็อป — โหลดหน้านี้ใหม่แล้วลองอีกครั้ง ไม่มีอะไรถูกเปลี่ยน",
	"NOT_DELIVERED": "รอบนี้ยังไม่ได้ส่งงาน จึงยังร่างอีเมลไม่ได้ — ร่างอีเมลได้เมื่อรอบเสร็จและส่งไฟล์ให้แล้ว " +
		"ไม่มีอะไรถูกเปลี่ยน",
	"BAD_FIELDS":   "การส่งอีเมลรับเฉพาะผู้รับ สำเนา และการยืนยันของคุณ — โหลดหน้านี้ใหม่แล้วลองอีกครั้ง ยังไม่ได้ส่งอะไร",
	"NOT_APPROVED": "ยังไม่ได้ยืนยันการส่งข้อความนี้ — ตรวจข้อความแล้วกด “ยืนยันและส่ง” ยังไม่ได้ส่งอะไร",
	"NO_RECIPIENT": "ยังไม่ได้ใส่ผู้รับ — พิมพ์อีเมลของผู้รับอย่างน้อยหนึ่งคน แล้วส่งอีกครั้ง ยังไม่ได้ส่งอะไร",
	"BAD_RECIPIENT": "ผู้รับต้องเป็นอีเมลทีละรายการ ไม่มีชื่อหรือเครื่องหมายอื่นปน และรวมผู้รับกับสำเนาได้ไม่เกิน 10 รายการ " +
		"— แก้รายชื่อแล้วส่งอีกครั้ง ยังไม่ได้ส่งอะไร",
	"DRAFT_CHANGED": "ร่างอีเมลนี้ถูกสร้างใหม่หลังจากที่คุณเปิดดู — เปิดร่างล่าสุด ตรวจข้อความอีกครั้งแล้วค่อยส่ง ยังไม่ได้ส่งอะไร",
	// AM7: the organisation connects its own relay (mail.go); until then, a sentence
	"NO_MAIL_CONNECTOR": mailNotSetUp,
	"MAIL_FAILED": "ส่งอีเมลไม่สำเร็จ เพราะเซิร์ฟเวอร์อีเมลขององค์กรไม่รับข้อความนี้ — ลองใหม่อีกครั้ง " +
		"หรือคัดลอกข้อความไปส่งจากอีเมลของคุณ ถ้ายังไม่ได้ให้แจ้งผู้ดูแล ร่างอีเมลยังอยู่ครบ",
	"MAIL_RECIPIENT": "เซิร์ฟเวอร์อีเมลไม่รับผู้รับ {email} — ตรวจอีเมลผู้รับแล้วส่งอีกครั้ง ยังไม่ได้ส่งอะไร",
	// the admin page's (the relay's settings and its test message)
	"MAIL_BAD_FIELDS": "หน้านี้ตั้งได้เฉพาะเซิร์ฟเวอร์ พอร์ต การเข้ารหัส ชื่อผู้ใช้ และอีเมลผู้ส่ง — รหัสผ่านใส่ใน .env ชื่อ " +
		SMTPPasswordEnv + " เท่านั้น ไม่มีอะไรถูกเปลี่ยน",
	"MAIL_NOT_CONFIGURED": "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล — กรอกเซิร์ฟเวอร์ พอร์ต และอีเมลผู้ส่ง แล้วกดบันทึก " +
		"จนกว่าจะตั้งเสร็จ ผู้ใช้ยังคัดลอกร่างอีเมลไปส่งเองได้ตามเดิม",
	"MAIL_NO_PASSWORD": "ยังไม่ได้ใส่รหัสผ่านของเซิร์ฟเวอร์อีเมล — ผู้ดูแลระบบใส่ใน .env ชื่อ " + SMTPPasswordEnv +
		" แล้วเริ่มบริการใหม่ (หรือเว้นชื่อผู้ใช้ว่างถ้าเซิร์ฟเวอร์ไม่ขอรหัสผ่าน)",
	"MAIL_CONNECT": "ติดต่อเซิร์ฟเวอร์อีเมลไม่ได้ — ตรวจชื่อเซิร์ฟเวอร์และพอร์ต และให้แน่ใจว่าเครื่องนี้ออกไปถึงเซิร์ฟเวอร์ได้ ยังไม่ได้ส่งอะไร",
	"MAIL_TLS": "เชื่อมต่อแบบเข้ารหัส (TLS) กับเซิร์ฟเวอร์อีเมลไม่สำเร็จ — ตรวจโหมดการเข้ารหัสกับพอร์ต " +
		"(STARTTLS ใช้ 587 ส่วน TLS ใช้ 465) และใบรับรองของเซิร์ฟเวอร์ ยังไม่ได้ส่งอะไร",
	"MAIL_NO_STARTTLS": "เซิร์ฟเวอร์อีเมลนี้ไม่รองรับ STARTTLS — ถ้าใช้พอร์ต 465 ให้เลือกโหมด TLS ตั้งแต่เริ่ม ยังไม่ได้ส่งอะไร",
	"MAIL_CLEAR_AUTH": "เซิร์ฟเวอร์นี้ใช้ชื่อผู้ใช้และรหัสผ่าน แต่การเชื่อมต่อไม่ได้เข้ารหัส จึงไม่ส่งรหัสผ่านออกไป — " +
		"เปลี่ยนโหมดเป็น STARTTLS หรือ TLS (หรือเว้นชื่อผู้ใช้ว่างถ้าเซิร์ฟเวอร์ไม่ขอรหัสผ่าน) ยังไม่ได้ส่งอะไร",
	"MAIL_AUTH": "เซิร์ฟเวอร์อีเมลไม่รับชื่อผู้ใช้หรือรหัสผ่าน — ตรวจชื่อผู้ใช้ในหน้านี้และรหัสผ่านใน .env (" + SMTPPasswordEnv +
		") ยังไม่ได้ส่งอะไร",
	"MAIL_SENDER": "เซิร์ฟเวอร์อีเมลไม่ยอมให้ส่งในนามอีเมลผู้ส่งที่ตั้งไว้ — ใช้อีเมลผู้ส่งที่บัญชีนี้ส่งได้ ยังไม่ได้ส่งอะไร",
	"MAIL_DATA":   "เซิร์ฟเวอร์อีเมลรับข้อความไม่ครบ — ลองใหม่อีกครั้ง ถ้ายังไม่ได้ให้ตรวจการตั้งค่าของเซิร์ฟเวอร์ ยังไม่ได้ส่งอะไร",
}

// mailSentence is one of the sentences above with its values ({email}), in Thai.
func mailSentence(code string, vars ...string) string { return mailSentenceIn(Thai, code, vars...) }

// mailSentenceIn is mailSentence in l (AM11): the same code, the same {placeholders}, the English of
// reminderMailMessagesEn (reminders_en.go).
func mailSentenceIn(l Lang, code string, vars ...string) string {
	m := reminderMailMessages[code]
	if l == English {
		if e, ok := reminderMailMessagesEn[code]; ok {
			m = e
		}
	}
	for i := 0; i+1 < len(vars); i += 2 {
		m = strings.ReplaceAll(m, "{"+vars[i]+"}", vars[i+1])
	}
	return m
}

// mailText is one of the sentences above as a text that carries both languages (a thread item, a
// payload: whoever writes it picks).
func mailText(code string, vars ...string) PersonText {
	return InBoth(func(l Lang) string { return mailSentenceIn(l, code, vars...) })
}

// sayMail writes one of the sentences above, in the language of the request w answers.
func sayMail(w http.ResponseWriter, status int, code string, vars ...string) {
	writeErr(w, status, code, mailSentenceIn(langOfWriter(w), code, vars...))
}

// reminderMailRoutes mounts sections B and C (inside the allowed group).
func (a *API) reminderMailRoutes(r chi.Router) {
	r.Get("/installs/{id}/reminders", a.listReminders)
	r.Post("/installs/{id}/reminders", a.createReminder)
	r.Put("/reminders/{id}", a.putReminder)
	r.Post("/reminders/{id}/snooze", a.snoozeReminder)
	r.Post("/reminders/{id}/skip", a.skipReminder)
	r.Post("/reminders/{id}/stop", a.stopReminder)
	r.Post("/reminders/{id}/resume", a.resumeReminder)
	r.Delete("/reminders/{id}", a.deleteReminder)
	r.Get("/notifications", a.listNotifications)
	r.Post("/notifications/{id}/shown", a.notificationShown)
	r.Post("/notifications/{id}/dismiss", a.dismissNotification)
	r.Post("/runs/{id}/email-draft", a.emailDraft)
	r.Get("/email-drafts/{id}", a.getEmailDraft)
	r.Post("/email-drafts/{id}/send", a.sendEmail)
}

type reminderReq struct {
	Label *string         `json:"label"`
	Rule  json.RawMessage `json:"rule"`
	Days  int             `json:"days"`
}

func readReminderReq(r *http.Request) (reminderReq, error) {
	var req reminderReq
	b, err := io.ReadAll(io.LimitReader(r.Body, 1<<16))
	if err != nil || len(bytes.TrimSpace(b)) == 0 {
		return req, err
	}
	return req, json.Unmarshal(b, &req)
}

func cleanLabel(s string) (string, bool) {
	s = clean(s, 1000)
	return s, s != "" && len([]rune(s)) <= 60
}

func (a *API) listReminders(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	list, err := a.store.remindersOf(r.Context(), in.ID)
	if err != nil {
		a.fail(w, err, "reminder")
		return
	}
	views := make([]ReminderView, 0, len(list))
	for _, m := range list {
		views = append(views, m.view())
	}
	writeJSON(w, http.StatusOK, map[string]any{"reminders": views, "reaches": reminderReaches, "note": Text(reminderNote)})
}

func (a *API) createReminder(w http.ResponseWriter, r *http.Request) {
	in, c, ok := a.ownedInstall(w, r)
	if !ok || !a.agentGate(w, r, in.Slug, false) {
		return
	}
	req, err := readReminderReq(r)
	if err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	rule, err := ParseReminderRule(req.Rule)
	if err != nil {
		sayMail(w, http.StatusBadRequest, "BAD_RULE")
		return
	}
	label, ok := "", false
	if req.Label != nil {
		label, ok = cleanLabel(*req.Label)
	}
	if !ok {
		sayMail(w, http.StatusBadRequest, "BAD_LABEL")
		return
	}
	// AM11: the reminder speaks the language of the screen that set it
	rule.Lang = RequestLang(r).mark()
	ruleJSON, _ := json.Marshal(rule)
	ctx := r.Context()
	var id uuid.UUID
	if err := a.store.Pool.QueryRow(ctx, `INSERT INTO agent_market.reminders (install_id, tenant_id, user_id, label, rule, next_due_at)
		VALUES ($1, $2, $3, $4, $5, $6) RETURNING id`, in.ID, c.TenantUUID, c.UserID, label, ruleJSON, rule.NextDue(time.Now())).Scan(&id); err != nil {
		a.fail(w, err, "reminder")
		return
	}
	m, err := a.store.reminderOf(ctx, a.store.Pool, c.TenantUUID, c.UserID, id, false)
	if err != nil {
		a.fail(w, err, "reminder")
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"reminder": m.view()})
}

// ownedReminder reads the caller's reminder; somebody else's is not found.
func (a *API) ownedReminder(w http.ResponseWriter, r *http.Request) (Reminder, bool) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return Reminder{}, false
	}
	m, err := a.store.reminderOf(r.Context(), a.store.Pool, c.TenantUUID, c.UserID, id, false)
	if err != nil {
		if errors.Is(err, ErrNotFound) {
			say(w, http.StatusNotFound, "NOT_FOUND", "what", whatIn(langOfWriter(w), "reminder"))
			return m, false
		}
		a.fail(w, err, "reminder")
		return m, false
	}
	return m, true
}

// changed answers with the reminder as it is now.
func (a *API) changed(w http.ResponseWriter, r *http.Request, m Reminder, err error) {
	if err != nil {
		a.fail(w, err, "reminder")
		return
	}
	cur, err := a.store.reminderOf(r.Context(), a.store.Pool, m.TenantID, m.UserID, m.ID, false)
	if err != nil {
		a.fail(w, err, "reminder")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"reminder": cur.view()})
}

func (a *API) putReminder(w http.ResponseWriter, r *http.Request) {
	m, ok := a.ownedReminder(w, r)
	if !ok {
		return
	}
	req, err := readReminderReq(r)
	if err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	label, rule, next := m.Label, m.Rule, m.NextDueAt
	snoozed := m.SnoozedUntil
	if req.Label != nil {
		if label, ok = cleanLabel(*req.Label); !ok {
			sayMail(w, http.StatusBadRequest, "BAD_LABEL")
			return
		}
	}
	if len(bytes.TrimSpace(req.Rule)) > 0 {
		if rule, err = ParseReminderRule(req.Rule); err != nil {
			sayMail(w, http.StatusBadRequest, "BAD_RULE")
			return
		}
		next, snoozed = rule.NextDue(time.Now()), nil
	}
	// AM11: … or that last changed it (its name, its day)
	rule.Lang = RequestLang(r).mark()
	ruleJSON, _ := json.Marshal(rule)
	_, err = a.store.Pool.Exec(r.Context(), `UPDATE agent_market.reminders SET label = $2, rule = $3, next_due_at = $4, snoozed_until = $5,
		updated_at = now() WHERE id = $1`, m.ID, label, ruleJSON, next, snoozed)
	a.changed(w, r, m, err)
}

func (a *API) snoozeReminder(w http.ResponseWriter, r *http.Request) {
	m, ok := a.ownedReminder(w, r)
	if !ok {
		return
	}
	req, err := readReminderReq(r)
	if err != nil || (req.Days != 1 && req.Days != 3 && req.Days != 7) {
		sayMail(w, http.StatusBadRequest, "BAD_SNOOZE")
		return
	}
	// fired and waiting: the one follow-up moves here; not fired: the firing does
	until := time.Now().Add(time.Duration(req.Days) * 24 * time.Hour)
	_, err = a.store.Pool.Exec(r.Context(), `UPDATE agent_market.reminders SET snoozed_until = $2, updated_at = now() WHERE id = $1`, m.ID, until)
	a.changed(w, r, m, err)
}

// skipReminder leaves out the period that is waiting (its round is closed,
// charged nothing, its notifications no longer news), or else the next one.
func (a *API) skipReminder(w http.ResponseWriter, r *http.Request) {
	m, ok := a.ownedReminder(w, r)
	if !ok {
		return
	}
	ctx := r.Context()
	err := func() error {
		tx, err := a.store.Pool.Begin(ctx)
		if err != nil {
			return err
		}
		defer tx.Rollback(ctx) //nolint:errcheck
		cur, err := a.store.reminderOf(ctx, tx, m.TenantID, m.UserID, m.ID, true)
		if err != nil {
			return err
		}
		period := cur.Rule.PeriodOf(cur.NextDueAt)
		if cur.Waiting && cur.FiredPeriod != nil {
			closed, err := a.store.closeWaitingRound(ctx, tx, *cur.WaitingRunID, "reminder_skipped",
				Text("ข้ามงวด %s ตามที่คุณเลือก รอบที่รอไฟล์อยู่จึงปิดไว้ ไม่คิดเครดิต", ReminderPeriod(*cur.FiredPeriod)))
			if err != nil {
				return err
			}
			if closed {
				period = *cur.FiredPeriod
			}
		}
		if _, err := tx.Exec(ctx, `UPDATE agent_market.reminders SET skipped_period = $2, updated_at = now() WHERE id = $1`, m.ID, period); err != nil {
			return err
		}
		return tx.Commit(ctx)
	}()
	a.changed(w, r, m, err)
}

func (a *API) stopReminder(w http.ResponseWriter, r *http.Request) {
	m, ok := a.ownedReminder(w, r)
	if !ok {
		return
	}
	_, err := a.store.Pool.Exec(r.Context(), `UPDATE agent_market.reminders SET status = 'stopped', updated_at = now() WHERE id = $1`, m.ID)
	a.changed(w, r, m, err)
}

func (a *API) resumeReminder(w http.ResponseWriter, r *http.Request) {
	m, ok := a.ownedReminder(w, r)
	if !ok {
		return
	}
	_, err := a.store.Pool.Exec(r.Context(), `UPDATE agent_market.reminders SET status = 'active', next_due_at = $2, snoozed_until = NULL,
		updated_at = now() WHERE id = $1`, m.ID, m.Rule.NextDue(time.Now()))
	a.changed(w, r, m, err)
}

// deleteReminder removes the reminder; the rounds it opened stay.
func (a *API) deleteReminder(w http.ResponseWriter, r *http.Request) {
	m, ok := a.ownedReminder(w, r)
	if !ok {
		return
	}
	if _, err := a.store.Pool.Exec(r.Context(), `DELETE FROM agent_market.reminders WHERE id = $1`, m.ID); err != nil {
		a.fail(w, err, "reminder")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
