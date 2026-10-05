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
// people who keep one thread per period. AM10 (chats.go): a chat can be
// named, pinned and deleted — deleted means off the list, never destroyed:
// its rounds, files and remembered answers stay. Deleting a chat is still
// never the way to start fresh; the chip says so.

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
	// AM10: pinned chats come first; Named says the title is the person's own;
	// Period is the latest round's ("2026-09"), what an unnamed chat is called by.
	PinnedAt *time.Time `json:"pinned_at"`
	Named    bool       `json:"named"`
	Period   string     `json:"period,omitempty"`
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

// ListThreads lists an install's chats that were not deleted: pinned ones
// first, then the newest activity first.
func (s *Store) ListThreads(ctx context.Context, in Install) ([]Thread, error) {
	rows, err := s.Pool.Query(ctx, `
			WITH t AS (
				SELECT NULL::uuid AS id, i.created_at, i.first_chat_title AS title, i.first_chat_pinned_at AS pinned_at
				FROM agent_market.agent_installs i WHERE i.id = $1 AND i.first_chat_deleted_at IS NULL
				UNION ALL
				SELECT id, created_at, title, pinned_at FROM agent_market.agent_threads WHERE install_id = $1 AND deleted_at IS NULL
			)
			SELECT t.id, t.created_at, t.title, t.pinned_at,
				COALESCE((SELECT max(i.created_at) FROM agent_market.agent_thread_items i
					WHERE i.install_id = $1 AND i.thread_id IS NOT DISTINCT FROM t.id), t.created_at),
				(SELECT count(*) FROM agent_market.agent_runs r
					WHERE r.install_id = $1 AND r.kind = 'run' AND r.thread_id IS NOT DISTINCT FROM t.id)::int,
				(SELECT r.period FROM agent_market.agent_runs r
					WHERE r.install_id = $1 AND r.kind = 'run' AND r.thread_id IS NOT DISTINCT FROM t.id AND r.period IS NOT NULL
					ORDER BY r.created_at DESC LIMIT 1),
				EXISTS (SELECT 1 FROM agent_market.agent_runs r
					WHERE r.install_id = $1 AND r.thread_id IS NOT DISTINCT FROM t.id AND r.status = 'awaiting_input')
			FROM t ORDER BY t.pinned_at DESC NULLS LAST, 5 DESC`, in.ID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Thread{}
	for rows.Next() {
		var t Thread
		var title, period *string
		if err := rows.Scan(&t.ID, &t.CreatedAt, &title, &t.PinnedAt, &t.LastAt, &t.Rounds, &period, &t.Waiting); err != nil {
			return nil, err
		}
		if period != nil {
			t.Period = *period
		}
		if title != nil && strings.TrimSpace(*title) != "" {
			t.Title, t.Named = *title, true
		}
		out = append(out, t)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	// A chat a person did not name is named for what it is: the period of its
	// latest round ("กันยายน 2569"); before any round, "แชทแรก" or when it began
	// ("แชทใหม่ · 28 ก.ย. 2569", with the time when two began the same day).
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
	sameDay, samePeriod := map[string]int{}, map[string]int{}
	for _, t := range out {
		sameDay[name(t, false)]++
		if !t.Named && t.Period != "" {
			samePeriod[t.Period]++
		}
	}
	for i := range out {
		if out[i].Named {
			continue
		}
		if out[i].Period != "" {
			// two chats of one period are told apart by when they began
			out[i].Title = PeriodLabel(out[i].Period)
			if samePeriod[out[i].Period] > 1 {
				out[i].Title += " · " + name(out[i], sameDay[name(out[i], false)] > 1)
			}
			continue
		}
		out[i].Title = name(out[i], sameDay[name(out[i], false)] > 1)
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
	Status    string      `json:"status"` // open | chosen
	Choice    string      `json:"choice,omitempty"`
	Text      string      `json:"text,omitempty"` // what the person typed
	Files     []FileRef   `json:"files,omitempty"`
	ItemID    string      `json:"item_id"`
	LastRound IntentRound `json:"last_round"`
	Fresh     bool        `json:"fresh,omitempty"`
	RunID     string      `json:"run_id,omitempty"`
	Note      PersonText  `json:"note,omitzero"`
}

// IntentRound is the round a chip asks about.
type IntentRound struct {
	RunID       string     `json:"run_id"`
	PeriodLabel PersonText `json:"period_label"`
}

func (a *API) intentChip(ctx context.Context, tx pgx.Tx, in Install, c middleware.Caller, item ThreadItem, last Run,
	text string, files []File, o roundOpts) error {
	p := IntentPayload{Status: "open", Text: text, ItemID: item.ID.String(), Fresh: wantsFreshStart(text),
		LastRound: IntentRound{RunID: last.ID.String(), PeriodLabel: Text("%s", Name(periodOf(last)))}}
	if len(files) > 0 {
		p.Files = FileRefs(files)
	}
	// The person's message waits on the chip (the files stay unused until then).
	if err := a.store.PatchItem(ctx, tx, item.ID, ItemPatch{Status: "handled", HandledAs: "intent"}); err != nil {
		return err
	}
	_, _, err := a.store.AddItemIn(ctx, tx, in.ID, o.thread, c.TenantUUID, c.UserID, nil, p, nil)
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
		return nil, a.threadOf(w, r, in, nil)
	}
	id, err := uuid.Parse(v)
	if err != nil {
		badRequest(w, "BAD_THREAD")
		return nil, false
	}
	return &id, a.threadOf(w, r, in, &id)
}

// threadOf checks a thread named in a request belongs to the install and
// was not deleted (AM10: a deleted chat answers THREAD_GONE — its run pages
// are reached from the agent's dashboard, not through the chat).
func (a *API) threadOf(w http.ResponseWriter, r *http.Request, in Install, thread *uuid.UUID) bool {
	if thread != nil {
		ok, err := a.store.ThreadOwned(r.Context(), in.ID, *thread)
		if err != nil {
			a.fail(w, err, "thread")
			return false
		}
		if !ok {
			a.fail(w, ErrNotFound, "thread")
			return false
		}
	}
	gone, err := a.store.ThreadDeleted(r.Context(), in.ID, thread)
	if err != nil {
		a.fail(w, err, "thread")
		return false
	}
	if gone {
		say(w, http.StatusNotFound, "THREAD_GONE")
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
	if _, _, err := a.store.AddItemIn(ctx, tx, in.ID, &id, c.TenantUUID, c.UserID, nil, GreetingFor(m), nil); err != nil {
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
	item, _, err := a.store.AddItemIn(ctx, a.store.Pool, in.ID, body.ThreadID, c.TenantUUID, c.UserID, nil,
		Notice{Text: Text("เริ่มรอบใหม่แล้ว — ส่งไฟล์ของรอบนี้ได้เลย ใช้ค่าที่ตั้งไว้และคำตอบที่จำไว้ต่อ " +
			"ส่วนไฟล์ ตัวเลข และคำตอบของรอบก่อนไม่ถูกนำมาใช้"), RoundStart: true, Tone: "success"}, nil)
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
	patch := ItemPatch{Status: "chosen", Choice: body.Choice}
	o := roundOpts{thread: item.ThreadID, newRound: true}
	switch body.Choice {
	case "new_round":
		var ids []uuid.UUID
		for _, f := range p.Files {
			fid := f.ID
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
		patch.RunID = run.ID.String()
	case "about_last":
		last, err := a.store.LatestRunIn(ctx, in.ID, item.ThreadID)
		if err != nil {
			a.fail(w, err, "run")
			return
		}
		if strings.TrimSpace(p.Text) != "" {
			job, err := a.replyJob(ctx, tx, in, c, source, p.Text, &last.ID, item.ThreadID, nil)
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
			note := Text("ไฟล์ที่แนบมาไม่ได้ใส่ในรอบที่แล้ว เพราะรอบนั้นส่งตัวเลขไปแล้ว — ไฟล์ที่ไม่ได้ใช้ในรอบใดจะเก็บไว้ 30 วัน " +
				"ถ้าจะใช้ ให้กด “เริ่มรอบใหม่”")
			patch.Note = &note
		} else if strings.TrimSpace(p.Text) == "" {
			note := Text("พิมพ์คำถามเกี่ยวกับรอบที่แล้วได้เลย")
			patch.Note = &note
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
	var started any
	if patch.RunID != "" {
		started = patch.RunID
	}
	writeJSON(w, http.StatusOK, map[string]any{"changed": true, "choice": body.Choice, "run_id": started})
}

// replyJob queues a small job that answers a typed message about a round
// (a question about its figures, a file in another form). AM6: a question
// asked with a knowledge base carries what was found — on this job only.
func (a *API) replyJob(ctx context.Context, q Querier, in Install, c middleware.Caller, itemID uuid.UUID, text string,
	parent *uuid.UUID, thread *uuid.UUID, k *KnowledgeContext) (Run, error) {
	p := map[string]any{"item_id": itemID, "text": text}
	if k != nil {
		p["knowledge"] = k
	}
	params, _ := json.Marshal(p)
	job, _, err := a.store.CreateRun(ctx, q, Run{InstallID: in.ID, ParentRunID: parent, TenantID: c.TenantUUID,
		UserID: c.UserID, Kind: KindReply, Message: text, Params: params, DefinitionVersion: in.DefinitionVersion,
		Checksum: in.Checksum, BindingVersion: in.BindingVersion, ThreadID: thread}, "")
	return job, err
}

// roundRoutes mounts AM4's routes (inside the allowed group).
func (a *API) roundRoutes(r chi.Router) {
	r.Get("/installs/{id}/threads", a.listThreads)
	r.Post("/installs/{id}/threads", a.createThread)
	a.chatRoutes(r) // AM10: rename, pin, delete
	r.Post("/installs/{id}/rounds", a.startRound)
	r.Post("/intents/{id}", a.answerIntent)
	r.Get("/installs/{id}/models", a.installModels)
	r.Put("/installs/{id}/model", a.putInstallModel)
	r.Get("/installs/{id}/storage", a.installStorage)
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

// ── a failed round, tried again (AM5) ────────────────────────────────────
//
// A failed round was a dead end: typing "ทำรอบใหม่" started a round with no
// files at all. "ลองใหม่ด้วยไฟล์เดิม" starts the same round again — the files
// it had, the instruction it was given, the answers the person had already
// clicked and the thread — as a new run, with the model the install has now
// (AM6: someone who changed the model because the round failed gets the model
// they chose). The failed run stays as it ended (its steps are what a problem
// report shows); its files move to the new run, which is where they are
// needed and where retention now counts them from.

func (a *API) retry(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	ctx := r.Context()
	failed, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	in, err := a.store.GetInstall(ctx, c.TenantUUID, c.UserID, failed.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if !a.agentGate(w, r, in.Slug, false) {
		return
	}
	idem := "retry:" + failed.ID.String()
	// a second click: the round the first click started
	if again, err := a.store.RunByIdempotency(ctx, in.ID, idem); err == nil {
		a.writeRetry(w, ctx, again)
		return
	}
	// AM6: a round that finished with its gate open did not fail — the same
	// files give the same difference, and a second round of them is charged again
	if failed.Kind != KindRun || failed.Status != RunFailed || failed.Outcome() == OutcomeCompletedOpen {
		say(w, http.StatusConflict, "NOT_FAILED")
		return
	}
	files, err := a.store.RunFiles(ctx, failed.ID, "active")
	if err != nil {
		a.fail(w, err, "file")
		return
	}
	if len(files) == 0 {
		say(w, http.StatusConflict, "NOTHING_TO_RETRY", "days", "7")
		return
	}
	if latest, err := a.store.LatestRunIn(ctx, in.ID, failed.ThreadID); err == nil && !IsTerminal(latest.Status) {
		say(w, http.StatusConflict, "ROUND_IN_PROGRESS")
		return
	}
	def, err := a.store.DefinitionByID(ctx, in.DefinitionID)
	if err != nil {
		a.fail(w, err, "agent")
		return
	}
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := a.store.SupersedeInstallCards(ctx, tx, in.ID, "superseded"); err != nil {
		a.fail(w, err, "card")
		return
	}
	params, _ := json.Marshal(map[string]any{"retry_of": failed.ID})
	model := in.ModelChoice
	if model == "" {
		model = ModelAuto
	}
	// the install's current version: a retry after an upgrade runs the upgraded agent
	run, _, err := a.store.CreateRun(ctx, tx, Run{InstallID: in.ID, TenantID: c.TenantUUID, UserID: c.UserID, Kind: KindRun,
		Message: failed.Message, Params: params, DefinitionVersion: def.Version, Checksum: def.Checksum, EngineChecksum: def.EngineChecksum,
		BindingVersion: in.BindingVersion, Formats: failed.Formats, Answers: failed.Answers, ThreadID: failed.ThreadID,
		ModelChoice: &model}, idem)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_files SET run_id = $2 WHERE run_id = $1 AND status = 'active'`, failed.ID, run.ID); err != nil {
		a.fail(w, err, "file")
		return
	}
	rid := run.ID
	names := make([]string, 0, len(files))
	for _, f := range files {
		names = append(names, f.Name)
	}
	if _, _, err := a.store.AddItem(ctx, tx, in.ID, c.TenantUUID, c.UserID, &rid,
		Notice{Text: Text("ลองรอบนี้ใหม่ด้วยไฟล์เดิม %s ไฟล์: %s", Count(len(names)), FileNames(names)),
			RunID: run.ID.String(), RetryOf: failed.ID.String()}, nil); err != nil {
		a.fail(w, err, "run")
		return
	}
	if _, _, err := a.store.AddItem(ctx, tx, in.ID, c.TenantUUID, c.UserID, &rid, QueuedProgress(run), nil); err != nil {
		a.fail(w, err, "run")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "run")
		return
	}
	a.writeRetry(w, ctx, run)
}

func (a *API) writeRetry(w http.ResponseWriter, ctx context.Context, run Run) {
	out := map[string]any{"run": run}
	if it, err := a.store.ItemFor(ctx, run.InstallID, "progress", "run_id", run.ID.String()); err == nil {
		out["item"] = it
	}
	writeJSON(w, http.StatusOK, out)
}

// RunByIdempotency finds the run an idempotency key already made.
func (s *Store) RunByIdempotency(ctx context.Context, install uuid.UUID, key string) (Run, error) {
	x, err := scanRun(s.Pool.QueryRow(ctx, `SELECT `+runCols+` FROM agent_market.agent_runs
		WHERE install_id = $1 AND idempotency_key = $2`, install, key))
	if errors.Is(err, pgx.ErrNoRows) {
		return x, ErrNotFound
	}
	return x, err
}

// ── a document that was left out, brought back (AM5) ─────────────────────
//
// A round keeps the list of what it left out ("เอกสารที่ยังไม่ได้รวม",
// computed.json not_included), and each entry says what bringing the
// document back takes: answers to give in place of the ones that left it out
// (recorded as printed, with an exception), or answers to take back so that
// the document's question is asked again. The control plane applies exactly
// that to the round's answers and lets the same round compute again: the
// files come out as the next version, and the list is the new round's.
// Which answers, and what they mean, is the package's; nothing here knows
// what an invoice is.

type restoreSpec struct {
	Set map[string]struct {
		Value json.RawMessage `json:"value"`
		Label string          `json:"label"`
		Code  string          `json:"code"`
	} `json:"set"`
	Drop []string `json:"drop"`
}

type leftOut struct {
	DocKey     string      `json:"doc_key"`
	Name       string      `json:"name"`
	CanRestore bool        `json:"can_restore"`
	Restore    restoreSpec `json:"restore"`
}

// leftOutOf reads the round's list from its newest computed table.
func (a *API) leftOutOf(ctx context.Context, run Run) ([]leftOut, error) {
	var c struct {
		NotIncluded []leftOut `json:"not_included"`
	}
	if err := a.decodeComputed(ctx, run, &c); err != nil {
		return nil, err
	}
	return c.NotIncluded, nil
}

// decodeComputed reads the round's newest computed table into v.
func (a *API) decodeComputed(ctx context.Context, run Run, v any) error {
	var key string
	err := a.store.Pool.QueryRow(ctx, `SELECT output->>'computed_key' FROM agent_market.agent_run_steps
		WHERE run_id = $1 AND name = 'compute' AND status = 'succeeded' ORDER BY seq DESC LIMIT 1`, run.ID).Scan(&key)
	if err != nil || key == "" {
		return ErrNotFound
	}
	body, _, _, err := a.files.Download(ctx, key)
	if err != nil {
		return err
	}
	defer body.Close()
	return json.NewDecoder(io.LimitReader(body, 64<<20)).Decode(v)
}

// computeAgain lets a finished round compute again with what was just
// recorded on it (a document brought back, a receipt confirmed): the thread
// says what changed, and the round's progress card is working again. false:
// the round could not be resumed (it was stopped).
func (a *API) computeAgain(ctx context.Context, tx pgx.Tx, in Install, c middleware.Caller, run Run, n Notice) (bool, error) {
	rid := run.ID
	n.RunID = run.ID.String()
	if _, _, err := a.store.AddItem(ctx, tx, in.ID, c.TenantUUID, c.UserID, &rid, n, nil); err != nil {
		return false, err
	}
	resumed, err := a.store.Resume(ctx, tx, run.ID)
	if err != nil || !resumed {
		return false, err
	}
	if it, err := a.store.ItemFor(ctx, run.InstallID, "progress", "run_id", run.ID.String()); err == nil {
		_ = a.store.PatchItem(ctx, tx, it.ID, ItemPatch{Status: RunQueued, Outcome: RunQueued})
	}
	return true, nil
}

func (a *API) restore(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var req struct {
		DocKey string `json:"doc_key"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&req); err != nil || strings.TrimSpace(req.DocKey) == "" {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	run, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	in, err := a.store.GetInstall(ctx, c.TenantUUID, c.UserID, run.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if !a.agentGate(w, r, in.Slug, false) {
		return
	}
	if run.Kind != KindRun || run.Status != RunSucceeded {
		say(w, http.StatusConflict, "ROUND_NOT_DONE")
		return
	}
	list, err := a.leftOutOf(ctx, run)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	var doc *leftOut
	for i := range list {
		if list[i].DocKey == req.DocKey {
			doc = &list[i]
		}
	}
	if doc == nil {
		say(w, http.StatusNotFound, "NOT_LEFT_OUT")
		return
	}
	if !doc.CanRestore {
		say(w, http.StatusConflict, "ALREADY_INCLUDED")
		return
	}
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	defer func() { _ = tx.Rollback(ctx) }()
	drop := map[string]bool{}
	for _, k := range doc.Restore.Drop {
		drop[k] = true
	}
	if len(drop) > 0 {
		keys := make([]string, 0, len(drop))
		for k := range drop {
			keys = append(keys, k)
		}
		if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_runs SET answers = answers - $2::text[], updated_at = now() WHERE id = $1`,
			run.ID, keys); err != nil {
			a.fail(w, err, "run")
			return
		}
		// the question those answers belonged to may be asked again
		cards, err := a.store.RunCards(ctx, run.ID)
		if err != nil {
			a.fail(w, err, "card")
			return
		}
		for _, cd := range cards {
			var qs []Question
			_ = json.Unmarshal(cd.Questions, &qs)
			mine := false
			for _, q := range qs {
				mine = mine || drop[q.Key]
			}
			if !mine || cd.Status == "superseded" {
				continue
			}
			if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_run_clarifications SET status = 'superseded' WHERE id = $1`, cd.ID); err != nil {
				a.fail(w, err, "card")
				return
			}
			cd.Status = "superseded"
			if err := a.store.SyncCardItem(ctx, tx, cd); err != nil && !errors.Is(err, ErrNotFound) {
				a.fail(w, err, "card")
				return
			}
		}
	}
	set := map[string]Answer{}
	for k, v := range doc.Restore.Set {
		set[k] = Answer{Value: v.Value, Label: v.Label, Moment: "preflight", Via: "card", Code: v.Code}
	}
	if err := a.store.MergeAnswers(ctx, tx, run.ID, set); err != nil {
		a.fail(w, err, "run")
		return
	}
	asks := len(drop) > 0 || len(set) == 0
	text := Text("นำ “%s” กลับมารวมในรอบนี้แล้ว กำลังคำนวณใหม่ ไฟล์ชุดใหม่จะเป็นรุ่นถัดไป เอกสารนี้บันทึกตามที่พิมพ์ พร้อมข้อยกเว้นให้ผู้ตรวจดู",
		Typed(doc.Name, 160))
	if asks {
		text = Text("นำ “%s” กลับมาแล้ว ต้องตอบคำถามของเอกสารนี้ก่อนจึงจะรวมได้ คำถามจะขึ้นด้านล่าง", Typed(doc.Name, 160))
	}
	resumed, err := a.computeAgain(ctx, tx, in, c, run, Notice{Text: text, Restored: doc.DocKey})
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if !resumed {
		say(w, http.StatusConflict, "ROUND_NOT_DONE")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "run")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"changed": true, "asks": asks, "doc_key": doc.DocKey, "name": doc.Name})
}

// ── a receipt confirmed in this system (AM6) ─────────────────────────────
//
// A company that received goods but has no receipt file for them (it keeps
// its receipts in no system, or the file did not come) confirms the receipt
// of a PO line here. The round's computed table offers the lines it may
// (receipt_offers: goods tested two ways for want of a receipt); the answer
// is recorded on the round as given by the signed-in person — an action, not
// a card, so it names them itself (Answer.By) — and the same round computes
// again the way a restored document does. Which lines, and what a receipt
// changes, is the package's.

type receiptOffer struct {
	Key         string          `json:"key"`
	PO          string          `json:"po"`
	LineNo      json.RawMessage `json:"line_no"`
	Unit        string          `json:"unit"`
	Description string          `json:"description"`
}

// receiptQuantity reads what was received: "all", or a number of at least 0
// with at most six decimals (as the package reads {"typed": …}).
func receiptQuantity(raw json.RawMessage) (json.RawMessage, string, bool) {
	var s string
	if json.Unmarshal(raw, &s) == nil && strings.TrimSpace(s) == "all" {
		return json.RawMessage(`"all"`), "", true
	}
	v, whole, decimals, ok := writtenAs(raw, false)
	if !ok || v < 0 || len(decimals) > 6 {
		return nil, "", false
	}
	typed := whole
	if decimals != "" {
		typed += "." + decimals
	}
	return mustJSON(map[string]string{"typed": typed}), typed, true
}

func (a *API) receipts(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var req struct {
		Key      string          `json:"key"`
		Quantity json.RawMessage `json:"quantity"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&req); err != nil || strings.TrimSpace(req.Key) == "" {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	run, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	in, err := a.store.GetInstall(ctx, c.TenantUUID, c.UserID, run.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if !a.agentGate(w, r, in.Slug, false) {
		return
	}
	switch {
	case !IsTerminal(run.Status):
		say(w, http.StatusConflict, "RECEIPT_NOT_OPEN", "why", "รอบนี้ยังทำอยู่", "next", "รอให้รอบนี้เสร็จก่อน แล้วค่อยยืนยันการรับของ")
		return
	case run.Kind != KindRun || run.Status != RunSucceeded:
		say(w, http.StatusConflict, "RECEIPT_NOT_OPEN", "why", "รอบนี้ไม่สำเร็จ", "next", "ส่งไฟล์เพื่อเริ่มรอบใหม่ แล้วยืนยันการรับของในรอบนั้น")
		return
	}
	var computed struct {
		Offers []receiptOffer `json:"receipt_offers"`
	}
	if err := a.decodeComputed(ctx, run, &computed); errors.Is(err, ErrNotFound) {
		say(w, http.StatusNotFound, "NOT_COMPUTED")
		return
	} else if err != nil {
		a.fail(w, err, "run")
		return
	}
	if len(computed.Offers) == 0 {
		say(w, http.StatusConflict, "RECEIPT_NOT_OPEN", "why", "รอบนี้ไม่มีรายการที่ยืนยันการรับของในระบบได้",
			"next", "ส่งใบรับของเป็นไฟล์ในรอบใหม่แทน")
		return
	}
	var offer *receiptOffer
	for i := range computed.Offers {
		if computed.Offers[i].Key == req.Key {
			offer = &computed.Offers[i]
		}
	}
	if offer == nil {
		say(w, http.StatusNotFound, "NOT_OFFERED")
		return
	}
	value, typed, ok := receiptQuantity(req.Quantity)
	if !ok {
		say(w, http.StatusBadRequest, "BAD_QUANTITY")
		return
	}
	var line any
	_ = json.Unmarshal(offer.LineNo, &line)
	po, lineNo := Name(offer.PO), Name(strings.TrimSpace(fmt.Sprint(line)))
	label := Text("รับครบตามใบสั่งซื้อ")
	text := Text("ยืนยันแล้วว่าได้รับของตาม %s บรรทัด %s ครบตามที่สั่ง กำลังคำนวณรอบนี้ใหม่ ไฟล์ชุดใหม่จะเป็นรุ่นถัดไป", po, lineNo)
	if typed != "" {
		got := Join(" ", Text("%s", Name(typed)), LabelFromPackage(offer.Unit, NoText))
		label = Text("รับ %s", Words(got))
		text = Text("ยืนยันแล้วว่าได้รับของตาม %s บรรทัด %s จำนวน %s กำลังคำนวณรอบนี้ใหม่ ไฟล์ชุดใหม่จะเป็นรุ่นถัดไป", po, lineNo, Words(got))
	}
	code, _, _ := strings.Cut(req.Key, ":")
	by := c.UserID
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if err := a.store.MergeAnswers(ctx, tx, run.ID, map[string]Answer{req.Key: {Value: value, Label: label.String(), Moment: "run",
		Via: "action", Code: code, By: &by}}); err != nil {
		a.fail(w, err, "run")
		return
	}
	resumed, err := a.computeAgain(ctx, tx, in, c, run, Notice{Text: text})
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	if !resumed {
		say(w, http.StatusConflict, "RECEIPT_NOT_OPEN", "why", "รอบนี้ถูกหยุดไว้", "next", "ส่งไฟล์เพื่อเริ่มรอบใหม่ แล้วยืนยันการรับของในรอบนั้น")
		return
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "run")
		return
	}
	if run, err = a.store.RunByID(ctx, run.ID); err != nil {
		a.fail(w, err, "run")
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"run": run})
}
