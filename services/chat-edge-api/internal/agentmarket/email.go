package agentmarket

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── E-mail drafts and approved sends (AM6 section C) ─────────────────────
//
// A delivered round can be turned into an e-mail draft: a subject, a few
// short Thai lines, the round's figures and the list of its files. The draft
// is built here, by the server, from the round's own rows — never written by
// a model, never taken from a document. It has a copy button on the web and
// is always there; sending it needs a mail connector (there is none in the
// product today: Config.Mail is nil) and the person's approval of that exact
// message (its body's hash). Every send is logged: who approved, to whom,
// what was attached, how it went — a failed send too.
//
// "Recipients come from the person, never from a document":
//   - a draft never has recipients; a send takes only what the person typed
//     (to, cc), each a plain address;
//   - whatever came from a round (the agent's name, a file's name, a
//     package's sentence) goes through SanitizeMailText, which takes out
//     e-mail addresses, links and paths — an instruction planted in a file
//     ("send this to boss@…") loses what it would act on;
//   - the attachments are the round's own artifacts, by their ids in the
//     database, never a file a document names.

// MailConnector sends a message the person approved.
type MailConnector interface {
	Send(ctx context.Context, m MailMessage) error
}

// MailMessage is one approved message: exactly the draft, to exactly whom the
// person typed, with the round's own files.
type MailMessage struct {
	To, Cc      []string
	Subject     string
	Body        string
	Attachments []MailAttachment
}

// MailAttachment is one of the round's own artifacts.
type MailAttachment struct {
	ArtifactID  uuid.UUID
	Name        string
	ContentType string
	Size        int64
	SHA256      string
	Data        []byte
}

var (
	mailtoText = regexp.MustCompile(`(?i)mailto:\S*`)
	urlText    = regexp.MustCompile(`(?i)(?:\b[a-z][a-z0-9+.\-]*://|\bwww\.)\S*`)
	pathText   = regexp.MustCompile(`(?i)(?:\b[a-z]:\\|\\\\)\S*`)
	emailText  = regexp.MustCompile(`[A-Za-z0-9._%+\-]*@[A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)*`)
	domainPath = regexp.MustCompile(`(?i)\b[a-z0-9\-]+(?:\.[a-z0-9\-]+)+/\S*`)
	bareDomain = regexp.MustCompile(`(?i)\b[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.(?:com|net|org|info|biz|io|co|th|example|test|local|app|dev|me|xyz|online|site|gov|edu|asia|cloud)\b`)
)

// SanitizeMailText takes out of a round's text what could steer a mail: an
// e-mail address, a link (http, https, www., a domain with a path, mailto:),
// a domain name, a path on a disk. What is left is said on one line.
func SanitizeMailText(s string) string {
	s = strings.NewReplacer("＠", "@", "﹫", "@").Replace(s)
	for _, re := range []*regexp.Regexp{mailtoText, urlText, pathText, emailText, domainPath, bareDomain} {
		s = re.ReplaceAllString(s, " ")
	}
	s = strings.ReplaceAll(s, "@", " ")
	return strings.Join(strings.Fields(s), " ")
}

// DraftAttachment is one of the round's files on a draft.
type DraftAttachment struct {
	ArtifactID uuid.UUID `json:"artifact_id"`
	Name       string    `json:"name"`
	Size       int64     `json:"size"`
	Format     string    `json:"format"`
}

// EmailSend is one approved send, as the contract has it.
type EmailSend struct {
	ID         uuid.UUID  `json:"id"`
	DraftID    uuid.UUID  `json:"draft_id"`
	To         []string   `json:"to"`
	Cc         []string   `json:"cc"`
	Status     string     `json:"status"`
	At         time.Time  `json:"at"`
	ApprovedBy ApprovedBy `json:"approved_by"`
}

// ApprovedBy is the signed-in person who approved a send.
type ApprovedBy struct {
	Name  string `json:"name"`
	Email string `json:"email"`
}

// EmailDraft is a draft as the person reads it — and the payload of its
// thread item (kind email_draft, declared in schema.go).
type EmailDraft struct {
	ID               uuid.UUID         `json:"id"`
	RunID            uuid.UUID         `json:"run_id"`
	Subject          PersonText        `json:"subject"`
	Body             PersonText        `json:"body"`
	Attachments      []DraftAttachment `json:"attachments"`
	CanSend          bool              `json:"can_send"`
	CannotSendReason PersonText        `json:"cannot_send_reason,omitzero"`
	BodySHA256       string            `json:"body_sha256"`
	CreatedAt        time.Time         `json:"created_at"`
	Sent             []EmailSend       `json:"sent"`

	installID, tenantID, userID uuid.UUID
}

func (EmailDraft) itemKind() string { return "email_draft" }

func bodyHash(body string) string {
	sum := sha256.Sum256([]byte(body))
	return hex.EncodeToString(sum[:])
}

// ── building a draft ─────────────────────────────────────────────────────

// draftFigure is one figure of a summary the draft names, in the order it names them.
type draftFigure struct {
	key   string
	money bool
	line  func(Fact) PersonText
}

var draftFigures = []draftFigure{
	{"revenue", true, func(v Fact) PersonText { return Text("รายได้รวม %s บาท", v) }},
	{"total_expenses", true, func(v Fact) PersonText { return Text("ค่าใช้จ่ายรวม %s บาท", v) }},
	{"net_profit", true, func(v Fact) PersonText {
		return Text("กำไร (ขาดทุน) สุทธิ %s บาท", v)
	}},
	{"bank_balance", true, func(v Fact) PersonText {
		return Text("ยอดคงเหลือตามธนาคาร %s บาท", v)
	}},
	{"book_balance", true, func(v Fact) PersonText {
		return Text("ยอดคงเหลือตามบัญชี %s บาท", v)
	}},
	{"adjusted_bank", true, func(v Fact) PersonText {
		return Text("ยอดตามธนาคารหลังปรับปรุง %s บาท", v)
	}},
	{"adjusted_book", true, func(v Fact) PersonText {
		return Text("ยอดตามบัญชีหลังปรับปรุง %s บาท", v)
	}},
	{"difference", true, func(v Fact) PersonText { return Text("ผลต่าง %s บาท", v) }},
	{"balance_diff", true, func(v Fact) PersonText {
		return Text("ผลต่างของยอดคงเหลือ %s บาท", v)
	}},
	{"total_thb", true, func(v Fact) PersonText { return Text("ยอดรวม %s บาท", v) }},
	{"claimed_thb", true, func(v Fact) PersonText { return Text("ยอดเบิกรวม %s บาท", v) }},
	{"vat_thb", true, func(v Fact) PersonText { return Text("ภาษีมูลค่าเพิ่ม %s บาท", v) }},
	{"value_passed", true, func(v Fact) PersonText {
		return Text("ยอดรวมใบแจ้งหนี้ที่ผ่านการตรวจ %s บาท", v)
	}},
	{"value_blocked", true, func(v Fact) PersonText {
		return Text("ยอดรวมใบแจ้งหนี้ที่ระงับไว้ %s บาท", v)
	}},
	{"value_person", true, func(v Fact) PersonText {
		return Text("ยอดรวมที่รอคนตัดสิน %s บาท", v)
	}},
	{"received_not_billed", true, func(v Fact) PersonText {
		return Text("รับของแล้วแต่ยังไม่ถูกเรียกเก็บ %s บาท", v)
	}},
	{"billed_not_received", true, func(v Fact) PersonText {
		return Text("ถูกเรียกเก็บแต่ยังไม่ได้รับของ %s บาท", v)
	}},
	{"invoices", false, func(v Fact) PersonText { return Text("ใบแจ้งหนี้ %s ฉบับ", v) }},
	{"passed", false, func(v Fact) PersonText { return Text("ผ่านการตรวจ %s ฉบับ", v) }},
	{"documents", false, func(v Fact) PersonText { return Text("เอกสาร %s ฉบับ", v) }},
	{"claims", false, func(v Fact) PersonText { return Text("รายการเบิก %s รายการ", v) }},
	{"matched", false, func(v Fact) PersonText { return Text("จับคู่ได้ %s รายการ", v) }},
	{"exceptions", false, func(v Fact) PersonText { return Text("ข้อยกเว้น %s รายการ", v) }},
	{"review", false, func(v Fact) PersonText {
		return Text("รายการที่ต้องตรวจทาน %s รายการ", v)
	}},
}

var figureShape = regexp.MustCompile(`^-?[0-9][0-9,]*(\.[0-9]+)?$`)

// figureOf reads a figure the summary carries (a number, or a string of one).
func figureOf(v any) (float64, bool) {
	var s string
	switch x := v.(type) {
	case json.Number:
		s = x.String()
	case float64:
		return x, true
	case string:
		s = strings.TrimSpace(x)
	default:
		return 0, false
	}
	if !figureShape.MatchString(s) {
		return 0, false
	}
	f, err := strconv.ParseFloat(strings.ReplaceAll(s, ",", ""), 64)
	return f, err == nil
}

// outcomeWords are F's words for what a delivered round came to.
func outcomeWords(run Run) PersonText {
	if run.Outcome() == OutcomeCompletedOpen {
		return CompletionTitle(run.StatusReason)
	}
	return Text("เสร็จแล้ว")
}

// mailFileName is an artifact's name as a mail may carry it: sanitised, with its format.
func mailFileName(a Artifact) string {
	name := SanitizeMailText(a.Name)
	name = strings.NewReplacer("/", " ", "\\", " ").Replace(name)
	ext := "." + strings.ToLower(a.Format)
	if !strings.HasSuffix(strings.ToLower(name), ext) {
		name = strings.TrimSpace(strings.TrimSuffix(name, ".")) + ext
	}
	if strings.TrimSpace(strings.TrimSuffix(name, ext)) == "" {
		name = "ไฟล์ผลงาน" + ext
	}
	return name
}

// buildDraft builds a delivered round's draft from its rows.
func (a *API) buildDraft(ctx context.Context, run Run) (EmailDraft, error) {
	d := EmailDraft{RunID: run.ID, installID: run.InstallID, tenantID: run.TenantID, userID: run.UserID}
	in, err := a.store.InstallByID(ctx, run.InstallID)
	if err != nil {
		return d, err
	}
	agentName := SanitizeMailText(in.Title)
	if agentName == "" {
		if def, err := a.store.DefinitionByID(ctx, in.DefinitionID); err == nil {
			agentName = SanitizeMailText(def.Name)
		}
	}
	agent := LabelFromPackage(agentName, Text("agent ของคุณ"))
	var period PersonText
	if run.Period != nil && *run.Period != "" {
		period = LabelFromPackage(SanitizeMailText(PeriodLabel(*run.Period)), NoText)
	}
	outcome := outcomeWords(run)
	d.Subject = Join(" · ", agent, period, outcome)

	// the package's summary, as the person-text rule lets it be sent (ForClient)
	var summary map[string]any
	if it, err := a.store.ItemFor(ctx, run.InstallID, "outputs", "run_id", run.ID.String()); err == nil {
		var p struct {
			Summary map[string]any `json:"summary"`
		}
		dec := json.NewDecoder(bytes.NewReader(it.Payload))
		dec.UseNumber()
		_ = dec.Decode(&p)
		summary = p.Summary
	} else if !errors.Is(err, ErrNotFound) {
		return d, err
	}

	head := []PersonText{Text("สรุปผลของ %s", Words(agent))}
	if !period.Empty() {
		head[0] = Text("สรุปผลของ %s งวด %s", Words(agent), Words(period))
	}
	head = append(head, Text("สถานะ: %s", Words(outcome)))
	if g, ok := summary["gate"].(map[string]any); ok {
		if what, ok := g["what"].(string); ok {
			head = append(head, FromPackage(SanitizeMailText(what), NoText))
		}
	}
	var figures []PersonText
	for _, f := range draftFigures {
		v, ok := figureOf(summary[f.key])
		if !ok {
			continue
		}
		fact := Baht(v)
		if !f.money {
			if v != float64(int64(v)) {
				continue
			}
			fact = Count(int(v))
		}
		figures = append(figures, Text("· %s", Words(f.line(fact))))
	}
	arts, err := a.store.RunArtifacts(ctx, run.ID)
	if err != nil {
		return d, err
	}
	var files []PersonText
	d.Attachments = []DraftAttachment{}
	for _, art := range latestArtifacts(arts) { // the newest version of each file (catalog.go)
		name := mailFileName(art)
		d.Attachments = append(d.Attachments, DraftAttachment{ArtifactID: art.ID, Name: name, Size: art.Size, Format: art.Format})
		files = append(files, Text("· %s", FileName(name)))
	}
	var inputs []string
	if fs, err := a.store.RunFiles(ctx, run.ID, "active"); err == nil {
		for _, f := range fs {
			if n := SanitizeMailText(f.Name); n != "" {
				inputs = append(inputs, n)
			}
		}
	}
	sections := []PersonText{Join("\n", head...)}
	if len(figures) > 0 {
		sections = append(sections, Join("\n", append([]PersonText{Text("ตัวเลขสำคัญ")}, figures...)...))
	}
	if len(files) > 0 {
		sections = append(sections, Join("\n", append([]PersonText{Text("ไฟล์แนบ %s ไฟล์", Count(len(files)))}, files...)...))
	}
	if len(inputs) > 0 {
		sections = append(sections, Text("ข้อมูลจากไฟล์ที่ส่งมา: %s", FileNames(inputs)))
	}
	sections = append(sections, Text("ร่างนี้ระบบสร้างจากผลของรอบงาน ตรวจทานตัวเลขและไฟล์แนบก่อนส่งทุกครั้ง"))
	// what is stored is what a person is shown of it, so the hash is of what they read
	d.Body = FromPackage(Join("\n\n", sections...).String(), NoText)
	d.BodySHA256 = bodyHash(d.Body.String())
	return d, nil
}

// ── the store ────────────────────────────────────────────────────────────

func (s *Store) saveDraft(ctx context.Context, d EmailDraft) (EmailDraft, error) {
	atts, _ := json.Marshal(d.Attachments)
	err := s.Pool.QueryRow(ctx, `INSERT INTO agent_market.email_drafts (run_id, install_id, tenant_id, user_id, subject, body, attachments, body_sha256)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		ON CONFLICT (run_id) DO UPDATE SET subject = EXCLUDED.subject, body = EXCLUDED.body, attachments = EXCLUDED.attachments,
			body_sha256 = EXCLUDED.body_sha256, updated_at = now()
		RETURNING id, created_at`, d.RunID, d.installID, d.tenantID, d.userID, d.Subject.String(), d.Body.String(), atts, d.BodySHA256).
		Scan(&d.ID, &d.CreatedAt)
	return d, err
}

// draftOf reads the caller's draft (somebody else's is not found).
func (s *Store) draftOf(ctx context.Context, tenant, user, id uuid.UUID) (EmailDraft, error) {
	var d EmailDraft
	var subject, body string
	var atts json.RawMessage
	err := s.Pool.QueryRow(ctx, `SELECT id, run_id, install_id, tenant_id, user_id, subject, body, attachments, body_sha256, created_at
		FROM agent_market.email_drafts WHERE id = $1 AND tenant_id = $2 AND user_id = $3`, id, tenant, user).
		Scan(&d.ID, &d.RunID, &d.installID, &d.tenantID, &d.userID, &subject, &body, &atts, &d.BodySHA256, &d.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return d, ErrNotFound
	}
	if err != nil {
		return d, err
	}
	// a stored text is only what a person is shown of it; it was stored as that
	d.Subject, d.Body = LabelFromPackage(subject, NoText), FromPackage(body, NoText)
	_ = json.Unmarshal(atts, &d.Attachments)
	if d.Attachments == nil {
		d.Attachments = []DraftAttachment{}
	}
	return d, nil
}

func (s *Store) sendsOf(ctx context.Context, draft uuid.UUID) ([]EmailSend, error) {
	rows, err := s.Pool.Query(ctx, `SELECT id, draft_id, to_addrs, cc_addrs, status, approved_at, approved_by_name, approved_by_email
		FROM agent_market.email_sends WHERE draft_id = $1 ORDER BY created_at`, draft)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []EmailSend{}
	for rows.Next() {
		var x EmailSend
		if err := rows.Scan(&x.ID, &x.DraftID, &x.To, &x.Cc, &x.Status, &x.At, &x.ApprovedBy.Name, &x.ApprovedBy.Email); err != nil {
			return nil, err
		}
		x.To, x.Cc = nonNil(x.To), nonNil(x.Cc)
		out = append(out, x)
	}
	return out, rows.Err()
}

// complete adds what the draft says about sending, and its sends.
func (a *API) complete(ctx context.Context, d EmailDraft) (EmailDraft, error) {
	d.CanSend = a.cfg.Mail != nil
	if !d.CanSend {
		d.CannotSendReason = Text("ยังไม่มีตัวเชื่อมต่ออีเมลในระบบ คัดลอกข้อความนี้ไปส่งจากอีเมลของคุณได้")
	}
	sent, err := a.store.sendsOf(ctx, d.ID)
	d.Sent = sent
	return d, err
}

// postDraft puts the draft in the round's thread: once, brought up to date after.
func (a *API) postDraft(ctx context.Context, d EmailDraft) error {
	if it, err := a.store.ItemFor(ctx, d.installID, "email_draft", "id", d.ID.String()); err == nil {
		return a.store.UpdateItem(ctx, it.ID, d)
	} else if !errors.Is(err, ErrNotFound) {
		return err
	}
	rid := d.RunID
	_, _, err := a.store.AddItem(ctx, a.store.Pool, d.installID, d.tenantID, d.userID, &rid, d, nil)
	return err
}

// ── handlers ─────────────────────────────────────────────────────────────

// emailDraft builds (or rebuilds) the draft of a delivered round.
func (a *API) emailDraft(w http.ResponseWriter, r *http.Request) {
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
	if run.Kind != KindRun || !run.Delivered() {
		sayMail(w, http.StatusConflict, "NOT_DELIVERED")
		return
	}
	d, err := a.buildDraft(ctx, run)
	if err == nil {
		d, err = a.store.saveDraft(ctx, d)
	}
	if err == nil {
		d, err = a.complete(ctx, d)
	}
	if err == nil {
		err = a.postDraft(ctx, d)
	}
	if err != nil {
		a.fail(w, err, "draft")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"draft": d})
}

func (a *API) ownedDraft(w http.ResponseWriter, r *http.Request) (EmailDraft, bool) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return EmailDraft{}, false
	}
	d, err := a.store.draftOf(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		if errors.Is(err, ErrNotFound) {
			say(w, http.StatusNotFound, "NOT_FOUND", "what", "ร่างอีเมล")
		} else {
			a.fail(w, err, "draft")
		}
		return d, false
	}
	return d, true
}

func (a *API) getEmailDraft(w http.ResponseWriter, r *http.Request) {
	d, ok := a.ownedDraft(w, r)
	if !ok {
		return
	}
	d, err := a.complete(r.Context(), d)
	if err != nil {
		a.fail(w, err, "draft")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"draft": d})
}

// plainAddress is an e-mail address and nothing else: no name, no list, no scheme.
var plainAddress = regexp.MustCompile(`^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+$`)

// MaxRecipients is how many addresses one send may carry, to and cc together.
const MaxRecipients = 10

// recipients are the addresses the person typed, each a plain address, once.
func recipients(to, cc []string) ([]string, []string, string) {
	seen := map[string]bool{}
	take := func(list []string) ([]string, bool) {
		out := []string{}
		for _, s := range list {
			s = strings.TrimSpace(s)
			if s == "" {
				continue
			}
			if len(s) > 254 || !plainAddress.MatchString(s) {
				return nil, false
			}
			if k := strings.ToLower(s); !seen[k] {
				seen[k] = true
				out = append(out, s)
			}
		}
		return out, true
	}
	t, ok := take(to)
	if !ok {
		return nil, nil, "BAD_RECIPIENT"
	}
	c, ok := take(cc)
	if !ok {
		return nil, nil, "BAD_RECIPIENT"
	}
	if len(t) == 0 {
		return nil, nil, "NO_RECIPIENT"
	}
	if len(t)+len(c) > MaxRecipients {
		return nil, nil, "BAD_RECIPIENT"
	}
	return t, c, ""
}

type sendReq struct {
	To         []string `json:"to"`
	Cc         []string `json:"cc"`
	BodySHA256 string   `json:"body_sha256"`
	Approve    bool     `json:"approve"`
}

// sendEmail sends the approved draft through the connector, to the people
// the person typed, and logs it — a failed send too.
func (a *API) sendEmail(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	d, ok := a.ownedDraft(w, r)
	if !ok {
		return
	}
	var req sendReq
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<16))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		if strings.Contains(err.Error(), "unknown field") {
			sayMail(w, http.StatusBadRequest, "BAD_FIELDS")
		} else {
			badRequest(w, "BAD_JSON")
		}
		return
	}
	if !req.Approve {
		sayMail(w, http.StatusBadRequest, "NOT_APPROVED")
		return
	}
	to, cc, problem := recipients(req.To, req.Cc)
	if problem != "" {
		sayMail(w, http.StatusBadRequest, problem)
		return
	}
	if req.BodySHA256 != d.BodySHA256 || bodyHash(d.Body.String()) != d.BodySHA256 {
		sayMail(w, http.StatusConflict, "DRAFT_CHANGED")
		return
	}
	if a.cfg.Mail == nil {
		sayMail(w, http.StatusConflict, "NO_MAIL_CONNECTOR")
		return
	}
	ctx := r.Context()
	run, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, d.RunID)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	// the round's own files, by the ids the draft holds, read from the database
	arts, err := a.store.RunArtifacts(ctx, run.ID)
	if err != nil {
		a.fail(w, err, "artifact")
		return
	}
	byID := map[uuid.UUID]Artifact{}
	for _, art := range arts {
		byID[art.ID] = art
	}
	msg := MailMessage{To: to, Cc: cc, Subject: d.Subject.String(), Body: d.Body.String()}
	type logged struct {
		ArtifactID uuid.UUID `json:"artifact_id"`
		Name       string    `json:"name"`
		Size       int64     `json:"size"`
		SHA256     string    `json:"sha256"`
		Format     string    `json:"format"`
	}
	attached := []logged{}
	var sendErr error
	for _, at := range d.Attachments {
		art, ok := byID[at.ArtifactID]
		if !ok {
			sendErr = errors.New("attachment " + at.ArtifactID.String() + " is not one of the round's artifacts")
			break
		}
		data, err := a.readArtifact(ctx, art)
		if err != nil {
			sendErr = err
			break
		}
		name := mailFileName(art)
		msg.Attachments = append(msg.Attachments, MailAttachment{ArtifactID: art.ID, Name: name, ContentType: artifactMime(art),
			Size: int64(len(data)), SHA256: art.SHA256, Data: data})
		attached = append(attached, logged{ArtifactID: art.ID, Name: name, Size: int64(len(data)), SHA256: art.SHA256, Format: art.Format})
	}
	if sendErr == nil {
		sendErr = a.cfg.Mail.Send(ctx, msg)
	}
	status, errText := "sent", ""
	if sendErr != nil {
		status, errText = "failed", TrimText(sendErr.Error(), 1000)
		a.log.Warn("an approved e-mail was not sent", "draft", d.ID, "err", sendErr)
	}
	send := EmailSend{DraftID: d.ID, To: to, Cc: cc, Status: status,
		ApprovedBy: ApprovedBy{Name: a.store.DisplayName(ctx, accessOf(r).OrgID, c.UserID, c.Email), Email: c.Email}}
	atts, _ := json.Marshal(attached)
	if err := a.store.Pool.QueryRow(ctx, `INSERT INTO agent_market.email_sends (draft_id, run_id, to_addrs, cc_addrs, subject, body_sha256,
			attachments, approved_by_user_id, approved_by_name, approved_by_email, status, error)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING id, approved_at`,
		d.ID, d.RunID, to, cc, msg.Subject, d.BodySHA256, atts, c.UserID, send.ApprovedBy.Name, send.ApprovedBy.Email, status, errText).
		Scan(&send.ID, &send.At); err != nil {
		a.fail(w, err, "draft")
		return
	}
	if cur, err := a.complete(ctx, d); err == nil {
		_ = a.postDraft(ctx, cur)
	}
	writeJSON(w, http.StatusOK, map[string]any{"send": send})
}

func (a *API) readArtifact(ctx context.Context, art Artifact) ([]byte, error) {
	body, _, _, err := a.files.Download(ctx, art.StorageKey)
	if err != nil {
		return nil, err
	}
	defer body.Close()
	return io.ReadAll(io.LimitReader(body, 64<<20))
}
