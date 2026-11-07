package agentmarket

import (
	"archive/zip"
	"bytes"
	"context"
	"io"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM6 — what sections B and C keep is the person's like the rest: in the
// export, stopped at the deletion request, erased at the purge. The send log
// is the organisation's record of what left and to whom; it stays, and the
// person who approved it is no longer named by id or address (as a sign-off
// is). An install erased whole takes all four with it.

func (e *testEnv) seedAM6(in Install) (reminder, notification, draft, send uuid.UUID) {
	e.t.Helper()
	ctx := context.Background()
	run := uuid.New()
	for _, q := range []struct {
		sql  string
		args []any
	}{
		{`INSERT INTO agent_market.reminders (install_id, tenant_id, user_id, label, rule, next_due_at)
			VALUES ($1, $2, $3, 'ปิดงบ', '{"every":"month","day":5,"time":"09:00"}', now() + interval '1 day') RETURNING id`,
			[]any{in.ID, in.TenantID, in.UserID}},
		{`INSERT INTO agent_market.notifications (tenant_id, user_id, kind, title, body, link, install_id, dedupe_key)
			VALUES ($1, $2, 'reminder_due', 'ถึงเวลาปิดงบ', 'ต้องใช้ 3 ไฟล์', '/chat/agents/i/' || $3, $4, 'k-' || $3) RETURNING id`,
			[]any{in.TenantID, in.UserID, in.ID.String(), in.ID}},
		{`INSERT INTO agent_market.email_drafts (run_id, install_id, tenant_id, user_id, subject, body, body_sha256)
			VALUES ($1, $2, $3, $4, 'ผลตรวจ', 'สรุป', 'abc') RETURNING id`, []any{run, in.ID, in.TenantID, in.UserID}},
	} {
		var id uuid.UUID
		if err := e.store.Pool.QueryRow(ctx, q.sql, q.args...).Scan(&id); err != nil {
			e.t.Fatal(err)
		}
		switch {
		case reminder == uuid.Nil:
			reminder = id
		case notification == uuid.Nil:
			notification = id
		default:
			draft = id
		}
	}
	if err := e.store.Pool.QueryRow(ctx, `INSERT INTO agent_market.email_sends (draft_id, run_id, to_addrs, subject, body_sha256,
		approved_by_user_id, approved_by_name, approved_by_email, status) VALUES ($1, $2, '{"cfo@company.example"}', 'ผลตรวจ', 'abc',
		$3, 'สมหญิง ใจดี', 'somying@company.example', 'sent') RETURNING id`, draft, run, in.UserID).Scan(&send); err != nil {
		e.t.Fatal(err)
	}
	// the draft's run belongs to the install (the send log reaches an erased install through it)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.email_drafts SET run_id = (SELECT id FROM agent_market.agent_runs
		WHERE install_id = $1 ORDER BY created_at LIMIT 1) WHERE id = $2 AND EXISTS (SELECT 1 FROM agent_market.agent_runs WHERE install_id = $1)`,
		in.ID, draft); err != nil {
		e.t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.email_sends SET run_id = d.run_id FROM agent_market.email_drafts d
		WHERE d.id = email_sends.draft_id AND email_sends.id = $1`, send); err != nil {
		e.t.Fatal(err)
	}
	return
}

func (e *testEnv) count(q string, args ...any) int {
	e.t.Helper()
	var n int
	if err := e.store.Pool.QueryRow(context.Background(), q, args...).Scan(&n); err != nil {
		e.t.Fatal(err)
	}
	return n
}

func TestRemindersNotificationsAndMailAreThePersonsLikeTheRest(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	mine := e.install()
	e.seedAgentData(mine)
	rem, note, draft, send := e.seedAM6(mine)
	other := e.user
	other.UserID = uuid.New()
	e.user, other = other, e.user
	theirs := e.install()
	e.user = other
	e.seedAgentData(theirs)
	theirRem, theirNote, _, theirSend := e.seedAM6(theirs)

	p := NewAccountProvider(e.store, e.api.files)
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	if _, err := p.Export(ctx, mine.TenantID, mine.UserID, func(path string, r io.Reader) error {
		w, err := zw.Create(path)
		if err == nil {
			_, err = io.Copy(w, r)
		}
		return err
	}); err != nil {
		t.Fatal(err)
	}
	_ = zw.Close()
	zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	got := map[string]string{}
	for _, f := range zr.File {
		rc, _ := f.Open()
		b, _ := io.ReadAll(rc)
		rc.Close()
		got[f.Name] = string(b)
	}
	for doc, id := range map[string]uuid.UUID{"reminders.json": rem, "notifications.json": note, "email_drafts.json": draft, "email_sends.json": send} {
		if !strings.Contains(got[doc], id.String()) {
			t.Fatalf("the export's %s holds the person's row: %q", doc, got[doc])
		}
	}
	for doc, id := range map[string]uuid.UUID{"reminders.json": theirRem, "notifications.json": theirNote, "email_sends.json": theirSend} {
		if strings.Contains(got[doc], id.String()) {
			t.Fatalf("the export's %s holds someone else's row", doc)
		}
	}

	// the deletion request: no reminder fires any more, nothing is shown any more
	if _, err := p.Stop(ctx, mine.UserID); err != nil {
		t.Fatal(err)
	}
	if n := e.count(`SELECT count(*) FROM agent_market.reminders WHERE id = $1 AND status = 'stopped'`, rem); n != 1 {
		t.Fatal("the person's reminder is stopped at the deletion request")
	}
	if n := e.count(`SELECT count(*) FROM agent_market.notifications WHERE id = $1 AND dismissed_at IS NOT NULL`, note); n != 1 {
		t.Fatal("the person's notification is dismissed at the deletion request")
	}
	if n := e.count(`SELECT count(*) FROM agent_market.reminders WHERE id = $1 AND status = 'active'`, theirRem); n != 1 {
		t.Fatal("someone else's reminder was stopped")
	}

	// the purge
	if _, err := p.Purge(ctx, mine.UserID); err != nil {
		t.Fatal(err)
	}
	for _, q := range []string{`SELECT count(*) FROM agent_market.reminders WHERE user_id = $1`,
		`SELECT count(*) FROM agent_market.notifications WHERE user_id = $1`,
		`SELECT count(*) FROM agent_market.email_drafts WHERE user_id = $1`,
		`SELECT count(*) FROM agent_market.email_sends WHERE approved_by_user_id = $1`} {
		if n := e.count(q, mine.UserID); n != 0 {
			t.Fatalf("%s: %d left", q, n)
		}
	}
	// the send log is the organisation's: kept while the round it belongs to is kept, and then it
	// names nobody by id or address (this install was erased whole, so it went with it)
	if n := e.count(`SELECT count(*) FROM agent_market.email_sends WHERE id = $1
		AND (approved_by_email <> '' OR approved_by_user_id <> '00000000-0000-0000-0000-000000000000')`, send); n != 0 {
		t.Fatal("the send log still names the person who approved it")
	}
	for _, q := range []string{`SELECT count(*) FROM agent_market.reminders WHERE id = $1`,
		`SELECT count(*) FROM agent_market.notifications WHERE id = $1`} {
		id := theirRem
		if strings.Contains(q, "notifications") {
			id = theirNote
		}
		if n := e.count(q, id); n != 1 {
			t.Fatalf("someone else's row was purged: %s", q)
		}
	}
	if n := e.count(`SELECT count(*) FROM agent_market.email_sends WHERE id = $1 AND approved_by_email = 'somying@company.example'`, theirSend); n != 1 {
		t.Fatal("someone else's send log was changed")
	}
}

func TestAnInstallErasedWholeTakesItsRemindersNotificationsAndMail(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	e.seedAgentData(in)
	e.seedAM6(in)
	if _, _, err := e.store.PurgeInstalls(ctx, e.api.files, []uuid.UUID{in.ID}); err != nil {
		t.Fatal(err)
	}
	for _, q := range []string{`SELECT count(*) FROM agent_market.reminders WHERE install_id = $1`,
		`SELECT count(*) FROM agent_market.notifications WHERE install_id = $1`,
		`SELECT count(*) FROM agent_market.email_drafts WHERE install_id = $1`,
		`SELECT count(*) FROM agent_market.email_sends s WHERE s.draft_id IN (SELECT id FROM agent_market.email_drafts WHERE install_id = $1)
			OR s.approved_by_user_id = (SELECT user_id FROM agent_market.agent_installs WHERE id = $1)`} {
		if n := e.count(q, in.ID); n != 0 {
			t.Fatalf("%s: %d left", q, n)
		}
	}
	var sends int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.email_sends WHERE approved_by_user_id = $1`, in.UserID).Scan(&sends)
	if sends != 0 {
		t.Fatalf("the send log of an erased install: %d left", sends)
	}
}
