package agentmarket

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// testEnv is a control plane on a private database (cloned by testdb, the
// agent_market schema applied by Migrate — the core template is untouched).
type testEnv struct {
	t     *testing.T
	store *Store
	api   *API
	srv   *httptest.Server
	user  middleware.Caller
}

func newEnv(t *testing.T) *testEnv { t.Helper(); return newEnvCfg(t, nil) }

// newEnvCfg is newEnv with the configuration adjusted (AM3: access lists,
// break-glass admins).
func newEnvCfg(t *testing.T, mut func(*Config)) *testEnv {
	t.Helper()
	pool := testdb.Fresh(t)
	if pool == nil {
		return nil
	}
	ctx := context.Background()
	if _, err := Migrate(ctx, pool); err != nil {
		t.Fatal(err)
	}
	files, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	store := NewStore(pool)
	cfg := ConfigFromEnv()
	cfg.Enabled = true
	cfg.StreamPoll = 50 * time.Millisecond
	// AM3: the AM1/AM2 tests install agents they seed themselves, which the
	// organisation has not published — their user is the organisation's admin
	// (a publisher may install an unpublished agent). The AM3 tests sign in
	// as plain members with the X-Test-Email header.
	cfg.AllowedEmails = nil
	cfg.AdminEmails = []string{"am1-test@example.test"}
	if mut != nil {
		mut(&cfg)
	}
	api := NewAPIWithStore(cfg, store, files, slog.Default())
	e := &testEnv{t: t, store: store, api: api,
		user: middleware.Caller{UserID: uuid.New(), TenantUUID: uuid.New(), Email: "am1-test@example.test"}}
	r := chi.NewRouter()
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			c := e.user
			if other := req.Header.Get("X-Test-User"); other != "" {
				c = middleware.Caller{UserID: uuid.MustParse(other), TenantUUID: e.user.TenantUUID, Email: "other@example.test"}
			}
			if email := req.Header.Get("X-Test-Email"); email != "" { // AM3: a colleague signed in as themselves
				c.Email = email
				if req.Header.Get("X-Test-User") == "" {
					c.UserID = uuid.NewSHA1(uuid.NameSpaceURL, []byte(email))
					c.TenantUUID = uuid.NewSHA1(uuid.NameSpaceDNS, []byte(email))
				}
			}
			next.ServeHTTP(w, req.WithContext(middleware.WithCaller(req.Context(), c)))
		})
	})
	r.Route("/agent-market", api.Routes)
	// AM7: the admin app's door — the same handlers, the admin named by the
	// headers admin-ui's proxy sets (chat-edge mounts it behind its admin
	// bearer; the tests call it directly, see adminDo).
	if door, ok := any(api).(interface{ AdminRoutes(chi.Router) }); ok {
		r.Route("/admin-door", door.AdminRoutes)
	}
	e.srv = httptest.NewServer(r)
	t.Cleanup(e.srv.Close)
	return e
}

func (e *testEnv) do(method, path string, body any, hdr ...string) (int, map[string]any) {
	e.t.Helper()
	var rdr io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rdr = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, e.srv.URL+"/agent-market"+path, rdr)
	req.Header.Set("Content-Type", "application/json")
	for i := 0; i+1 < len(hdr); i += 2 {
		req.Header.Set(hdr[i], hdr[i+1])
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

// testAdmin is an admin-ui account as its proxy names it (AM7).
type testAdmin struct{ ID, Email, Name, Role string }

var (
	uiAdmin     = testAdmin{ID: "0b8f5a8e-0000-4000-8000-000000000001", Email: "it-admin@example.test", Name: "IT Admin", Role: "admin"}
	uiPublisher = testAdmin{ID: "0b8f5a8e-0000-4000-8000-000000000002", Email: "finance-lead@example.test", Name: "หัวหน้าบัญชี", Role: "agent_publisher"}
	uiViewer    = testAdmin{ID: "0b8f5a8e-0000-4000-8000-000000000003", Email: "viewer@example.test", Name: "Viewer", Role: "viewer"}
)

// adminDo calls the admin app's door (AdminRoutes) as one admin-ui account.
func (e *testEnv) adminDo(who testAdmin, method, path string, body any) (int, map[string]any) {
	e.t.Helper()
	var rdr io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rdr = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, e.srv.URL+"/admin-door"+path, rdr)
	req.Header.Set("Content-Type", "application/json")
	if who.ID != "" {
		req.Header.Set("X-Admin-User-Id", who.ID)
		req.Header.Set("X-Admin-Email", who.Email)
		req.Header.Set("X-Admin-Name", url.PathEscape(who.Name))
		req.Header.Set("X-Admin-Role", who.Role)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

func (e *testEnv) mustAdmin(who testAdmin, code int, method, path string, body any) map[string]any {
	e.t.Helper()
	got, out := e.adminDo(who, method, path, body)
	if got != code {
		e.t.Fatalf("%s %s as %s = %d, want %d: %v", method, path, who.Role, got, code, out)
	}
	return out
}

const testManifest = `{"schema_version":"agent.market.v1","id":"sran.financial-report","version":"1.0.0","name":"รายงานการเงิน",
"short_name":"ปิดงบการเงิน","python_package":"financial_report","greeting":{"text":"สวัสดีครับ"},
"inputs":[{"role":"primary","label":"GL","required":true}],"outputs":[{"format":"docx","label":"รายงาน","default":true},
{"format":"pptx","label":"สไลด์","default":true},{"format":"xlsx","label":"ตาราง","default":false}],
"capabilities":{"required":["spreadsheet.read.xlsx","deliverable.report.docx"],"optional":["deliverable.report.pdf"]},
"install":{"defaults":{"formats":["docx","pptx"]}},"permissions":{"default":"read_only"},"cannot":"อ่านอย่างเดียว"}`

func (e *testEnv) seedDefinition() Definition {
	e.t.Helper()
	d, err := e.store.UpsertDefinition(context.Background(), Definition{Slug: "sran.financial-report", Version: "1.0.0",
		Name: "รายงานการเงิน", Manifest: json.RawMessage(testManifest), Checksum: "pkgsum", EngineChecksum: "engsum"})
	if err != nil {
		e.t.Fatal(err)
	}
	return d
}

func (e *testEnv) heartbeat(caps map[string]Binding) {
	b, _ := json.Marshal(caps)
	if err := e.store.Heartbeat(context.Background(), "runner-test", "t", time.Now(), b, 0, 2); err != nil {
		e.t.Fatal(err)
	}
}

func fullCaps() map[string]Binding {
	return map[string]Binding{
		"spreadsheet.read.xlsx":   {Capability: "spreadsheet.read.xlsx", Impl: "sandbox", Version: "x"},
		"deliverable.report.docx": {Capability: "deliverable.report.docx", Impl: "engine", Version: "y"},
	}
}

func TestInstallNeedsARunnerWithTheCapabilities(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	e.seedDefinition()
	if code, _ := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"}); code != http.StatusServiceUnavailable {
		t.Fatalf("no runner: want 503, got %d", code)
	}
	e.heartbeat(map[string]Binding{"spreadsheet.read.xlsx": {}})
	if code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"}); code != http.StatusConflict {
		t.Fatalf("missing capability: want 409, got %d %v", code, body)
	}
	e.heartbeat(fullCaps())
	code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report", "idempotency_key": "k1",
		"settings": map[string]any{"company": "บริษัท ทดสอบ จำกัด"}})
	if code != 200 || body["created"] != true {
		t.Fatalf("install: %d %v", code, body)
	}
	id := body["install"].(map[string]any)["id"].(string)
	// A double-click installs once.
	code, body = e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report", "idempotency_key": "k1"})
	if code != 200 || body["created"] != false || body["install"].(map[string]any)["id"].(string) != id {
		t.Fatalf("idempotent install: %d %v", code, body)
	}
	// The workspace greets and states the job.
	_, th := e.do("GET", "/installs/"+id+"/thread", nil)
	items := th["items"].([]any)
	if len(items) != 1 || items[0].(map[string]any)["kind"] != "greeting" {
		t.Fatalf("greeting: %v", items)
	}
	// Bindings are stored with a version.
	_, got := e.do("GET", "/installs/"+id, nil)
	defs := got["defaults"].([]any)
	if len(defs) < 2 {
		t.Fatalf("install defaults (formats + company) not stored: %v", defs)
	}
}

func (e *testEnv) install() Install {
	e.t.Helper()
	e.seedDefinition()
	e.heartbeat(fullCaps())
	code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"})
	if code != 200 {
		e.t.Fatalf("install %d %v", code, body)
	}
	id := uuid.MustParse(body["install"].(map[string]any)["id"].(string))
	in, err := e.store.GetInstall(context.Background(), e.user.TenantUUID, e.user.UserID, id)
	if err != nil {
		e.t.Fatal(err)
	}
	return in
}

func (e *testEnv) openCard(run Run, blocking bool) Card {
	e.t.Helper()
	q := Question{Key: "period_mixed", Code: "period_mixed", Severity: "blocking", Header: "รายการนอกงวด", Question: "ตัดออกไหม",
		Options: []Option{{Label: "ตัดออก", Value: json.RawMessage(`"exclude"`)}, {Label: "นับรวมด้วย", Value: json.RawMessage(`"include"`)}},
		Default: json.RawMessage(`"exclude"`), Rememberable: true}
	qs, _ := json.Marshal([]Question{q})
	c, err := e.store.CreateCard(context.Background(), e.store.Pool, Card{RunID: run.ID, InstallID: run.InstallID,
		TenantID: run.TenantID, UserID: run.UserID, Moment: "preflight", Kind: "questions", Blocking: blocking, Questions: qs})
	if err != nil {
		e.t.Fatal(err)
	}
	rid := run.ID
	if _, _, err := e.store.AddItem(context.Background(), e.store.Pool, run.InstallID, run.TenantID, run.UserID, &rid,
		CardItemPayload(c, NoText, NoText), nil); err != nil {
		e.t.Fatal(err)
	}
	return c
}

func TestMessageWithFileStartsARunAndAnsweringIsIdempotent(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	f, err := e.store.CreateFile(ctx, e.store.Pool, File{ID: uuid.New(), TenantID: in.TenantID, UserID: in.UserID, InstallID: in.ID,
		Name: "GL.xlsx", Size: 10, SHA256: "abc", StorageKey: "agent-market/x", Status: "staged"})
	if err != nil {
		t.Fatal(err)
	}
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "ปิดงบกันยายน",
		"file_ids": []string{f.ID.String()}, "client_id": "c-1"})
	if code != 200 || body["route"] != "new_run" {
		t.Fatalf("message: %d %v", code, body)
	}
	runID := uuid.MustParse(body["run_id"].(string))
	// The same message resent (flaky network) does nothing new.
	if code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "ปิดงบกันยายน",
		"file_ids": []string{f.ID.String()}, "client_id": "c-1"}); code != 200 || body["duplicate"] != true {
		t.Fatalf("resend must be a no-op: %d %v", code, body)
	}
	run, _ := e.store.RunByID(ctx, runID)
	if run.Status != RunQueued {
		t.Fatalf("a new run is queued, got %s", run.Status)
	}
	files, _ := e.store.RunFiles(ctx, runID, "active")
	if len(files) != 1 {
		t.Fatalf("the file is attached to the run: %v", files)
	}
	// The runner would park it on a card; simulate that.
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status='awaiting_input', awaiting_since=now() WHERE id=$1`, runID)
	card := e.openCard(run, true)
	ans := map[string]any{"answers": map[string]any{"period_mixed": map[string]any{"labels": []string{"ตัดออก"}}}}
	code, body = e.do("POST", "/cards/"+card.ID.String()+"/answer", ans)
	if code != 200 || body["changed"] != true {
		t.Fatalf("answer: %d %v", code, body)
	}
	run, _ = e.store.RunByID(ctx, runID)
	if run.Status != RunQueued {
		t.Fatalf("an answer resumes the parked run, got %s", run.Status)
	}
	var answers map[string]Answer
	_ = json.Unmarshal(run.Answers, &answers)
	if string(answers["period_mixed"].Value) != `"exclude"` || answers["period_mixed"].CardID != card.ID.String() {
		t.Fatalf("answer recorded by key: %v", answers)
	}
	// Second submit — even with a different choice — changes nothing.
	ans2 := map[string]any{"answers": map[string]any{"period_mixed": map[string]any{"labels": []string{"นับรวมด้วย"}}}}
	code, body = e.do("POST", "/cards/"+card.ID.String()+"/answer", ans2)
	if code != 200 || body["changed"] != false {
		t.Fatalf("second submit must be a no-op: %d %v", code, body)
	}
	run, _ = e.store.RunByID(ctx, runID)
	_ = json.Unmarshal(run.Answers, &answers)
	if string(answers["period_mixed"].Value) != `"exclude"` {
		t.Fatal("the recorded answer must not change")
	}
	// AM4: "leave the stray rows out" is about THIS round's data: it is not
	// remembered for the next round (before AM4 it became the install's
	// default and quietly shaped the next month).
	defs, _ := e.store.ListDefaults(ctx, in.ID)
	for _, d := range defs {
		if d.Key == "answers.period_mixed" {
			t.Fatalf("a round-data answer must not be remembered: %+v", defs)
		}
	}
}

func TestStaleSupersededCardChangesNothing(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	if err != nil {
		t.Fatal(err)
	}
	card := e.openCard(run, true)
	if _, err := e.store.SupersedeOpenCards(ctx, e.store.Pool, run.ID, "superseded"); err != nil {
		t.Fatal(err)
	}
	code, body := e.do("POST", "/cards/"+card.ID.String()+"/answer",
		map[string]any{"answers": map[string]any{"period_mixed": map[string]any{"labels": []string{"ตัดออก"}}}})
	if code != 200 || body["changed"] != false || body["card"].(map[string]any)["status"] != "superseded" {
		t.Fatalf("a scrolled-back superseded card must return its state: %d %v", code, body)
	}
}

func TestCancelTwiceSameStateAndOwnerScoping(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	run, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	c1, b1 := e.do("POST", "/runs/"+run.ID.String()+"/cancel", nil)
	c2, b2 := e.do("POST", "/runs/"+run.ID.String()+"/cancel", nil)
	s1 := b1["run"].(map[string]any)["status"]
	s2 := b2["run"].(map[string]any)["status"]
	if c1 != 200 || c2 != 200 || s1 != RunCancelled || s2 != RunCancelled {
		t.Fatalf("cancel twice: %d %v / %d %v", c1, s1, c2, s2)
	}
	other := uuid.New().String()
	if code, _ := e.do("GET", "/runs/"+run.ID.String(), nil, "X-Test-User", other); code != 404 {
		t.Fatalf("another user's run must be 404, got %d", code)
	}
	if code, _ := e.do("POST", "/runs/"+run.ID.String()+"/cancel", nil, "X-Test-User", other); code != 404 {
		t.Fatalf("another user cannot cancel: %d", code)
	}
}

// cardOnScreen is the status the thread shows for a card — what the web renders.
func (e *testEnv) cardOnScreen(in Install, card Card) string {
	e.t.Helper()
	_, th := e.do("GET", "/installs/"+in.ID.String()+"/thread", nil)
	for _, it := range th["items"].([]any) {
		m := it.(map[string]any)
		p, _ := m["payload"].(map[string]any)
		if m["kind"] == "card" && p["card_id"] == card.ID.String() {
			return p["status"].(string)
		}
	}
	e.t.Fatalf("card %s is not in the thread", card.ID)
	return ""
}

func TestCancelledRunsCardIsNotAnswerableOnScreen(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	run, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status='awaiting_input', awaiting_since=now() WHERE id=$1`, run.ID)
	card := e.openCard(run, true)
	if code, _ := e.do("POST", "/runs/"+run.ID.String()+"/cancel", nil); code != 200 {
		t.Fatalf("cancel: %d", code)
	}
	if got := e.cardOnScreen(in, card); got != "cancelled" {
		t.Fatalf("a cancelled run's card must not stay open on screen, thread shows %q", got)
	}
}

func TestNewRoundClosesCardsLeftOnEarlierRuns(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	// An older finished run with a card nobody answered (the probe's leftover
	// "include this file?" card), and the latest finished run with another.
	older, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status='succeeded', finished_at=now() WHERE id=$1`, older.ID)
	late := e.openCard(older, false)
	latest, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status='succeeded', finished_at=now() WHERE id=$1`, latest.ID)
	asked := e.openCard(latest, false)
	f, err := e.store.CreateFile(ctx, e.store.Pool, File{ID: uuid.New(), TenantID: in.TenantID, UserID: in.UserID, InstallID: in.ID,
		Name: "GL_10.xlsx", Size: 10, SHA256: "def", StorageKey: "agent-market/y", Status: "staged"})
	if err != nil {
		t.Fatal(err)
	}
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "ปิดงบเดือนตุลาคม",
		"file_ids": []string{f.ID.String()}, "client_id": "c-new-round"})
	if code != 200 || body["route"] != "intent" {
		t.Fatalf("files after a finished round ask first (AM4): %d %v", code, body)
	}
	chip := lastOfKind(e.threadItems(in, ""), "intent")
	code, body = e.do("POST", "/intents/"+chip["id"].(string), map[string]any{"choice": "new_round"})
	if code != 200 || body["run_id"] == nil {
		t.Fatalf("new round: %d %v", code, body)
	}
	for name, c := range map[string]Card{"card of an older finished run": late, "card of the latest finished run": asked} {
		if got := e.cardOnScreen(in, c); got != "superseded" {
			t.Fatalf("%s must close when a new round starts, thread shows %q", name, got)
		}
		row, _ := e.store.CardByID(ctx, c.ID)
		if row.Status != "superseded" {
			t.Fatalf("%s row: %s", name, row.Status)
		}
	}
}

// A cosmetic question on a card runs on its default until the person answers;
// once they click, the files must not say the default was used.
func TestClickedCosmeticAnswerReplacesItsDefaultAssumption(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	run, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status='awaiting_input', awaiting_since=now() WHERE id=$1`, run.ID)
	blocking := Finding{Key: "period_mixed", Code: "period_mixed", Severity: "blocking", Moment: "preflight", Header: "นอกงวด",
		Question: "ตัดออกไหม", Kind: "question", Default: json.RawMessage(`"exclude"`), Evidence: json.RawMessage(`{}`),
		Options: []Option{{Label: "ตัดออก", Value: json.RawMessage(`"exclude"`)}, {Label: "นับรวมด้วย", Value: json.RawMessage(`"include"`)}}}
	cosmetic := Finding{Key: "year_convention_mixed", Code: "year_convention_mixed", Severity: "cosmetic", Moment: "preflight",
		Header: "ปี", Question: "เขียนปีแบบไหน", Kind: "question", Default: json.RawMessage(`"be"`), Evidence: json.RawMessage(`{}`),
		Options: []Option{{Label: "พ.ศ.", Value: json.RawMessage(`"be"`)}, {Label: "ค.ศ.", Value: json.RawMessage(`"ce"`)}}}
	plan, err := Plan(PlanInput{Moment: "preflight", Findings: []Finding{blocking, cosmetic}})
	if err != nil || plan.Card == nil {
		t.Fatalf("plan: %v %+v", err, plan)
	}
	_ = e.store.MergeAnswers(ctx, e.store.Pool, run.ID, plan.AutoAnswers)
	_ = e.store.AddAssumptions(ctx, e.store.Pool, run.ID, plan.Assumptions)
	qs, _ := json.Marshal(plan.Card.Questions)
	card, err := e.store.CreateCard(ctx, e.store.Pool, Card{RunID: run.ID, InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Moment: "preflight", Kind: "questions", Blocking: true, Questions: qs})
	if err != nil {
		t.Fatal(err)
	}
	code, _ := e.do("POST", "/cards/"+card.ID.String()+"/answer", map[string]any{"answers": map[string]any{
		"period_mixed": map[string]any{"labels": []string{"ตัดออก"}}, "year_convention_mixed": map[string]any{"labels": []string{"ค.ศ."}}}})
	if code != 200 {
		t.Fatalf("answer: %d", code)
	}
	got, _ := e.store.RunByID(ctx, run.ID)
	var as []Assumption
	_ = json.Unmarshal(got.Assumptions, &as)
	for _, a := range as {
		if a.Code == "year_convention_mixed" && a.Source == "default" {
			t.Fatalf("the person chose ค.ศ.; the files must not say the default was used: %+v", as)
		}
	}
}

func TestEventStreamResumesFromCursor(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	_, th := e.do("GET", "/installs/"+in.ID.String()+"/thread", nil)
	cursor := int64(th["cursor"].(float64))
	_, _, _ = e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, nil, AgentText{Text: Text("ข้อความแรก")}, nil)
	_, _, _ = e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, nil, AgentText{Text: Text("ข้อความที่สอง")}, nil)
	cctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(cctx, "GET", e.srv.URL+"/agent-market/installs/"+in.ID.String()+"/events?after="+itoa(int(cursor)), nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	buf := make([]byte, 8192)
	var got []byte
	deadline := time.Now().Add(1500 * time.Millisecond)
	for time.Now().Before(deadline) && bytes.Count(got, []byte("event: item")) < 2 {
		n, _ := resp.Body.Read(buf)
		got = append(got, buf[:n]...)
	}
	if bytes.Count(got, []byte("event: item")) != 2 || bytes.Contains(got, []byte("greeting")) {
		t.Fatalf("stream from cursor must send exactly the two new items, got:\n%s", got)
	}
}

// AM1b: PDF is not an output any more; asking the API for one is refused.
func TestFormatsRefusePDF(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	run, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status='succeeded', finished_at=now() WHERE id=$1`, run.ID)
	code, body := e.do("POST", "/runs/"+run.ID.String()+"/formats", map[string]any{"formats": []string{"pdf"}})
	if code != 400 {
		t.Fatalf("a PDF request must be refused, got %d %v", code, body)
	}
}
