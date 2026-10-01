package agentmarket

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"
)

// ── Notifications (AM6 section B) ────────────────────────────────────────
//
// A notification tells a person that something waits for them: a reminder's
// round waiting for files (and its one follow-up), or a round parked on a
// question they have not looked at (and its one follow-up). Each is written
// once — its dedupe key is unique, so a restart or a second runner never
// doubles one — and shown once per surface (the web's bell, the desktop's
// toast). Its title and body are built here from the platform's sentences.
//
// A round parked on a question: awaiting_input on a card that is not a
// request_files card, for more than four hours, on an install its owner has
// not opened since the card was posted, gets one "waiting" notification;
// three days later, if it is still so, one "waiting_followup"; then nothing.

// notification is a row to write. Its texts are PersonTexts.
type notification struct {
	TenantID, UserID uuid.UUID
	Kind             string
	Title, Body      PersonText
	Link             string
	InstallID        uuid.UUID
	RunID            *uuid.UUID
	ReminderID       *uuid.UUID
	CardID           *uuid.UUID
	Dedupe           string
}

// addNotification writes a notification once; false = it was written before.
func (s *Store) addNotification(ctx context.Context, q Querier, n notification) (bool, error) {
	tag, err := q.Exec(ctx, `INSERT INTO agent_market.notifications (tenant_id, user_id, kind, title, body, link, install_id, run_id,
			reminder_id, card_id, dedupe_key)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) ON CONFLICT (dedupe_key) DO NOTHING`,
		n.TenantID, n.UserID, n.Kind, n.Title.String(), n.Body.String(), n.Link, n.InstallID, n.RunID, n.ReminderID, n.CardID, n.Dedupe)
	return err == nil && tag.RowsAffected() == 1, err
}

// Notification is one notification as the contract sends it.
type Notification struct {
	ID         uuid.UUID  `json:"id"`
	Kind       string     `json:"kind"`
	Title      string     `json:"title"`
	Body       string     `json:"body"`
	Link       string     `json:"link"`
	InstallID  uuid.UUID  `json:"install_id"`
	RunID      *uuid.UUID `json:"run_id"`
	ReminderID *uuid.UUID `json:"reminder_id"`
	AgentName  string     `json:"agent_name"`
	CreatedAt  time.Time  `json:"created_at"`
}

// surfaceColumn is the column that records when a notification was first
// shown on a kind of surface (a record, not a filter — AM9).
var surfaceColumn = map[string]string{"web": "shown_web_at", "desktop": "shown_desktop_at"}

// Notifications are the person's notifications that nobody has acted on yet,
// newest first, at most 20. One about a round that no longer waits (its files
// arrived, its question was answered, it was closed) is no longer news.
//
// AM9 — which surface a reminder reaches, decided: every surface the person
// has. The list no longer leaves out what another client showed: before, the
// first page to show a notification stamped "web" for all of them (the
// desktop app's own page included), and the person's browser never saw it.
// Each client remembers what it has raised itself (a browser in its storage,
// the desktop app in a file), and acting on a notification anywhere — open,
// snooze, skip, stop, close — dismisses it, which ends it on every surface.
func (s *Store) Notifications(ctx context.Context, tenant, user uuid.UUID, surface string) ([]Notification, error) {
	if _, ok := surfaceColumn[surface]; !ok {
		return nil, ErrNotFound
	}
	rows, err := s.Pool.Query(ctx, `SELECT n.id, n.kind, n.title, n.body, n.link, n.install_id, n.run_id, n.reminder_id, i.title, n.created_at
		FROM agent_market.notifications n
		JOIN agent_market.agent_installs i ON i.id = n.install_id AND i.status = 'active'
		LEFT JOIN agent_market.agent_runs r ON r.id = n.run_id
		LEFT JOIN agent_market.agent_run_clarifications c ON c.id = n.card_id
		WHERE n.user_id = $1 AND n.tenant_id = $2 AND n.dismissed_at IS NULL
			AND (n.run_id IS NULL OR (r.status = 'awaiting_input'
				AND (n.card_id IS NOT NULL OR NOT EXISTS (SELECT 1 FROM agent_market.agent_files f WHERE f.run_id = r.id))))
			AND (n.card_id IS NULL OR c.status = 'open')
		ORDER BY n.created_at DESC, n.id LIMIT 20`, user, tenant)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Notification{}
	for rows.Next() {
		var n Notification
		var agent string
		if err := rows.Scan(&n.ID, &n.Kind, &n.Title, &n.Body, &n.Link, &n.InstallID, &n.RunID, &n.ReminderID, &agent, &n.CreatedAt); err != nil {
			return nil, err
		}
		n.AgentName = LabelFromPackage(agent, Text("agent ของคุณ")).String()
		out = append(out, n)
	}
	return out, rows.Err()
}

// waitingPass tells the owners of rounds parked on a question, once and then
// once more (the minute pass runs it after the reminders).
func (s *Store) waitingPass(ctx context.Context, now time.Time, p *ReminderPass) error {
	type parked struct {
		card, run, install, tenant, user uuid.UUID
		thread                           *uuid.UUID
		title                            string
		questions                        json.RawMessage
	}
	read := func(sql string) ([]parked, error) {
		rows, err := s.Pool.Query(ctx, sql, now)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		var out []parked
		for rows.Next() {
			var x parked
			if err := rows.Scan(&x.card, &x.run, &x.install, &x.tenant, &x.user, &x.thread, &x.title, &x.questions); err != nil {
				return nil, err
			}
			out = append(out, x)
		}
		return out, rows.Err()
	}
	notify := func(x parked, kind string, title PersonText) (bool, error) {
		var qs []json.RawMessage
		_ = json.Unmarshal(x.questions, &qs)
		n := len(qs)
		if n == 0 {
			n = 1
		}
		card, run := x.card, x.run
		return s.addNotification(ctx, s.Pool, notification{TenantID: x.tenant, UserID: x.user, Kind: kind, Title: title,
			Body: Text("มีคำถาม %s ข้อที่ต้องตอบก่อน agent จะทำต่อ เปิดดูแล้วตอบในการ์ดคำถามได้เลย", Count(n)),
			Link: workspaceLink(x.install, x.thread, x.run), InstallID: x.install, RunID: &run, CardID: &card,
			Dedupe: kind + ":" + x.card.String()})
	}
	name := func(x parked) PersonText { return LabelFromPackage(x.title, Text("agent ของคุณ")) }
	first, err := read(`SELECT c.id, c.run_id, c.install_id, c.tenant_id, c.user_id, r.thread_id, i.title, c.questions
		FROM agent_market.agent_run_clarifications c
		JOIN agent_market.agent_runs r ON r.id = c.run_id AND r.status = 'awaiting_input' AND r.kind = 'run'
		JOIN agent_market.agent_installs i ON i.id = c.install_id AND i.status = 'active' AND ` + agentOn + `
		WHERE c.status = 'open' AND c.blocking AND c.kind <> 'request_files' AND c.created_at <= $1::timestamptz - interval '4 hours'
			AND (i.last_opened_at IS NULL OR i.last_opened_at < c.created_at)
			AND NOT EXISTS (SELECT 1 FROM agent_market.notifications n WHERE n.dedupe_key = 'waiting:' || c.id::text)
		ORDER BY c.created_at LIMIT 200`)
	if err != nil {
		return err
	}
	for _, x := range first {
		ok, err := notify(x, "waiting", Text("%s รอคำตอบจากคุณ", Words(name(x))))
		if err != nil {
			return err
		}
		if ok {
			p.Waiting++
		}
	}
	again, err := read(`SELECT c.id, c.run_id, c.install_id, c.tenant_id, c.user_id, r.thread_id, i.title, c.questions
		FROM agent_market.notifications n
		JOIN agent_market.agent_run_clarifications c ON c.id = n.card_id AND c.status = 'open'
		JOIN agent_market.agent_runs r ON r.id = c.run_id AND r.status = 'awaiting_input'
		JOIN agent_market.agent_installs i ON i.id = c.install_id AND i.status = 'active' AND ` + agentOn + `
		WHERE n.kind = 'waiting' AND n.created_at <= $1::timestamptz - interval '3 days'
			AND (i.last_opened_at IS NULL OR i.last_opened_at < n.created_at)
			AND NOT EXISTS (SELECT 1 FROM agent_market.notifications f WHERE f.dedupe_key = 'waiting_followup:' || c.id::text)
		ORDER BY n.created_at LIMIT 200`)
	if err != nil {
		return err
	}
	for _, x := range again {
		ok, err := notify(x, "waiting_followup", Text("%s ยังรอคำตอบจากคุณ", Words(name(x))))
		if err != nil {
			return err
		}
		if ok {
			p.WaitingFollowUps++
		}
	}
	return nil
}

// ── handlers ─────────────────────────────────────────────────────────────

func (a *API) listNotifications(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	surface := r.URL.Query().Get("surface")
	if _, ok := surfaceColumn[surface]; !ok {
		sayMail(w, http.StatusBadRequest, "BAD_SURFACE")
		return
	}
	list, err := a.store.Notifications(r.Context(), c.TenantUUID, c.UserID, surface)
	if err != nil {
		a.fail(w, err, "notification")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"notifications": list})
}

// markNotification sets one column of the caller's notification (somebody
// else's is not found). Marking twice keeps the first time.
func (a *API) markNotification(w http.ResponseWriter, r *http.Request, col string) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	tag, err := a.store.Pool.Exec(r.Context(), `UPDATE agent_market.notifications SET `+col+` = COALESCE(`+col+`, now())
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`, id, c.TenantUUID, c.UserID)
	if err != nil {
		a.fail(w, err, "notification")
		return
	}
	if tag.RowsAffected() == 0 {
		say(w, http.StatusNotFound, "NOT_FOUND", "what", "การแจ้งเตือน")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (a *API) notificationShown(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Surface string `json:"surface"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 1<<12)).Decode(&req)
	col, ok := surfaceColumn[req.Surface]
	if !ok {
		sayMail(w, http.StatusBadRequest, "BAD_SURFACE")
		return
	}
	a.markNotification(w, r, col)
}

func (a *API) dismissNotification(w http.ResponseWriter, r *http.Request) {
	a.markNotification(w, r, "dismissed_at")
}
