package runner

// AM6 sections B and C end to end: a reminder on the invoice ↔ PO agent, the
// real runner and the real sandbox image, the control plane's handlers
// (AGENT_MARKET_IT=1). Nothing runs until the person sends the files.

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
)

const poSamples = "backend/agent_market/packages/po_matching/sample-data"

var poSampleFiles = []string{"ใบสั่งซื้อ_2569-09.xlsx", "ใบรับของ_2569-09.xlsx", "ใบกำกับภาษี_2569-09.xlsx"}

var itICT = time.FixedZone("ICT", 7*3600)

func (e *itEnv) setupPOInstall() am.Install {
	e.t.Helper()
	ctx := context.Background()
	if err := e.runner("reg").Register(ctx); err != nil {
		e.t.Fatal(err)
	}
	def, err := e.store.DefinitionBySlug(ctx, "sran.invoice-po-matching")
	if err != nil {
		e.t.Fatalf("the invoice ↔ PO agent is not registered by the runner: %v", err)
	}
	tx, _ := e.store.Pool.Begin(ctx)
	in, _, err := e.store.CreateInstall(ctx, tx, am.Install{TenantID: uuid.New(), UserID: uuid.New(), DefinitionID: def.ID,
		Slug: def.Slug, DefinitionVersion: def.Version, Checksum: def.Checksum, Title: def.Name, BindingVersion: 1,
		BudgetCredits: 3_000_000}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	for k, v := range map[string]string{"formats": `["xlsx"]`, "company": `"บริษัท สยามตัวอย่างการค้า จำกัด"`, "ap_gr_practice": `"yes"`} {
		_ = e.store.SetDefault(ctx, tx, in.ID, am.Default{Key: k, Value: json.RawMessage(v), Source: "install"})
	}
	if err := tx.Commit(ctx); err != nil {
		e.t.Fatal(err)
	}
	e.install = in
	return in
}

type itMail struct {
	mu   sync.Mutex
	sent []am.MailMessage
}

func (m *itMail) Send(_ context.Context, msg am.MailMessage) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.sent = append(m.sent, msg)
	return nil
}

// apiWith is the control plane signed in as the install's owner, with a mail
// connector or none.
func (e *itEnv) apiWith(mail am.MailConnector) *httptest.Server {
	e.t.Helper()
	cfg := am.ConfigFromEnv()
	cfg.Enabled = true
	cfg.AllowedEmails = nil
	cfg.AdminEmails = []string{"am6-it@example.test"}
	cfg.Mail = mail
	api := am.NewAPIWithStore(cfg, e.store, e.files, slog.Default())
	r := chi.NewRouter()
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			c := middleware.Caller{UserID: e.install.UserID, TenantUUID: e.install.TenantID, Email: "am6-it@example.test"}
			next.ServeHTTP(w, req.WithContext(middleware.WithCaller(req.Context(), c)))
		})
	})
	r.Route("/agent-market", api.Routes)
	srv := httptest.NewServer(r)
	e.t.Cleanup(srv.Close)
	return srv
}

// stagePO stores the three sample files as uploads the person has not sent yet.
func (e *itEnv) stagePO() []string {
	e.t.Helper()
	ctx := context.Background()
	in := e.install
	var ids []string
	for _, name := range poSampleFiles {
		data, err := os.ReadFile(filepath.Join(e.root, filepath.FromSlash(poSamples), name))
		if err != nil {
			e.t.Fatal(err)
		}
		id := uuid.New()
		key := am.UploadKey(in.TenantID, in.ID, id, name)
		if err := e.files.Upload(ctx, key, strings.NewReader(string(data)), "", int64(len(data))); err != nil {
			e.t.Fatal(err)
		}
		if _, err := e.store.CreateFile(ctx, e.store.Pool, am.File{ID: id, TenantID: in.TenantID, UserID: in.UserID, InstallID: in.ID,
			Name: name, Size: int64(len(data)), SHA256: fmt.Sprintf("%x", sha(data)), StorageKey: key, Status: "staged"}); err != nil {
			e.t.Fatal(err)
		}
		ids = append(ids, id.String())
	}
	return ids
}

func (e *itEnv) count(sql string, args ...any) int {
	e.t.Helper()
	var n int
	if err := e.store.Pool.QueryRow(context.Background(), sql, args...).Scan(&n); err != nil {
		e.t.Fatal(err)
	}
	return n
}

func (e *itEnv) charged(run uuid.UUID) float64 {
	e.t.Helper()
	var v float64
	_ = e.store.Pool.QueryRow(context.Background(), `SELECT COALESCE(sum(credits), 0)::float8 FROM agent_market.agent_budget_ledger
		WHERE run_id = $1 AND voided_at IS NULL`, run).Scan(&v)
	return v
}

func (e *itEnv) reminderOn(srv *httptest.Server, due time.Time) string {
	e.t.Helper()
	code, body := post(e.t, srv, "/installs/"+e.install.ID.String()+"/reminders", map[string]any{"label": "ตรวจใบแจ้งหนี้",
		"rule": map[string]any{"every": "month", "day": 5, "time": "09:00"}})
	if code != 201 {
		e.t.Fatalf("a reminder: %d %v", code, body)
	}
	id := body["reminder"].(map[string]any)["id"].(string)
	if _, err := e.store.Pool.Exec(context.Background(), `UPDATE agent_market.reminders SET next_due_at = $2 WHERE id = $1`, id, due); err != nil {
		e.t.Fatal(err)
	}
	return id
}

func TestAReminderOnThePOAgentOpensAWaitingRoundThatRunsOnlyWhenTheFilesArrive(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.setupPOInstall()
	srv := e.apiWith(nil)
	e.reminderOn(srv, time.Now().Add(-time.Minute))
	r := e.runner("r1")
	p, err := r.RemindersOnce(ctx)
	if err != nil || p.Fired != 1 {
		t.Fatalf("the minute pass: %+v %v", p, err)
	}
	runs, _ := e.store.ListRuns(ctx, in.ID, 10)
	if len(runs) != 1 || runs[0].Status != am.RunAwaitingInput {
		t.Fatalf("one round waiting for files: %+v", runs)
	}
	run := runs[0]
	cards, _ := e.store.RunCards(ctx, run.ID)
	if len(cards) != 1 || cards[0].Kind != "request_files" {
		t.Fatalf("a request_files card: %+v", cards)
	}
	var qs []am.Question
	_ = json.Unmarshal(cards[0].Questions, &qs)
	var req []map[string]any
	_ = json.Unmarshal(qs[0].Request, &req)
	roles := map[string]bool{}
	for _, x := range req {
		roles[fmt.Sprint(x["role"])] = x["required"] == true
	}
	if len(req) != 3 || !roles["po"] || !roles["invoice"] || roles["gr"] {
		t.Fatalf("the card lists the agent's inputs as the package declares them: %v", req)
	}
	t.Logf("the card asks for: %v", req)
	if n := e.count(`SELECT count(*) FROM agent_market.notifications WHERE install_id = $1 AND kind = 'reminder_due'`, in.ID); n != 1 {
		t.Fatalf("one notification: %d", n)
	}
	var title, body string
	_ = e.store.Pool.QueryRow(ctx, `SELECT title, body FROM agent_market.notifications WHERE install_id = $1`, in.ID).Scan(&title, &body)
	if !am.ReadsForPerson(title) || !am.ReadsForPerson(body) || !strings.HasPrefix(body, "ต้องใช้ 2 ไฟล์นี้: ") {
		t.Fatalf("the notification: %q · %q", title, body)
	}
	t.Logf("the notification: %s · %s", title, body)
	if p, err := r.RemindersOnce(ctx); err != nil || p != (am.ReminderPass{}) {
		t.Fatalf("a second pass: %+v %v", p, err)
	}
	if n := e.count(`SELECT count(*) FROM agent_market.agent_runs WHERE install_id = $1`, in.ID); n != 1 {
		t.Fatalf("rounds after the second pass: %d", n)
	}
	if len(e.steps(run.ID)) != 0 || e.charged(run.ID) != 0 {
		t.Fatal("the reminder's round did work or was charged before a person sent anything")
	}
	// the person sends the three sample files on the card (the web sends them as a message)
	code, out := post(t, srv, "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "", "file_ids": e.stagePO(),
		"client_id": "am6-it-files"})
	if code != 200 || out["route"] != "resumed" || out["run_id"] != run.ID.String() {
		t.Fatalf("the files resume the round: %d %v", code, out)
	}
	got := e.drive(r, run.ID, 5*time.Minute, func(x am.Run) bool { return terminal(x) || parked(x) })
	if got.Status != am.RunSucceeded {
		cs, _ := e.store.RunCards(ctx, run.ID)
		for _, c := range cs {
			t.Logf("card %s %s: %s", c.Kind, c.Status, c.Questions)
		}
		t.Fatalf("the round delivers: %s %s", got.Status, got.StatusReason)
	}
	// the files are named after the period the package read from them, never a period the reminder guessed
	arts0, _ := e.store.RunArtifacts(ctx, run.ID)
	for _, a := range arts0 {
		if !strings.Contains(a.Name, "2026-09") {
			t.Fatalf("a delivered file is named after another period: %s", a.Name)
		}
	}
	if !got.Delivered() || got.CreditsSpent <= 0 || e.charged(run.ID) != got.CreditsSpent {
		t.Fatalf("charged after delivery: spent %v, ledger %v", got.CreditsSpent, e.charged(run.ID))
	}
	t.Logf("delivered, charged %v credits", got.CreditsSpent)
	// the e-mail draft: the round's artifacts, no recipient
	code, out = post(t, srv, "/runs/"+run.ID.String()+"/email-draft", nil)
	if code != 200 {
		t.Fatalf("draft: %d %v", code, out)
	}
	d := out["draft"].(map[string]any)
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	var want, have []string
	for _, a := range arts {
		want = append(want, a.ID.String())
	}
	for _, a := range d["attachments"].([]any) {
		have = append(have, a.(map[string]any)["artifact_id"].(string))
	}
	if len(arts) == 0 || strings.Join(want, ",") != strings.Join(have, ",") {
		t.Fatalf("attachments %v, the round's artifacts %v", have, want)
	}
	for _, k := range []string{"to", "cc", "recipients"} {
		if _, ok := d[k]; ok {
			t.Fatalf("the draft carries %q", k)
		}
	}
	if b := d["body"].(string); strings.Contains(b, "@") || !am.ReadsForPerson(strings.ReplaceAll(b, "\n", " ")) {
		t.Fatalf("the body:\n%s", b)
	}
	t.Logf("the draft: %s\n%s", d["subject"], d["body"])
	send := map[string]any{"to": []string{"ap@company.example"}, "body_sha256": d["body_sha256"], "approve": true}
	code, out = post(t, srv, "/email-drafts/"+d["id"].(string)+"/send", send)
	if er, _ := out["error"].(map[string]any); code != 409 || er["code"] != "NO_MAIL_CONNECTOR" {
		t.Fatalf("no connector: %d %v", code, out)
	}
	mail := &itMail{}
	code, out = post(t, e.apiWith(mail), "/email-drafts/"+d["id"].(string)+"/send", send)
	if code != 200 || out["send"].(map[string]any)["status"] != "sent" || len(mail.sent) != 1 ||
		strings.Join(mail.sent[0].To, ",") != "ap@company.example" || len(mail.sent[0].Attachments) != len(arts) {
		t.Fatalf("with a connector and approval: %d %v", code, out)
	}
	var who uuid.UUID
	var email string
	if err := e.store.Pool.QueryRow(ctx, `SELECT approved_by_user_id, approved_by_email FROM agent_market.email_sends WHERE run_id = $1`,
		run.ID).Scan(&who, &email); err != nil || who != in.UserID || email != "am6-it@example.test" {
		t.Fatalf("the send's log: %v %q %v", who, email, err)
	}
}

func TestAReminderWhoseRoundGotNoFileClosesItChargesNothingAndFollowsUpOnce(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.setupPOInstall()
	srv := e.apiWith(nil)
	id := e.reminderOn(srv, time.Date(2026, 10, 5, 9, 0, 0, 0, itICT))
	clock := time.Date(2026, 10, 5, 9, 1, 0, 0, itICT)
	r := e.runner("r1")
	r.Now = func() time.Time { return clock }
	if p, err := r.RemindersOnce(ctx); err != nil || p.Fired != 1 {
		t.Fatalf("fired: %+v %v", p, err)
	}
	first, _ := e.store.LatestRun(ctx, in.ID)
	// three days without a file: one follow-up, then silence
	clock = clock.Add(72*time.Hour + time.Minute)
	if p, _ := r.RemindersOnce(ctx); p.FollowUps != 1 {
		t.Fatalf("the follow-up: %+v", p)
	}
	for i := 0; i < 3; i++ {
		clock = clock.Add(24 * time.Hour)
		if p, _ := r.RemindersOnce(ctx); p.FollowUps != 0 {
			t.Fatalf("a second follow-up: %+v", p)
		}
	}
	// the next month's firing closes the round that got no file and opens the next one
	clock = time.Date(2026, 11, 5, 9, 1, 0, 0, itICT)
	p, err := r.RemindersOnce(ctx)
	if err != nil || p.Cancelled != 1 || p.Fired != 1 {
		t.Fatalf("the next firing: %+v %v", p, err)
	}
	old, _ := e.store.RunByID(ctx, first.ID)
	if old.Status != am.RunCancelled || old.CreditsSpent != 0 || old.CreditsCost != 0 || e.charged(first.ID) != 0 {
		t.Fatalf("the round that got no file: %s %s charged %v cost %v", old.Status, old.StatusReason, old.CreditsSpent, old.CreditsCost)
	}
	next, _ := e.store.LatestRun(ctx, in.ID)
	var period string
	_ = e.store.Pool.QueryRow(ctx, `SELECT fired_period FROM agent_market.reminders WHERE id = $1`, id).Scan(&period)
	if next.ID == first.ID || next.Status != am.RunAwaitingInput || period != "2026-10" {
		t.Fatalf("the next round: %+v (%s)", next, period)
	}
	if n := e.count(`SELECT count(*) FROM agent_market.notifications WHERE reminder_id = $1 AND kind = 'reminder_followup'`, id); n != 1 {
		t.Fatalf("follow-ups: %d", n)
	}
	t.Logf("first round %s %s, next round %s waiting for %s", old.Status, old.StatusReason, next.ID, period)
}

func TestThePOMatchingSelfTestPassesThroughTheRunner(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.setupPOInstall()
	def, _ := e.store.DefinitionByID(ctx, in.DefinitionID)
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Kind: am.KindSelfTest, Message: "ทดสอบกับข้อมูลตัวอย่าง", DefinitionVersion: def.Version, Checksum: def.Checksum,
		EngineChecksum: def.EngineChecksum}, "")
	if err != nil {
		t.Fatal(err)
	}
	got := e.drive(e.runner("r1"), run.ID, 5*time.Minute, func(x am.Run) bool { return terminal(x) || parked(x) })
	cards, _ := e.store.RunCards(ctx, run.ID)
	if got.Status != am.RunSucceeded || len(cards) != 0 {
		t.Fatalf("the self-test: %s %s, %d card(s)", got.Status, got.StatusReason, len(cards))
	}
	// its summary, as the package wrote it, reads as written for a person
	var raw json.RawMessage
	if err := e.store.Pool.QueryRow(ctx, `SELECT payload FROM agent_market.agent_thread_items WHERE run_id = $1 AND kind = 'outputs'
		ORDER BY rev DESC LIMIT 1`, run.ID).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	var p struct {
		Summary map[string]any `json:"summary"`
	}
	_ = json.Unmarshal(raw, &p)
	if len(p.Summary) == 0 {
		t.Fatal("no summary")
	}
	var texts []string
	var walk func(v any)
	walk = func(v any) {
		switch x := v.(type) {
		case string:
			texts = append(texts, x)
		case []any:
			for _, e := range x {
				walk(e)
			}
		case map[string]any:
			for _, e := range x {
				walk(e)
			}
		}
	}
	walk(p.Summary)
	thai := 0
	for _, s := range texts {
		if !strings.ContainsFunc(s, func(c rune) bool { return c >= 0x0E00 && c <= 0x0E7F }) {
			continue
		}
		thai++
		if !am.ReadsForPerson(s) && am.ShownToPerson(s) != s {
			t.Errorf("a sentence of the summary is not for a person as written: %q → %q", s, am.ShownToPerson(s))
		}
	}
	t.Logf("the summary's %d Thai texts read as written for a person", thai)
	// its computed table links every invoice by the matching engine (e3, one run per document)
	var c struct {
		Match struct {
			Links []struct {
				Kind, Document, State string
			} `json:"links"`
		} `json:"match"`
	}
	if err := json.Unmarshal(e.computedOf(run.ID), &c); err != nil {
		t.Fatal(err)
	}
	docs := map[string]int{}
	for _, l := range c.Match.Links {
		if l.Kind == "invoice" {
			docs[l.Document]++
			if l.State != "linked" {
				t.Errorf("a link of %s is %s", l.Document, l.State)
			}
		}
	}
	if len(docs) != 3 {
		t.Fatalf("the invoices linked by e3: %v (%d links)", docs, len(c.Match.Links))
	}
	t.Logf("match links: %d, invoice documents %v", len(c.Match.Links), docs)
}
