package agentmarket

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net/mail"
	"sort"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM7 phase 5 — the SMTP connector. AM6 built the approved-send path with no
// connector behind it; the organisation connects its own relay, set on the
// admin app's page (the password stays in .env). Every step is proved here
// against a real SMTP conversation with a local sink (smtpsink_test.go):
// only the relay is simulated.

const relaySender = "agents@example.test"

// relayOn points the organisation's relay at the sink (through the admin door, as the page does).
func (e *testEnv) relayOn(s *smtpSink, mode, user string) {
	e.t.Helper()
	e.mustAdmin(uiAdmin, 200, "PUT", "/mail", map[string]any{"host": "127.0.0.1", "port": s.port(), "tls_mode": mode,
		"username": user, "sender": relaySender})
}

// parsed is one message as the sink received it.
type parsed struct {
	hdr         mail.Header
	subject     string
	text        string
	attachments []string
}

func parseSink(t *testing.T, raw string) parsed {
	t.Helper()
	m, err := mail.ReadMessage(strings.NewReader(raw))
	if err != nil {
		t.Fatalf("not a message: %v\n%s", err, raw)
	}
	dec := new(mime.WordDecoder)
	p := parsed{hdr: m.Header}
	p.subject, _ = dec.DecodeHeader(m.Header.Get("Subject"))
	mt, params, _ := mime.ParseMediaType(m.Header.Get("Content-Type"))
	read := func(r io.Reader, enc string) string {
		b, _ := io.ReadAll(r)
		if strings.EqualFold(enc, "base64") {
			out, _ := decodeB64Lines(string(b))
			return out
		}
		return string(b)
	}
	if strings.HasPrefix(mt, "multipart/") {
		mr := multipart.NewReader(m.Body, params["boundary"])
		for {
			part, err := mr.NextRawPart()
			if err != nil {
				break
			}
			disp, dp, _ := mime.ParseMediaType(part.Header.Get("Content-Disposition"))
			if disp == "attachment" {
				name := dp["filename"]
				p.attachments = append(p.attachments, name)
				continue
			}
			p.text += read(part, part.Header.Get("Content-Transfer-Encoding"))
		}
	} else {
		p.text = read(m.Body, m.Header.Get("Content-Transfer-Encoding"))
	}
	return p
}

func TestAM7WithNoRelayEverySendFailsLikeAProduct(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	// the admin page: not set up, says why, never a password
	st := e.mustAdmin(uiAdmin, 200, "GET", "/mail", nil)
	raw, _ := json.Marshal(st)
	if st["ready"] != false || !strings.Contains(st["not_ready"].(string), "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล") ||
		st["password_env"] != SMTPPasswordEnv || strings.Contains(strings.ToLower(string(raw)), `"password":`) {
		t.Fatalf("mail state = %s", raw)
	}
	// the test button
	code, body := e.adminDo(uiAdmin, "POST", "/mail/test", map[string]any{"to": uiAdmin.Email})
	if c, msg := errMsg(body); code != 409 || c != "MAIL_NOT_CONFIGURED" || !strings.Contains(msg, "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล") ||
		!ReadsForPerson(msg) {
		t.Fatalf("test with no relay = %d %s %q", code, c, msg)
	}
	// a person's draft: still there to copy, and it says who can set the relay up
	e.seedDefinition()
	in := e.install()
	run, _ := e.deliveredRound(in, map[string]any{"period": "2026-09", "revenue": "1000.00"})
	d := draftOf(t, e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/email-draft", nil))
	why, _ := d["cannot_send_reason"].(string)
	if d["can_send"] != false || d["body"] == "" || !strings.Contains(why, "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล") ||
		!strings.Contains(why, "ผู้ดูแลตั้งได้ที่") || !strings.Contains(why, "คัดลอก") {
		t.Fatalf("draft with no relay: can_send=%v why=%q", d["can_send"], why)
	}
	code, body = e.do("POST", "/email-drafts/"+d["id"].(string)+"/send", map[string]any{"to": []string{"boss@company.example"},
		"body_sha256": d["body_sha256"], "approve": true})
	c, msg := errMsg(body)
	if code != 409 || c != "NO_MAIL_CONNECTOR" || !strings.Contains(msg, "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล — ผู้ดูแลตั้งได้ที่") ||
		!ReadsForPerson(msg) || looksEnglish(msg) {
		t.Fatalf("send with no relay = %d %s %q", code, c, msg)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.email_sends`); n != 0 {
		t.Fatalf("a send with no relay was logged: %d", n)
	}
}

func TestAM7TheAdminSetsTheRelayAndTheTestMessageArrives(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	sink := newSMTPSink(t)
	// what the page refuses, before anything changes
	for _, bad := range []map[string]any{
		{"host": "127.0.0.1", "port": 0, "tls_mode": "none", "sender": relaySender},
		{"host": "127.0.0.1", "port": 25, "tls_mode": "ssl", "sender": relaySender},
		{"host": "127.0.0.1", "port": 25, "tls_mode": "none", "sender": "Agents <agents@example.test>"},
		{"host": "mail relay.example", "port": 25, "tls_mode": "none", "sender": relaySender},
		{"host": "127.0.0.1\r\nRCPT TO:<x@evil.example>", "port": 25, "tls_mode": "none", "sender": relaySender},
		{"host": "127.0.0.1", "port": 25, "tls_mode": "none", "sender": relaySender, "password": "should-not-be-accepted"},
	} {
		if code, body := e.adminDo(uiAdmin, "PUT", "/mail", bad); code != 400 {
			t.Fatalf("PUT /mail %v = %d %v, want 400", bad, code, body)
		}
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.mail_settings`); n != 0 {
		t.Fatalf("a refused setting was stored: %d", n)
	}
	log.take()
	e.relayOn(sink, "none", "")
	got := log.take()
	for _, f := range []string{"host", "port", "tls_mode", "sender"} {
		if _, ok := find(got, "agent_market.mail."+f); !ok {
			t.Fatalf("the relay's %s changed without an entry: %+v", f, got)
		}
	}
	st := e.mustAdmin(uiAdmin, 200, "GET", "/mail", nil)
	if st["ready"] != true || st["settings"].(map[string]any)["updated_by"] != uiAdmin.Email {
		t.Fatalf("mail state = %v", st)
	}
	out := e.mustAdmin(uiAdmin, 200, "POST", "/mail/test", map[string]any{"to": uiAdmin.Email})
	if out["sent"] != true || out["to"] != uiAdmin.Email {
		t.Fatalf("test = %v", out)
	}
	msgs := sink.messages()
	if len(msgs) != 1 || msgs[0].From != relaySender || strings.Join(msgs[0].Rcpt, ",") != uiAdmin.Email {
		t.Fatalf("the sink received %+v", msgs)
	}
	p := parseSink(t, msgs[0].Data)
	if p.subject != "ข้อความทดสอบจาก Agent Market" || !strings.Contains(p.text, "ทดสอบ") || !strings.Contains(p.text, uiAdmin.Email) {
		t.Fatalf("test message: %q / %q", p.subject, p.text)
	}
	if x, ok := find(log.take(), "agent_market.mail.test"); !ok || x.Details["to"] != uiAdmin.Email {
		t.Fatalf("the test message is not in the log")
	}
	// the relay is an admin's: a publisher reaches none of it
	for _, c := range [][2]string{{"GET", "/mail"}, {"PUT", "/mail"}, {"POST", "/mail/test"}} {
		if code, _ := e.adminDo(uiPublisher, c[0], c[1], map[string]any{"to": uiPublisher.Email}); code != 403 {
			t.Fatalf("publisher %s %s = %d", c[0], c[1], code)
		}
	}
	// a relay that refuses the recipient: a sentence, not a stack trace
	sink.reject = "nobody@example.test"
	code, body := e.adminDo(uiAdmin, "POST", "/mail/test", map[string]any{"to": "nobody@example.test"})
	if c, msg := errMsg(body); code != 502 || c != "MAIL_RECIPIENT" || !strings.Contains(msg, "nobody@example.test") || !ReadsForPerson(msg) {
		t.Fatalf("refused recipient = %d %s %q", code, c, msg)
	}
}

func TestAM7AnApprovedMessageGoesThroughTheRelay(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	sink := newSMTPSink(t)
	sink.startTLS, sink.wantUser, sink.wantPass = true, "relay-user", "relay-pass-not-real"
	e.api.smtp.RootCAs = sink.roots
	e.api.smtp.Password = func() string { return "relay-pass-not-real" }
	e.relayOn(sink, "starttls", "relay-user")
	st := e.mustAdmin(uiAdmin, 200, "GET", "/mail", nil)
	raw, _ := json.Marshal(st)
	if st["ready"] != true || st["password_set"] != true || strings.Contains(string(raw), "relay-pass") {
		t.Fatalf("mail state = %s", raw)
	}

	// composed → approved → sent → received
	e.seedDefinition()
	in := e.install()
	run, arts := e.deliveredRound(in, map[string]any{"period": "2026-09", "revenue": "1250000.00"})
	d := draftOf(t, e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/email-draft", nil))
	if d["can_send"] != true {
		t.Fatalf("a relay is set, the draft cannot send: %v", d["cannot_send_reason"])
	}
	out := e.mustDo(200, "POST", "/email-drafts/"+d["id"].(string)+"/send", map[string]any{"to": []string{"cfo@company.example"},
		"cc": []string{"audit@company.example"}, "body_sha256": d["body_sha256"], "approve": true})
	if out["send"].(map[string]any)["status"] != "sent" {
		t.Fatalf("send = %v", out)
	}
	msgs := sink.messages()
	if len(msgs) != 1 {
		t.Fatalf("the sink received %d messages", len(msgs))
	}
	m := msgs[0]
	if !m.TLS || !m.Authd || m.From != relaySender || strings.Join(m.Rcpt, ",") != "cfo@company.example,audit@company.example" {
		t.Fatalf("envelope tls=%v auth=%v from=%s rcpt=%v", m.TLS, m.Authd, m.From, m.Rcpt)
	}
	p := parseSink(t, m.Data)
	if p.subject != d["subject"] || !strings.Contains(p.text, "1,250,000.00") {
		t.Fatalf("subject %q (draft %q), body %q", p.subject, d["subject"], p.text)
	}
	if p.hdr.Get("Reply-To") != e.user.Email || p.hdr.Get("To") != "cfo@company.example" || p.hdr.Get("Cc") != "audit@company.example" {
		t.Fatalf("headers %v", p.hdr)
	}
	var names []string
	for _, a := range arts {
		names = append(names, a.Name)
	}
	sort.Strings(names)
	sort.Strings(p.attachments)
	if strings.Join(p.attachments, "|") != strings.Join(names, "|") {
		t.Fatalf("attached %v, the round's artifacts %v", p.attachments, names)
	}
	var who uuid.UUID
	var status, errText string
	if err := e.store.Pool.QueryRow(context.Background(), `SELECT approved_by_user_id, status, error FROM agent_market.email_sends
		WHERE draft_id = $1`, d["id"]).Scan(&who, &status, &errText); err != nil || who != e.user.UserID || status != "sent" {
		t.Fatalf("logged: %v %s %v", who, status, err)
	}
}

func TestAM7ThePasswordIsNeverSentInClear(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	sink := newSMTPSink(t) // no STARTTLS offered
	sink.wantUser, sink.wantPass = "relay-user", "relay-pass-not-real"
	e.api.smtp.Password = func() string { return "relay-pass-not-real" }
	e.relayOn(sink, "none", "relay-user")
	code, body := e.adminDo(uiAdmin, "POST", "/mail/test", map[string]any{"to": uiAdmin.Email})
	if c, msg := errMsg(body); code != 502 || c != "MAIL_CLEAR_AUTH" || !strings.Contains(msg, "เข้ารหัส") {
		t.Fatalf("auth over a clear connection = %d %s %q", code, c, msg)
	}
	sink.mu.Lock()
	sent := len(sink.auth)
	sink.mu.Unlock()
	if sent != 0 || len(sink.messages()) != 0 {
		t.Fatalf("the password went over a clear connection (%d) or a message was sent", sent)
	}
	// STARTTLS asked for, the relay does not offer it: nothing goes, the reason is said
	e.relayOn(sink, "starttls", "relay-user")
	code, body = e.adminDo(uiAdmin, "POST", "/mail/test", map[string]any{"to": uiAdmin.Email})
	if c, msg := errMsg(body); code != 502 || c != "MAIL_NO_STARTTLS" || !strings.Contains(msg, "TLS") {
		t.Fatalf("no STARTTLS = %d %s %q", code, c, msg)
	}
	// a username with no password in .env is not "ready"
	e.api.smtp.Password = func() string { return "" }
	st := e.mustAdmin(uiAdmin, 200, "GET", "/mail", nil)
	if st["ready"] != false || st["password_set"] != false || !strings.Contains(st["not_ready"].(string), SMTPPasswordEnv) {
		t.Fatalf("no password: %v", st)
	}
}

func TestAM7NoDocumentSteersAMessageThroughTheRelay(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	sink := newSMTPSink(t)
	e.relayOn(sink, "none", "")
	ctx := context.Background()
	e.seedDefinition()
	in := e.install()
	seller := "บริษัท " + steering[0] + " จำกัด"
	run, arts := e.deliveredRound(in, map[string]any{"period": "2026-09", "period_label": steering[2], "revenue": "1250000.00",
		"gate":         map[string]any{"passed": true, "what": "ลงตัว ผู้ขาย " + seller + " " + steering[1]},
		"not_included": []any{map[string]any{"doc_key": "d1", "name": steering[1], "reason": steering[3] + " " + steering[2]}}})
	rid := run.ID
	if _, err := e.store.CreateFile(ctx, e.store.Pool, File{ID: uuid.New(), TenantID: in.TenantID, UserID: in.UserID, InstallID: in.ID,
		RunID: &rid, Name: steering[1], Size: 10, SHA256: "x", StorageKey: "agent-market/x", Status: "active"}); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_output_artifacts SET name = $2 WHERE id = $1`, arts[0].ID,
		steering[3]+" "+steering[2]+" "+steering[0]+".docx"); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET title = $2 WHERE id = $1`, in.ID, "รายงานการเงิน "+steering[0]); err != nil {
		t.Fatal(err)
	}
	d := draftOf(t, e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/email-draft", nil))
	e.mustDo(200, "POST", "/email-drafts/"+d["id"].(string)+"/send", map[string]any{"to": []string{"owner@company.example"},
		"body_sha256": d["body_sha256"], "approve": true})
	msgs := sink.messages()
	if len(msgs) != 1 {
		t.Fatalf("messages: %d", len(msgs))
	}
	m := msgs[0]
	if strings.Join(m.Rcpt, ",") != "owner@company.example" {
		t.Fatalf("RCPT TO %v — a document chose a recipient", m.Rcpt)
	}
	t.Logf("ASSERT the relay was asked to deliver to exactly what the person typed: %v", m.Rcpt)
	p := parseSink(t, m.Data)
	for _, h := range []string{"To", "Cc", "Bcc", "Reply-To"} {
		if v := p.hdr.Get(h); strings.Contains(v, "evil") {
			t.Fatalf("%s: %q", h, v)
		}
	}
	for _, bad := range []string{"evil.example", "http", "mailto", "www.", "C:\\", "secret.xlsx"} {
		if strings.Contains(p.subject+"\n"+p.text+"\n"+strings.Join(p.attachments, "\n"), bad) {
			t.Fatalf("the message carries %q:\n%s\n%s\n%v", bad, p.subject, p.text, p.attachments)
		}
		t.Logf("ASSERT the delivered message (subject, body, attachment names) has no %q", bad)
	}
	if len(p.attachments) != len(arts) {
		t.Fatalf("attachments %v for %d artifacts", p.attachments, len(arts))
	}
	t.Logf("ASSERT the delivered attachments are the round's %d artifacts: %v", len(arts), p.attachments)
}

func TestAM7RemindersRideTheRelayOnce(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	// the person as the chat knows them
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO identity.tenants (id, name, slug) VALUES ($1, 'am7', $2)`,
		e.user.TenantUUID, "am7-"+e.user.TenantUUID.String()[:8]); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO identity.users (id, tenant_id, email, name, password_hash)
		VALUES ($1, $2, $3, 'AM7 Test', 'x')`, e.user.UserID, e.user.TenantUUID, e.user.Email); err != nil {
		t.Fatal(err)
	}
	e.seedDefinition()
	in := e.install()
	note := func(key string) {
		if _, err := e.store.addNotification(ctx, e.store.Pool, notification{TenantID: in.TenantID, UserID: in.UserID, Kind: "reminder_due",
			Title: Text("ถึงเวลาปิดงบเดือน ก.ย. 2569"), Body: Text("ต้องใช้ 1 ไฟล์นี้: GL"), Link: workspaceLink(in.ID, nil, uuid.New()),
			InstallID: in.ID, Dedupe: key}); err != nil {
			t.Fatal(err)
		}
	}
	mailer := e.api.smtp
	mailer.WebURL = "https://chat.example.test/chat"

	// no relay: in-app and tray as now, and never by e-mail later
	note("am7-1")
	if p, err := mailer.NotificationsPass(ctx); err != nil || p.Skipped != 1 || p.Sent != 0 {
		t.Fatalf("no relay: %+v %v", p, err)
	}
	sink := newSMTPSink(t)
	e.relayOn(sink, "none", "")
	if p, _ := mailer.NotificationsPass(ctx); p.Sent != 0 || len(sink.messages()) != 0 {
		t.Fatalf("a notification written before the relay was e-mailed later: %+v", p)
	}
	// with a relay: the person gets it once
	note("am7-2")
	if p, err := mailer.NotificationsPass(ctx); err != nil || p.Sent != 1 {
		t.Fatalf("with a relay: %+v %v", p, err)
	}
	if p, _ := mailer.NotificationsPass(ctx); p.Sent != 0 {
		t.Fatalf("sent twice: %+v", p)
	}
	msgs := sink.messages()
	if len(msgs) != 1 || strings.Join(msgs[0].Rcpt, ",") != e.user.Email {
		t.Fatalf("reminder went to %+v", msgs)
	}
	p := parseSink(t, msgs[0].Data)
	if p.subject != "ถึงเวลาปิดงบเดือน ก.ย. 2569" || !strings.Contains(p.text, "ต้องใช้ 1 ไฟล์นี้: GL") ||
		!strings.Contains(p.text, "https://chat.example.test/chat/agents/") {
		t.Fatalf("reminder: %q / %q", p.subject, p.text)
	}
	var status string
	_ = e.store.Pool.QueryRow(ctx, `SELECT email_status FROM agent_market.notifications WHERE dedupe_key = 'am7-2'`).Scan(&status)
	if status != "sent" {
		t.Fatalf("email_status = %q", status)
	}
}

// decodeB64Lines decodes a base64 body split over lines.
func decodeB64Lines(s string) (string, error) {
	b, err := base64.StdEncoding.DecodeString(strings.Join(strings.Fields(s), ""))
	return string(b), err
}

func TestAM7Migration006DownUndoesAll(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	sink := newSMTPSink(t)
	e.relayOn(sink, "none", "")
	down, err := migrationFS.ReadFile("migrations/006_am7.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatalf("down: %v", err)
	}
	tables := e.countRows(`SELECT count(*) FROM information_schema.tables WHERE table_schema = 'agent_market' AND table_name = 'mail_settings'`)
	cols := e.countRows(`SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market'
		AND table_name = 'notifications' AND column_name IN ('email_status', 'emailed_at')`)
	rec := e.countRows(`SELECT count(*) FROM agent_market.schema_migrations WHERE version = '006_am7'`)
	if tables != 0 || cols != 0 || rec != 0 {
		t.Fatalf("down leaves table %d, columns %d, record %d", tables, cols, rec)
	}
	did, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(did) != 1 || did[0] != "006_am7" {
		t.Fatalf("up again: %v %v", did, err)
	}
}
