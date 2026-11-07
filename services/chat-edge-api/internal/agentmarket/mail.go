package agentmarket

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"mime"
	"mime/multipart"
	"net"
	"net/http"
	"net/smtp"
	"net/textproto"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── The mail relay (AM7) ─────────────────────────────────────────────────
//
// AM6 built the approved-send path with no connector behind it. The
// organisation connects its own relay: an admin keeps its host, port, TLS
// mode, username and sender address on the admin app's Agent Market page
// (agent_market.mail_settings). The password lives in .env
// (AGENT_MARKET_SMTP_PASSWORD): it is read when a message goes and is never
// stored, rendered, logged or sent over a connection that is not encrypted.
//
// Until a relay is set up — the state every organisation starts in — every
// send says so in a sentence ("ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล — ผู้ดูแลตั้งได้ที่…")
// and the draft stays there to copy, as in AM6.
//
// One connector carries three things, and nothing else: a message a person
// approved (email.go — to whom they typed, the round's own files), the
// admin's test message, and a person's own reminders (NotificationsPass).
// Nothing is sent in bulk and no document chooses a recipient, a link or an
// attachment.

// SMTPPasswordEnv holds the relay's password (only ever in .env).
const SMTPPasswordEnv = "AGENT_MARKET_SMTP_PASSWORD"

// TLS modes of the relay.
const (
	MailSTARTTLS = "starttls" // plain connection upgraded with STARTTLS (587)
	MailTLS      = "tls"      // TLS from the first byte (465)
	MailPlain    = "none"     // no encryption: a relay inside the organisation's own network
)

// MailSettings is the organisation's relay as an admin set it.
type MailSettings struct {
	Host      string     `json:"host"`
	Port      int        `json:"port"`
	TLSMode   string     `json:"tls_mode"`
	Username  string     `json:"username"`
	Sender    string     `json:"sender"`
	UpdatedBy string     `json:"updated_by"`
	UpdatedAt *time.Time `json:"updated_at"`
}

func defaultMailSettings() MailSettings { return MailSettings{Port: 587, TLSMode: MailSTARTTLS} }

// mailSettingsQ reads the organisation's relay on q (the defaults when none is set).
func (s *Store) mailSettingsQ(ctx context.Context, q Querier, org string, lock bool) (MailSettings, error) {
	m := defaultMailSettings()
	sql := `SELECT host, port, tls_mode, username, sender, updated_by, updated_at FROM agent_market.mail_settings WHERE org_id = $1`
	if lock {
		sql += ` FOR UPDATE`
	}
	var at time.Time
	err := q.QueryRow(ctx, sql, orgOr(org)).Scan(&m.Host, &m.Port, &m.TLSMode, &m.Username, &m.Sender, &m.UpdatedBy, &at)
	if errors.Is(err, pgx.ErrNoRows) {
		return m, nil
	}
	if err != nil {
		return m, err
	}
	m.UpdatedAt = &at
	return m, nil
}

// SMTPMailer sends through the organisation's relay. It is the MailConnector
// of the approved-send path, and the reminders' relay.
type SMTPMailer struct {
	store *Store
	// Password reads AGENT_MARKET_SMTP_PASSWORD when a message goes.
	Password func() string
	// RootCAs trusts a relay's certificate (nil = the system's pool).
	RootCAs *x509.CertPool
	// WebURL is where a reminder's link points (AGENT_MARKET_WEB_URL: the
	// chat's public address; its origin is kept, its path replaced).
	WebURL string
	Log    *slog.Logger
}

// NewSMTPMailer is the relay of the store's organisations, its password from the environment.
func NewSMTPMailer(store *Store, log *slog.Logger) *SMTPMailer {
	return &SMTPMailer{store: store, Password: func() string { return os.Getenv(SMTPPasswordEnv) },
		WebURL: os.Getenv("AGENT_MARKET_WEB_URL"), Log: log}
}

func (m *SMTPMailer) password() string {
	if m == nil || m.Password == nil {
		return ""
	}
	return m.Password()
}

func (m *SMTPMailer) logger() *slog.Logger {
	if m.Log != nil {
		return m.Log
	}
	return slog.Default()
}

// mailNotSetUp is what a person reads while the organisation has no relay
// (the draft's "cannot send" line and NO_MAIL_CONNECTOR).
const mailNotSetUp = "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล — ผู้ดูแลตั้งได้ที่แอปผู้ดูแลระบบ หน้า Agent Market แท็บ “อีเมล” " +
	"ระหว่างนี้คัดลอกข้อความนี้ไปส่งจากอีเมลของคุณได้"

// notReady says why a message cannot go through this relay, as a code of
// reminderMailMessages ("" = it can).
func (m *SMTPMailer) notReady(set MailSettings) string {
	switch {
	case set.Host == "" || set.Sender == "":
		return "MAIL_NOT_CONFIGURED"
	case set.Username != "" && m.password() == "":
		return "MAIL_NO_PASSWORD"
	}
	return ""
}

// ready: a message can go now for this organisation.
func (m *SMTPMailer) ready(ctx context.Context, org string) bool {
	if m == nil || m.store == nil {
		return false
	}
	set, err := m.store.mailSettingsQ(ctx, m.store.Pool, org, false)
	return err == nil && m.notReady(set) == ""
}

// Send delivers one message through the relay of the message's organisation.
func (m *SMTPMailer) Send(ctx context.Context, msg MailMessage) error {
	set, err := m.store.mailSettingsQ(ctx, m.store.Pool, msg.Org, false)
	if err != nil {
		return err
	}
	if m.notReady(set) != "" {
		return &mailError{stage: "not_configured", err: errors.New("the relay is not set up")}
	}
	rcpts := append(append([]string{}, msg.To...), msg.Cc...)
	return m.deliver(ctx, set, rcpts, buildMessage(set.Sender, msg, time.Now()))
}

// mailError is where a delivery stopped (what a person reads is mailFailure's).
type mailError struct {
	stage string // not_configured | connect | tls | no_starttls | clear_auth | auth | sender | recipient | data
	addr  string // the recipient the relay refused
	err   error
}

func (e *mailError) Error() string {
	if e.addr != "" {
		return "smtp " + e.stage + " <" + e.addr + ">: " + e.err.Error()
	}
	return "smtp " + e.stage + ": " + e.err.Error()
}

func (e *mailError) Unwrap() error { return e.err }

// mailFailure is the sentence (a code of reminderMailMessages and its
// values) about a delivery that did not happen: for the admin who pressed
// the test button, or for the person who approved a message (who then still
// has the draft to copy).
func mailFailure(err error, forAdmin bool) (string, []string) {
	var me *mailError
	if !errors.As(err, &me) {
		return "MAIL_FAILED", nil
	}
	if me.stage == "recipient" {
		return "MAIL_RECIPIENT", []string{"email", me.addr}
	}
	if !forAdmin {
		if me.stage == "not_configured" {
			return "NO_MAIL_CONNECTOR", nil
		}
		return "MAIL_FAILED", nil
	}
	switch me.stage {
	case "not_configured":
		return "MAIL_NOT_CONFIGURED", nil
	case "connect":
		return "MAIL_CONNECT", nil
	case "tls":
		return "MAIL_TLS", nil
	case "no_starttls":
		return "MAIL_NO_STARTTLS", nil
	case "clear_auth":
		return "MAIL_CLEAR_AUTH", nil
	case "auth":
		return "MAIL_AUTH", nil
	case "sender":
		return "MAIL_SENDER", nil
	}
	return "MAIL_DATA", nil
}

// deliver speaks SMTP to the relay: connect, encrypt as set, authenticate
// (only over an encrypted connection), then one message to its recipients.
func (m *SMTPMailer) deliver(ctx context.Context, set MailSettings, rcpts []string, data []byte) error {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	addr := net.JoinHostPort(set.Host, strconv.Itoa(set.Port))
	tlsCfg := &tls.Config{ServerName: set.Host, MinVersion: tls.VersionTLS12, RootCAs: m.RootCAs}
	dialer := &net.Dialer{Timeout: 15 * time.Second}
	var conn net.Conn
	var err error
	if set.TLSMode == MailTLS {
		conn, err = (&tls.Dialer{NetDialer: dialer, Config: tlsCfg}).DialContext(ctx, "tcp", addr)
		if err != nil {
			if isTLSFailure(err) {
				return &mailError{stage: "tls", err: err}
			}
			return &mailError{stage: "connect", err: err}
		}
	} else if conn, err = dialer.DialContext(ctx, "tcp", addr); err != nil {
		return &mailError{stage: "connect", err: err}
	}
	if dl, ok := ctx.Deadline(); ok {
		_ = conn.SetDeadline(dl)
	}
	c, err := smtp.NewClient(conn, set.Host)
	if err != nil {
		_ = conn.Close()
		return &mailError{stage: "connect", err: err}
	}
	defer c.Close()
	if err := c.Hello(helloName(set.Sender)); err != nil {
		return &mailError{stage: "connect", err: err}
	}
	if set.TLSMode == MailSTARTTLS {
		if ok, _ := c.Extension("STARTTLS"); !ok {
			return &mailError{stage: "no_starttls", err: errors.New("the relay offers no STARTTLS")}
		}
		if err := c.StartTLS(tlsCfg); err != nil {
			return &mailError{stage: "tls", err: err}
		}
	}
	if set.Username != "" {
		// never a password over a connection that is not encrypted (net/smtp
		// would still allow it to a relay on this machine)
		if _, secure := c.TLSConnectionState(); !secure {
			return &mailError{stage: "clear_auth", err: errors.New("refusing to send a password over a clear connection")}
		}
		if ok, _ := c.Extension("AUTH"); !ok {
			return &mailError{stage: "auth", err: errors.New("the relay offers no AUTH")}
		}
		if err := c.Auth(smtp.PlainAuth("", set.Username, m.password(), set.Host)); err != nil {
			return &mailError{stage: "auth", err: err}
		}
	}
	if err := c.Mail(set.Sender); err != nil {
		return &mailError{stage: "sender", err: err}
	}
	for _, r := range rcpts {
		if err := c.Rcpt(r); err != nil {
			return &mailError{stage: "recipient", addr: r, err: err}
		}
	}
	w, err := c.Data()
	if err != nil {
		return &mailError{stage: "data", err: err}
	}
	if _, err := w.Write(data); err != nil {
		return &mailError{stage: "data", err: err}
	}
	if err := w.Close(); err != nil {
		return &mailError{stage: "data", err: err}
	}
	_ = c.Quit()
	return nil
}

func isTLSFailure(err error) bool {
	var rh tls.RecordHeaderError
	var ua x509.UnknownAuthorityError
	var hn x509.HostnameError
	var ci x509.CertificateInvalidError
	var ve *tls.CertificateVerificationError
	return errors.As(err, &rh) || errors.As(err, &ua) || errors.As(err, &hn) || errors.As(err, &ci) || errors.As(err, &ve) ||
		strings.Contains(err.Error(), "tls:")
}

// helloName is the name the connector gives itself: the sender's domain.
func helloName(sender string) string {
	if i := strings.LastIndexByte(sender, '@'); i >= 0 && i < len(sender)-1 {
		return sender[i+1:]
	}
	return "localhost"
}

// oneLine keeps a header value on its line.
func oneLine(s string) string {
	return strings.Join(strings.Fields(strings.NewReplacer("\r", " ", "\n", " ").Replace(s)), " ")
}

// base64Lines is data in base64, 76 characters a line.
func base64Lines(b []byte) string {
	enc := base64.StdEncoding.EncodeToString(b)
	var out strings.Builder
	for len(enc) > 76 {
		out.WriteString(enc[:76])
		out.WriteString("\r\n")
		enc = enc[76:]
	}
	out.WriteString(enc)
	out.WriteString("\r\n")
	return out.String()
}

// buildMessage is the message as it goes on the wire: the headers from what
// the connector and the person decided, the text, the files.
func buildMessage(from string, msg MailMessage, now time.Time) []byte {
	var b bytes.Buffer
	h := func(k, v string) { b.WriteString(k + ": " + v + "\r\n") }
	h("From", from)
	h("To", strings.Join(msg.To, ", "))
	if len(msg.Cc) > 0 {
		h("Cc", strings.Join(msg.Cc, ", "))
	}
	if msg.ReplyTo != "" && plainAddress.MatchString(msg.ReplyTo) {
		h("Reply-To", msg.ReplyTo)
	}
	h("Subject", mime.BEncoding.Encode("UTF-8", oneLine(msg.Subject)))
	h("Date", now.Format(time.RFC1123Z))
	h("Message-ID", "<"+uuid.NewString()+"@"+helloName(from)+">")
	h("MIME-Version", "1.0")
	text := []byte(strings.ReplaceAll(strings.ReplaceAll(msg.Body, "\r\n", "\n"), "\n", "\r\n"))
	if len(msg.Attachments) == 0 {
		h("Content-Type", "text/plain; charset=UTF-8")
		h("Content-Transfer-Encoding", "base64")
		b.WriteString("\r\n")
		b.WriteString(base64Lines(text))
		return b.Bytes()
	}
	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	h("Content-Type", "multipart/mixed; boundary=\""+mw.Boundary()+"\"")
	b.WriteString("\r\n")
	part, _ := mw.CreatePart(textproto.MIMEHeader{
		"Content-Type":              {"text/plain; charset=UTF-8"},
		"Content-Transfer-Encoding": {"base64"},
	})
	_, _ = io.WriteString(part, base64Lines(text))
	for _, a := range msg.Attachments {
		name := oneLine(strings.NewReplacer(`"`, "", `\`, "").Replace(a.Name))
		ct := a.ContentType
		if ct == "" {
			ct = "application/octet-stream"
		}
		ctype := mime.FormatMediaType(ct, map[string]string{"name": name})
		if ctype == "" {
			ctype = "application/octet-stream"
		}
		disp := mime.FormatMediaType("attachment", map[string]string{"filename": name})
		if disp == "" {
			disp = "attachment"
		}
		p, _ := mw.CreatePart(textproto.MIMEHeader{
			"Content-Type":              {ctype},
			"Content-Disposition":       {disp},
			"Content-Transfer-Encoding": {"base64"},
		})
		_, _ = io.WriteString(p, base64Lines(a.Data))
	}
	_ = mw.Close()
	b.Write(body.Bytes())
	return b.Bytes()
}

// ── the admin page's relay settings ──────────────────────────────────────

var mailHost = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$`)

// mailState is what the admin page shows of the relay: never the password,
// only whether .env holds one.
func (a *API) mailState(ctx context.Context, org string) (map[string]any, error) {
	set, err := a.store.mailSettingsQ(ctx, a.store.Pool, org, false)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"settings": set, "password_set": a.smtp.password() != "", "password_env": SMTPPasswordEnv}
	why := a.smtp.notReady(set)
	out["ready"] = why == ""
	if why != "" {
		out["not_ready"] = mailText(why)
	}
	return out, nil
}

func (a *API) adminMail(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	st, err := a.mailState(r.Context(), accessOf(r).OrgID)
	if err != nil {
		a.fail(w, err, "mail")
		return
	}
	writeJSON(w, http.StatusOK, st)
}

// mailLabels name each relay setting for a reader of the audit log (an entry
// is written once, in the language of the admin's request that made it).
var mailLabels = []struct {
	key   string
	label wording
}{
	{"host", "เซิร์ฟเวอร์อีเมล"}, {"port", "พอร์ต"}, {"tls_mode", "การเข้ารหัส"},
	{"username", "ชื่อผู้ใช้ของเซิร์ฟเวอร์อีเมล"}, {"sender", "อีเมลผู้ส่ง"},
}

func (a *API) adminPutMail(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	c, _ := caller(r)
	acc := accessOf(r)
	var body struct {
		Host     string `json:"host"`
		Port     int    `json:"port"`
		TLSMode  string `json:"tls_mode"`
		Username string `json:"username"`
		Sender   string `json:"sender"`
	}
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<14))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		if strings.Contains(err.Error(), "unknown field") {
			sayMail(w, http.StatusBadRequest, "MAIL_BAD_FIELDS")
		} else {
			badRequest(w, "BAD_JSON")
		}
		return
	}
	set := MailSettings{Host: strings.TrimSpace(body.Host), Port: body.Port, TLSMode: strings.TrimSpace(body.TLSMode),
		Username: strings.TrimSpace(body.Username), Sender: strings.TrimSpace(body.Sender)}
	switch {
	case set.Host != "" && (len(set.Host) > 253 || !mailHost.MatchString(set.Host)):
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", forReq(w, "ชื่อเซิร์ฟเวอร์อีเมลต้องเป็นชื่อโดเมน เช่น smtp.example.com หรือเลขที่อยู่ของเครื่อง"))
		return
	case set.Port < 1 || set.Port > 65535:
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", forReq(w, "พอร์ตต้องเป็นตัวเลข 1–65535 (ส่วนใหญ่ใช้ 587 หรือ 465)"))
		return
	case set.TLSMode != MailSTARTTLS && set.TLSMode != MailTLS && set.TLSMode != MailPlain:
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", forReq(w, "การเข้ารหัสต้องเป็น STARTTLS หรือ TLS หรือไม่เข้ารหัส"))
		return
	case len(set.Username) > 254 || strings.ContainsAny(set.Username, " \t\r\n"):
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", forReq(w, "ชื่อผู้ใช้ของเซิร์ฟเวอร์อีเมลต้องไม่มีช่องว่าง"))
		return
	case set.Sender != "" && (len(set.Sender) > 254 || !plainAddress.MatchString(set.Sender)):
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", forReq(w, "อีเมลผู้ส่งต้องเป็นอีเมลเดียว ไม่มีชื่อหรือเครื่องหมายอื่นปน"))
		return
	case set.Host != "" && set.Sender == "":
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", forReq(w, "ใส่อีเมลผู้ส่งด้วย — เซิร์ฟเวอร์ต้องรู้ว่าส่งในนามอีเมลใด"))
		return
	}
	ctx := r.Context()
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		if _, err := a.store.orgSettingsQ(ctx, tx, acc.OrgID, false); err != nil {
			return nil, err
		}
		before, err := a.store.mailSettingsQ(ctx, tx, acc.OrgID, true)
		if err != nil {
			return nil, err
		}
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.mail_settings (org_id, host, port, tls_mode, username, sender, updated_by)
			VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (org_id) DO UPDATE SET host = EXCLUDED.host, port = EXCLUDED.port,
			tls_mode = EXCLUDED.tls_mode, username = EXCLUDED.username, sender = EXCLUDED.sender, updated_by = EXCLUDED.updated_by,
			updated_at = now()`, orgOr(acc.OrgID), set.Host, set.Port, set.TLSMode, set.Username, set.Sender, normEmail(c.Email)); err != nil {
			return nil, err
		}
		b, n := wireOf(before), wireOf(set)
		var out []AuditEntry
		for _, f := range mailLabels {
			if jsonText(b[f.key]) == jsonText(n[f.key]) {
				continue
			}
			out = append(out, entry(r, "agent_market.mail."+f.key, "agent_market_mail", orgOr(acc.OrgID),
				map[string]any{"setting": f.key, "label": T(LangOf(r.Context()), f.label), "from": b[f.key], "to": n[f.key]}))
		}
		return out, nil
	})
	if err != nil {
		a.failChange(w, r, err, "mail")
		return
	}
	st, err := a.mailState(ctx, acc.OrgID)
	if err != nil {
		a.fail(w, err, "mail")
		return
	}
	writeJSON(w, http.StatusOK, st)
}

// testMessage is the admin's test message, in l — the language of the admin's own screen.
func testMessage(l Lang, set MailSettings, to, by string, now time.Time) MailMessage {
	return MailMessage{To: []string{to}, Subject: T(l, "ข้อความทดสอบจาก Agent Market"),
		Body: T(l, "ข้อความนี้ส่งจากหน้า Agent Market ในแอปผู้ดูแลระบบ เพื่อทดสอบเซิร์ฟเวอร์อีเมลขององค์กร\n"+
			"ถ้าคุณได้รับข้อความนี้ แปลว่าการตั้งค่าใช้ได้แล้ว ไม่ต้องตอบกลับ\n\n"+
			"ส่งโดย: ") + by + T(l, "\nเวลา: ") + DateTimeIn(l, now) + T(l, "\nเซิร์ฟเวอร์: ") + set.Host + T(l, " พอร์ต ") + strconv.Itoa(set.Port)}
}

// adminTestMail sends the test message to one address the admin typed.
func (a *API) adminTestMail(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	c, _ := caller(r)
	acc := accessOf(r)
	var body struct {
		To string `json:"to"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<12)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	to := strings.TrimSpace(body.To)
	if to == "" || len(to) > 254 || !plainAddress.MatchString(to) {
		sayMail(w, http.StatusBadRequest, "BAD_RECIPIENT")
		return
	}
	ctx := r.Context()
	set, err := a.store.mailSettingsQ(ctx, a.store.Pool, acc.OrgID, false)
	if err != nil {
		a.fail(w, err, "mail")
		return
	}
	if why := a.smtp.notReady(set); why != "" {
		sayMail(w, http.StatusConflict, why)
		return
	}
	logged := []AuditEntry{entry(r, "agent_market.mail.test", "agent_market_mail", orgOr(acc.OrgID),
		map[string]any{"to": to, "host": set.Host, "port": set.Port, "tls_mode": set.TLSMode})}
	if err := a.record(ctx, logged); err != nil {
		a.fail(w, err, "mail")
		return
	}
	msg := testMessage(langOfWriter(w), set, to, normEmail(c.Email), time.Now())
	msg.Org = acc.OrgID
	if err := a.smtp.Send(ctx, msg); err != nil {
		a.recordFailed(ctx, logged)
		a.log.Warn("the relay's test message was not sent", "err", err)
		code, vars := mailFailure(err, true)
		sayMail(w, http.StatusBadGateway, code, vars...)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"sent": true, "to": to})
}

// ── reminders by e-mail ──────────────────────────────────────────────────

// NotificationsPassResult is what one pass of the reminders' e-mail did.
type NotificationsPassResult struct{ Sent, Failed, Skipped int }

// NotificationsPass sends each new notification to its person by e-mail,
// once: sent, failed, or skipped when the organisation has no relay set up
// (it is then never sent later). The runner's minute pass calls it after the
// reminders. Several runners may: each notification is taken under its own
// row lock (SKIP LOCKED).
func (m *SMTPMailer) NotificationsPass(ctx context.Context) (NotificationsPassResult, error) {
	var res NotificationsPassResult
	for i := 0; i < 20; i++ {
		did, err := m.notifyOne(ctx, &res)
		if err != nil || !did {
			return res, err
		}
	}
	return res, nil
}

func (m *SMTPMailer) notifyOne(ctx context.Context, res *NotificationsPassResult) (bool, error) {
	tx, err := m.store.Pool.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	var id uuid.UUID
	var title, body, link, org, installStatus string
	var to *string
	// AM11: a notification is written in the language of the run it is about (notify.go, reminders.go);
	// the lines the mail adds round it are said in the same one
	var params json.RawMessage
	err = tx.QueryRow(ctx, `SELECT n.id, n.title, n.body, n.link, i.org_id, i.status,
			(SELECT u.email FROM identity.users u WHERE u.id = n.user_id AND u.is_active),
			(SELECT r.params FROM agent_market.agent_runs r WHERE r.id = n.run_id)
		FROM agent_market.notifications n
		JOIN agent_market.agent_installs i ON i.id = n.install_id
		WHERE n.email_status IS NULL AND n.dismissed_at IS NULL
		ORDER BY n.created_at LIMIT 1 FOR UPDATE OF n SKIP LOCKED`).Scan(&id, &title, &body, &link, &org, &installStatus, &to, &params)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	status := "skipped"
	set, err := m.store.mailSettingsQ(ctx, tx, org, false)
	if err != nil {
		return false, err
	}
	if installStatus == "active" && m.notReady(set) == "" && to != nil && plainAddress.MatchString(*to) {
		msg := reminderMessage(Run{Params: params}.Lang(), title, body, m.linkFor(link))
		msg.To, msg.Org = []string{*to}, org
		if err := m.deliver(ctx, set, msg.To, buildMessage(set.Sender, msg, time.Now())); err != nil {
			status = "failed"
			m.logger().Warn("a reminder was not e-mailed", "notification", id, "err", err)
		} else {
			status = "sent"
		}
	}
	if _, err := tx.Exec(ctx, `UPDATE agent_market.notifications SET email_status = $2, emailed_at = now() WHERE id = $1`, id, status); err != nil {
		return false, err
	}
	if err := tx.Commit(ctx); err != nil {
		return false, err
	}
	switch status {
	case "sent":
		res.Sent++
	case "failed":
		res.Failed++
	default:
		res.Skipped++
	}
	return true, nil
}

// linkFor is a notification's link on the chat's public address ("" = none).
func (m *SMTPMailer) linkFor(link string) string {
	u, err := url.Parse(strings.TrimSpace(m.WebURL))
	if err != nil || u.Scheme == "" || u.Host == "" || !strings.HasPrefix(link, "/") {
		return ""
	}
	return u.Scheme + "://" + u.Host + link
}

// reminderMessage is a notification as an e-mail: its title and body (the
// platform's sentences; what came from a package goes through the mail
// sanitiser) and the platform's own link to the round.
func reminderMessage(l Lang, title, body, link string) MailMessage {
	text := SanitizeMailText(title)
	if b := SanitizeMailText(body); b != "" {
		text += "\n\n" + b
	}
	if link != "" {
		text += T(l, "\n\nเปิดรอบนี้และส่งไฟล์ได้ที่: ") + link
	}
	text += T(l, "\n\n— Agent Market · อีเมลนี้มาจากการเตือนที่คุณตั้งไว้ในหน้า agent หยุดหรือเลื่อนการเตือนได้ที่หน้าเดียวกัน")
	return MailMessage{Subject: SanitizeMailText(title), Body: text}
}
