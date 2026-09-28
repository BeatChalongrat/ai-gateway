package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
)

// ── Rounds and threads (AM4) ─────────────────────────────────────────────
//
// A round is one run of the agent on one set of files. A finished round is
// never cleared and never continued: the workspace closes it with a divider
// ("รอบ กันยายน 2569 · เสร็จ") and a "เริ่มรอบใหม่" button. When files or an
// instruction arrive after a round finished and they do not read as a
// question about that round, the agent does not guess: it shows a small
// chip — [เริ่มรอบใหม่] [ถามเกี่ยวกับรอบที่แล้ว] — and waits.
//
// A new round inherits the install's settings and remembered answers only:
// never the previous round's files, figures, answers or typed instruction.
//
// A thread is one conversation with the agent. The install's first thread
// is implicit (thread_id NULL); "แชทใหม่กับ agent นี้" starts another, for
// people who keep one thread per period. Deleting a chat is never the way to
// start fresh — it would destroy the record behind the remembered answers.

var thaiMonths = []string{"มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม",
	"กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"}

// PeriodLabel is "2026-09" as a Thai reader says it: "กันยายน 2569".
func PeriodLabel(period string) string {
	var y, m int
	if _, err := fmt.Sscanf(period, "%d-%d", &y, &m); err != nil || m < 1 || m > 12 {
		return period
	}
	return fmt.Sprintf("%s %d", thaiMonths[m-1], y+543)
}

// ThaiDate is "28 ก.ย. 2569" (Bangkok).
func ThaiDate(t time.Time) string {
	loc, err := time.LoadLocation("Asia/Bangkok")
	if err != nil {
		loc = time.FixedZone("ICT", 7*3600)
	}
	t = t.In(loc)
	return fmt.Sprintf("%d %s %d", t.Day(), thaiMonthsShort[int(t.Month())-1], t.Year()+543)
}

// ── reading a sentence typed after a finished round ─────────────────────

var aboutRoundWords = []string{
	"?", "？", "ทำไม", "เพราะอะไร", "เพราะ", "อย่างไร", "ยังไง", "เท่าไร", "เท่าไหร่", "ไหม", "มั้ย", "หรือเปล่า", "หรือไม่",
	"ไม่ใช่เหรอ", "ไม่ใช่หรือ", "เหรอ", "หรอ", "อะไร", "ที่ไหน", "ตรงไหน", "มาจาก", "ที่มา", "อธิบาย", "คำนวณ", "คิดจาก",
	"ขอไฟล์", "ขอเป็น", "ขอแบบ", "ไฟล์อื่น", "แบบอื่น", "รูปแบบอื่น", "pdf", "พีดีเอฟ", "word", "เวิร์ด", "excel", "เอ็กเซล",
	"powerpoint", "สไลด์", "docx", "pptx", "xlsx", "ภาษาอังกฤษ", "english",
	"why", "how", "what", "where", "explain",
}

// ReadsAsAboutLastRound: a question about the round that just finished (why
// a figure is what it is, where it came from) or a request for its files in
// another form. Anything else typed after a finished round is an instruction.
func ReadsAsAboutLastRound(text string) bool {
	t := strings.ToLower(strings.TrimSpace(text))
	if t == "" {
		return false
	}
	for _, w := range aboutRoundWords {
		if strings.Contains(t, w) {
			return true
		}
	}
	return false
}

var socialWords = []string{"ขอบคุณ", "ขอบใจ", "โอเค", "ok", "okay", "thanks", "thank you", "ได้เลย", "รับทราบ", "เยี่ยม",
	"ดีมาก", "ครับ", "ค่ะ", "คะ", "จ้า", "👍", "🙏"}

// SocialOnly: a short thanks or acknowledgement, not an instruction.
func SocialOnly(text string) bool {
	t := strings.ToLower(strings.TrimSpace(text))
	if t == "" || len([]rune(t)) > 24 {
		return false
	}
	rest := t
	for _, w := range socialWords {
		rest = strings.ReplaceAll(rest, w, "")
	}
	return strings.Trim(rest, " .!~ๆ") == ""
}

// wantsFreshStart: words of someone about to delete a chat to start over.
// They wanted a new round; the chip gives it to them and says why the old
// rounds are kept.
func wantsFreshStart(text string) bool {
	t := strings.ToLower(text)
	for _, w := range []string{"ลบแชท", "ล้างแชท", "ลบประวัติ", "ล้างประวัติ", "เริ่มใหม่หมด", "ลบทิ้ง", "ล้างทั้งหมด", "clear chat", "delete chat"} {
		if strings.Contains(t, w) {
			return true
		}
	}
	return false
}

// ── store ────────────────────────────────────────────────────────────────

// Thread is one conversation with an installed agent (the first one has no id).
type Thread struct {
	ID        *uuid.UUID `json:"id"`
	Title     string     `json:"title"`
	CreatedAt time.Time  `json:"created_at"`
	LastAt    time.Time  `json:"last_at"`
	Rounds    int        `json:"rounds"`
	Waiting   bool       `json:"waiting"`
}

// ThreadOwned reports whether a thread belongs to the install.
func (s *Store) ThreadOwned(ctx context.Context, install uuid.UUID, thread uuid.UUID) (bool, error) {
	var ok bool
	err := s.Pool.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM agent_market.agent_threads WHERE id = $1 AND install_id = $2)`,
		thread, install).Scan(&ok)
	return ok, err
}

// CreateThread starts a new conversation with the agent.
func (s *Store) CreateThread(ctx context.Context, q Querier, in Install) (uuid.UUID, time.Time, error) {
	var id uuid.UUID
	var at time.Time
	err := q.QueryRow(ctx, `INSERT INTO agent_market.agent_threads (install_id, tenant_id, user_id) VALUES ($1, $2, $3)
		RETURNING id, created_at`, in.ID, in.TenantID, in.UserID).Scan(&id, &at)
	return id, at, err
}

// ListThreads lists an install's conversations, the newest activity first.
func (s *Store) ListThreads(ctx context.Context, in Install) ([]Thread, error) {
	rows, err := s.Pool.Query(ctx, `
		WITH t AS (
			SELECT NULL::uuid AS id, $2::timestamptz AS created_at
			UNION ALL
			SELECT id, created_at FROM agent_market.agent_threads WHERE install_id = $1
		)
		SELECT t.id, t.created_at,
			COALESCE((SELECT max(i.created_at) FROM agent_market.agent_thread_items i
				WHERE i.install_id = $1 AND i.thread_id IS NOT DISTINCT FROM t.id), t.created_at),
			(SELECT count(*) FROM agent_market.agent_runs r
				WHERE r.install_id = $1 AND r.kind = 'run' AND r.thread_id IS NOT DISTINCT FROM t.id)::int,
			(SELECT r.period FROM agent_market.agent_runs r
				WHERE r.install_id = $1 AND r.kind = 'run' AND r.thread_id IS NOT DISTINCT FROM t.id AND r.period IS NOT NULL
				ORDER BY r.created_at DESC LIMIT 1),
			EXISTS (SELECT 1 FROM agent_market.agent_runs r
				WHERE r.install_id = $1 AND r.thread_id IS NOT DISTINCT FROM t.id AND r.status = 'awaiting_input')
		FROM t ORDER BY 3 DESC`, in.ID, in.CreatedAt)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Thread{}
	var periods []string
	for rows.Next() {
		var t Thread
		var period *string
		if err := rows.Scan(&t.ID, &t.CreatedAt, &t.LastAt, &t.Rounds, &period, &t.Waiting); err != nil {
			return nil, err
		}
		p := ""
		if period != nil {
			p = *period
		}
		out = append(out, t)
		periods = append(periods, p)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	// Every chat keeps a name of its own ("แชทแรก", "แชทใหม่ · 28 ก.ย. 2569",
	// with the time when two began the same day); its latest round follows.
	name := func(t Thread, withTime bool) string {
		switch {
		case t.ID == nil:
			return "แชทแรก"
		case withTime:
			return "แชทใหม่ · " + ThaiDateTime(t.CreatedAt)
		default:
			return "แชทใหม่ · " + ThaiDate(t.CreatedAt)
		}
	}
	sameDay := map[string]int{}
	for _, t := range out {
		sameDay[name(t, false)]++
	}
	for i := range out {
		n := name(out[i], sameDay[name(out[i], false)] > 1)
		if periods[i] != "" {
			n += " · รอบ " + PeriodLabel(periods[i])
		}
		out[i].Title = n
	}
	return out, nil
}

// RoundStartedSince: "เริ่มรอบใหม่" was pressed in this thread after the given time.
func (s *Store) RoundStartedSince(ctx context.Context, q Querier, install uuid.UUID, thread *uuid.UUID, since time.Time) (bool, error) {
	var ok bool
	err := q.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM agent_market.agent_thread_items
		WHERE install_id = $1 AND thread_id IS NOT DISTINCT FROM $2::uuid AND kind = 'notice'
		  AND payload->>'round_start' = 'true' AND created_at > $3)`, install, thread, since).Scan(&ok)
	return ok, err
}

// LinkItem marks a person's message as handled by a run.
func (s *Store) LinkItem(ctx context.Context, q Querier, item, run uuid.UUID, handledAs string) error {
	_, err := q.Exec(ctx, `UPDATE agent_market.agent_thread_items SET run_id = $2,
		payload = payload || jsonb_build_object('status', 'handled', 'run_id', $4::text, 'handled_as', $3::text),
		rev = nextval('agent_market.thread_rev'), updated_at = now() WHERE id = $1`, item, run, handledAs, run.String())
	return err
}

// ItemByID is one thread item of the caller's.
func (s *Store) ItemByID(ctx context.Context, tenant, user, id uuid.UUID) (ThreadItem, error) {
	t, err := scanItem(s.Pool.QueryRow(ctx, `SELECT `+itemCols+` FROM agent_market.agent_thread_items
		WHERE id = $1 AND tenant_id = $2 AND user_id = $3`, id, tenant, user))
	if errors.Is(err, pgx.ErrNoRows) {
		return t, ErrNotFound
	}
	return t, err
}

// ── the chip ─────────────────────────────────────────────────────────────

// IntentPayload is the round-boundary chip as the thread shows it.
type IntentPayload struct {
	Status    string           `json:"status"` // open | chosen
	Choice    string           `json:"choice,omitempty"`
	Text      string           `json:"text,omitempty"`
	Files     []map[string]any `json:"files,omitempty"`
	ItemID    string           `json:"item_id"`
	LastRound map[string]any   `json:"last_round"`
	Fresh     bool             `json:"fresh,omitempty"`
	Model     string           `json:"model_choice,omitempty"`
	RunID     string           `json:"run_id,omitempty"`
	Note      string           `json:"note,omitempty"`
}

func (a *API) intentChip(ctx context.Context, tx pgx.Tx, in Install, c middleware.Caller, item ThreadItem, last Run,
	text string, files []File, o roundOpts) error {
	p := IntentPayload{Status: "open", Text: text, ItemID: item.ID.String(), Fresh: wantsFreshStart(text), Model: o.model,
		LastRound: map[string]any{"run_id": last.ID.String(), "period_label": periodOf(last)}}
	for _, f := range files {
		p.Files = append(p.Files, map[string]any{"id": f.ID, "name": f.Name, "size": f.Size})
	}
	// The person's message waits on the chip (the files stay unused until then).
	if err := a.store.PatchItem(ctx, tx, item.ID, map[string]any{"status": "handled", "handled_as": "intent"}); err != nil {
		return err
	}
	_, _, err := a.store.AddItemIn(ctx, tx, in.ID, o.thread, c.TenantUUID, c.UserID, nil, "intent", p, nil)
	return err
}

func periodOf(r Run) string {
	if r.Period != nil && *r.Period != "" {
		return PeriodLabel(*r.Period)
	}
	return ""
}

// ── handlers ─────────────────────────────────────────────────────────────

// threadParam reads ?thread= (absent = the first thread) and checks it is this install's.
func (a *API) threadParam(w http.ResponseWriter, r *http.Request, in Install) (*uuid.UUID, bool) {
	v := strings.TrimSpace(r.URL.Query().Get("thread"))
	if v == "" || v == "main" {
		return nil, true
	}
	id, err := uuid.Parse(v)
	if err != nil {
		badRequest(w, "BAD_THREAD")
		return nil, false
	}
	return &id, a.threadOf(w, r, in, &id)
}

// threadOf checks a thread named in a request belongs to the install.
func (a *API) threadOf(w http.ResponseWriter, r *http.Request, in Install, thread *uuid.UUID) bool {
	if thread == nil {
		return true
	}
	ok, err := a.store.ThreadOwned(r.Context(), in.ID, *thread)
	if err != nil {
		a.fail(w, err, "thread")
		return false
	}
	if !ok {
		a.fail(w, ErrNotFound, "thread")
		return false
	}
	return true
}

func (a *API) listThreads(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	list, err := a.store.ListThreads(r.Context(), in)
	if err != nil {
		a.fail(w, err, "thread")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"threads": list})
}

// createThread is "แชทใหม่กับ agent นี้": a new conversation, greeted like the
// first; it shares the install's settings and remembered answers only.
func (a *API) createThread(w http.ResponseWriter, r *http.Request) {
	in, c, ok := a.ownedInstall(w, r)
	if !ok || !a.agentGate(w, r, in.Slug, false) {
		return
	}
	ctx := r.Context()
	def, err := a.store.DefinitionByID(ctx, in.DefinitionID)
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	m, err := ParseManifest(def.Manifest)
	if err != nil {
		a.fail(w, err, "manifest")
		return
	}
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "thread")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	id, at, err := a.store.CreateThread(ctx, tx, in)
	if err != nil {
		a.fail(w, err, "thread")
		return
	}
	if _, _, err := a.store.AddItemIn(ctx, tx, in.ID, &id, c.TenantUUID, c.UserID, nil, "greeting", GreetingFor(m), nil); err != nil {
		a.fail(w, err, "thread")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "thread")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"thread": Thread{ID: &id, Title: "แชทใหม่ · " + ThaiDate(at), CreatedAt: at, LastAt: at}})
}

// startRound is the divider's "เริ่มรอบใหม่": one click, no typing. It opens the
// next round in the thread; the next files (or instruction) start it.
func (a *API) startRound(w http.ResponseWriter, r *http.Request) {
	in, c, ok := a.ownedInstall(w, r)
	if !ok || !a.agentGate(w, r, in.Slug, false) {
		return
	}
	var body struct {
		ThreadID *uuid.UUID `json:"thread_id"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body)
	if !a.threadOf(w, r, in, body.ThreadID) {
		return
	}
	ctx := r.Context()
	if last, err := a.store.LatestRunIn(ctx, in.ID, body.ThreadID); err == nil && !IsTerminal(last.Status) {
		say(w, http.StatusConflict, "ROUND_IN_PROGRESS")
		return
	}
	item, _, err := a.store.AddItemIn(ctx, a.store.Pool, in.ID, body.ThreadID, c.TenantUUID, c.UserID, nil, "notice",
		map[string]any{"text": "เริ่มรอบใหม่แล้ว — ส่งไฟล์ของรอบนี้ได้เลย ใช้ค่าที่ตั้งไว้และคำตอบที่จำไว้ต่อ " +
			"ส่วนไฟล์ ตัวเลข และคำตอบของรอบก่อนไม่ถูกนำมาใช้", "round_start": true, "tone": "success"}, nil)
	if err != nil {
		a.fail(w, err, "round")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"item": item})
}

// answerIntent is the chip's answer: start a new round with what was sent, or
// treat it as a question about the round that finished.
func (a *API) answerIntent(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var body struct {
		Choice string `json:"choice"` // new_round | about_last
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	if body.Choice != "new_round" && body.Choice != "about_last" {
		badRequest(w, "BAD_CHOICE")
		return
	}
	ctx := r.Context()
	item, err := a.store.ItemByID(ctx, c.TenantUUID, c.UserID, id)
	if err != nil || item.Kind != "intent" {
		a.fail(w, ErrNotFound, "chip")
		return
	}
	var p IntentPayload
	if err := json.Unmarshal(item.Payload, &p); err != nil {
		a.fail(w, err, "chip")
		return
	}
	if p.Status != "open" {
		// Idempotent: a second click finds what the first one did.
		writeJSON(w, http.StatusOK, map[string]any{"item": item, "changed": false})
		return
	}
	in, err := a.store.GetInstall(ctx, c.TenantUUID, c.UserID, item.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if !a.agentGate(w, r, in.Slug, false) {
		return
	}
	source, _ := uuid.Parse(p.ItemID)
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "chip")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	patch := map[string]any{"status": "chosen", "choice": body.Choice}
	o := roundOpts{thread: item.ThreadID, model: p.Model, newRound: true}
	switch body.Choice {
	case "new_round":
		var ids []uuid.UUID
		for _, f := range p.Files {
			fid, err := uuid.Parse(fmt.Sprint(f["id"]))
			if err != nil {
				continue
			}
			f, err := a.store.GetFile(ctx, c.TenantUUID, c.UserID, fid)
			if err != nil || f.RunID != nil {
				say(w, http.StatusGone, "BAD_FILE")
				return
			}
			ids = append(ids, fid)
		}
		run, err := a.newRun(ctx, tx, in, c, p.Text, ids, o)
		if err != nil {
			a.fail(w, err, "run")
			return
		}
		if source != uuid.Nil {
			if err := a.store.LinkItem(ctx, tx, source, run.ID, "new_run"); err != nil {
				a.fail(w, err, "chip")
				return
			}
		}
		patch["run_id"] = run.ID.String()
	case "about_last":
		last, err := a.store.LatestRunIn(ctx, in.ID, item.ThreadID)
		if err != nil {
			a.fail(w, err, "run")
			return
		}
		if strings.TrimSpace(p.Text) != "" {
			job, err := a.replyJob(ctx, tx, in, c, source, p.Text, &last.ID, item.ThreadID)
			if err != nil {
				a.fail(w, err, "run")
				return
			}
			if source != uuid.Nil {
				if err := a.store.LinkItem(ctx, tx, source, job.ID, "reply"); err != nil {
					a.fail(w, err, "chip")
					return
				}
			}
		}
		if len(p.Files) > 0 {
			patch["note"] = "ไฟล์ที่แนบมาไม่ได้ใส่ในรอบที่แล้ว เพราะรอบนั้นส่งตัวเลขไปแล้ว — ไฟล์ที่ไม่ได้ใช้ในรอบใดจะเก็บไว้ 30 วัน " +
				"ถ้าจะใช้ ให้กด “เริ่มรอบใหม่”"
		} else if strings.TrimSpace(p.Text) == "" {
			patch["note"] = "พิมพ์คำถามเกี่ยวกับรอบที่แล้วได้เลย"
		}
	}
	if err := a.store.PatchItem(ctx, tx, item.ID, patch); err != nil {
		a.fail(w, err, "chip")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "chip")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"changed": true, "choice": body.Choice, "run_id": patch["run_id"]})
}

// replyJob queues a small job that answers a typed message about a round
// (a question about its figures, a file in another form).
func (a *API) replyJob(ctx context.Context, q Querier, in Install, c middleware.Caller, itemID uuid.UUID, text string,
	parent *uuid.UUID, thread *uuid.UUID) (Run, error) {
	params, _ := json.Marshal(map[string]any{"item_id": itemID, "text": text})
	job, _, err := a.store.CreateRun(ctx, q, Run{InstallID: in.ID, ParentRunID: parent, TenantID: c.TenantUUID,
		UserID: c.UserID, Kind: KindReply, Message: text, Params: params, DefinitionVersion: in.DefinitionVersion,
		Checksum: in.Checksum, BindingVersion: in.BindingVersion, ThreadID: thread}, "")
	return job, err
}

// roundRoutes mounts AM4's routes (inside the allowed group).
func (a *API) roundRoutes(r chi.Router) {
	r.Get("/installs/{id}/threads", a.listThreads)
	r.Post("/installs/{id}/threads", a.createThread)
	r.Post("/installs/{id}/rounds", a.startRound)
	r.Post("/intents/{id}", a.answerIntent)
	r.Get("/installs/{id}/models", a.installModels)
	r.Put("/installs/{id}/model", a.putInstallModel)
	r.Get("/installs/{id}/storage", a.installStorage)
	r.Get("/admin/models", a.adminModels)
	a.accessRoutes(r)
}

// installStorage: what this install keeps, how long, and that it is not the
// company's document archive — said where people upload (AM4).
func (a *API) installStorage(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	ctx := r.Context()
	o, err := a.store.OrgSettingsFor(ctx, orgOr(in.OrgID))
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	use, err := a.store.StorageFor(ctx, in.ID, o.StorageWarnMB)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	years := o.RetentionYears
	if years <= 0 {
		years = DefaultRetentionYears
	}
	writeJSON(w, http.StatusOK, map[string]any{"storage": use, "retention_years": years, "retention": RetentionText(years),
		"not_archive": "ที่นี่ไม่ใช่ที่เก็บเอกสารของบริษัท — ต้นฉบับยังต้องเก็บตามที่บริษัทเก็บอยู่ ระบบเก็บสำเนาและหลักฐานว่าตัวเลขคำนวณจากไฟล์ใด " +
			"อย่าทิ้งใบกำกับภาษีตัวจริงเพราะมีสแกนอยู่ที่นี่",
		"personal_delete": "ถ้าลบบัญชี แชทและการตั้งค่าของคุณจะถูกลบ แต่ไฟล์ของรอบที่สำเร็จเป็นหลักฐานทางบัญชีขององค์กร เก็บต่อตามระยะที่องค์กรกำหนด " +
			"การลบก่อนกำหนดเป็นการตัดสินใจของผู้ดูแล"})
}
