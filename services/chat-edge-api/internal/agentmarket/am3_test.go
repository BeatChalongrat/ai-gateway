package agentmarket

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// AM3: anyone in the organisation can find an agent, install it and use it
// without us — the access list, budgets that name who to ask, the catalog's
// owner, the rail, sign-off, sharing inside the organisation, "report a
// problem", and messages a person can act on.

const am3Admin = "am1-test@example.test" // newEnv's break-glass admin

func as(email string) []string { return []string{"X-Test-Email", email} }

func (e *testEnv) mustDo(want int, method, path string, body any, hdr ...string) map[string]any {
	e.t.Helper()
	code, out := e.do(method, path, body, hdr...)
	if code != want {
		e.t.Fatalf("%s %s: want %d, got %d %v", method, path, want, code, out)
	}
	return out
}

func errMsg(body map[string]any) (string, string) {
	er, _ := body["error"].(map[string]any)
	c, _ := er["code"].(string)
	m, _ := er["message"].(string)
	return c, m
}

// installAs installs the financial report as a signed-in colleague.
func (e *testEnv) installAs(email string) Install {
	e.t.Helper()
	out := e.mustDo(200, "POST", "/installs", map[string]any{"slug": "sran.financial-report"}, as(email)...)
	id := uuid.MustParse(out["install"].(map[string]any)["id"].(string))
	in, err := e.store.InstallByID(context.Background(), id)
	if err != nil {
		e.t.Fatal(err)
	}
	return in
}

func (e *testEnv) publishFinancial() {
	e.t.Helper()
	e.seedDefinition()
	e.heartbeat(fullCaps())
	e.mustDo(200, "PUT", "/admin/catalog/sran.financial-report", map[string]any{"status": "published"})
}

func (e *testEnv) charge(in Install, credits float64) {
	e.t.Helper()
	run, _, err := e.store.CreateRun(context.Background(), e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Kind: KindRun, DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	if err := e.store.Charge(context.Background(), in.ID, run.ID, "k-"+uuid.NewString(), credits, "", nil); err != nil {
		e.t.Fatal(err)
	}
}

// succeededRun is a finished run with one delivered .docx (bytes in storage).
func (e *testEnv) succeededRun(in Install) (Run, Artifact) {
	e.t.Helper()
	ctx := context.Background()
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded', started_at = now() - interval '2 minutes',
		finished_at = now(), period = '2026-09' WHERE id = $1`, run.ID); err != nil {
		e.t.Fatal(err)
	}
	key := ArtifactKey(in.TenantID, run.ID, "report_v1.docx")
	body := "PK fake docx " + run.ID.String()
	if err := e.api.files.Upload(ctx, key, strings.NewReader(body), "", int64(len(body))); err != nil {
		e.t.Fatal(err)
	}
	art, _, err := e.store.InsertArtifact(ctx, Artifact{RunID: run.ID, InstallID: in.ID, Format: "docx", Kind: "report",
		Name: "report_v1.docx", Version: 1, StorageKey: key, Size: int64(len(body)), SHA256: "ab", ProducedByStep: "render-" + run.ID.String()},
		in.TenantID, in.UserID)
	if err != nil {
		e.t.Fatal(err)
	}
	run, _ = e.store.RunByID(ctx, run.ID)
	return run, art
}

// ── access ───────────────────────────────────────────────────────────────

func TestAM3AccessListAndWhoToAsk(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	e.publishFinancial()
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"access": "list", "contact_name": "คุณสมหญิง", "contact_email": "admin@example.test",
		"domains": []string{"example.test"}})
	stranger := "stranger@example.test"
	av := e.mustDo(200, "GET", "/availability", nil, as(stranger)...)
	if av["available"] != false || av["access"] != "list" || av["member"] != false || av["admin"] != false {
		t.Fatalf("a person not on the list: %v", av)
	}
	code, body := e.do("GET", "/catalog", nil, as(stranger)...)
	c, msg := errMsg(body)
	if code != 403 || c != "AGENT_MARKET_NOT_ALLOWED" || !strings.Contains(msg, "คุณสมหญิง") || !strings.Contains(msg, "ขอสิทธิ์") {
		t.Fatalf("refused, naming whom to ask: %d %s %q", code, c, msg)
	}
	// on the list: in, as a member
	e.mustDo(200, "PUT", "/admin/members/"+stranger, map[string]any{"department": "บัญชี", "display_name": "สมชาย ใจดี"})
	av = e.mustDo(200, "GET", "/availability", nil, as(stranger)...)
	if av["available"] != true || av["role"] != "member" || av["publisher"] != false {
		t.Fatalf("a member: %v", av)
	}
	e.mustDo(200, "GET", "/catalog", nil, as(stranger)...)
	// a member may not manage anything
	if code, body := e.do("GET", "/admin/org", nil, as(stranger)...); code != 403 {
		t.Fatalf("a member reads the admin page: %d %v", code, body)
	}
	// off: only admins
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"access": "off"})
	if code, _ := e.do("GET", "/catalog", nil, as(stranger)...); code != 403 {
		t.Fatalf("access off lets a member in: %d", code)
	}
	e.mustDo(200, "GET", "/catalog", nil)
	// everyone: anyone signed in
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"access": "everyone"})
	e.mustDo(200, "GET", "/catalog", nil, as("new.person@example.test")...)
	if code, _ := e.do("PUT", "/admin/org", map[string]any{"access": "anyone"}); code != 400 {
		t.Fatalf("a made-up access mode: %d", code)
	}
}

func TestAM3OrganisationSeedsOnceFromThePilotList(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	created, err := e.store.EnsureOrg(ctx, Config{AllowedEmails: []string{"owner@gbtech.test", "b@gbtech.test"}, UserBudgetCredits: 500})
	if err != nil || !created {
		t.Fatalf("first start creates the organisation: %v %v", created, err)
	}
	o, _ := e.store.OrgSettingsFor(ctx, DefaultOrg)
	if o.Access != AccessList || len(o.Domains) != 1 || o.Domains[0] != "gbtech.test" || o.UserMonthlyCredits != 500 {
		t.Fatalf("seeded: %+v", o)
	}
	members, _ := e.store.Members(ctx, DefaultOrg)
	if len(members) != 2 || members[0].Role != RoleAdmin {
		t.Fatalf("with no AGENT_MARKET_ADMIN_EMAILS the pilot's people are the first admins: %+v", members)
	}
	// the admins change it; a restart with other settings changes nothing
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.org_settings SET access = 'everyone'`); err != nil {
		t.Fatal(err)
	}
	if created, _ := e.store.EnsureOrg(ctx, Config{AllowedEmails: []string{"x@y.test"}}); created {
		t.Fatal("a second start must not re-seed")
	}
	o, _ = e.store.OrgSettingsFor(ctx, DefaultOrg)
	members, _ = e.store.Members(ctx, DefaultOrg)
	if o.Access != AccessEveryone || len(members) != 2 {
		t.Fatalf("the admins' settings win: %+v %d", o, len(members))
	}
	// an empty pilot list meant "everyone" in AM1, and still does
	e2 := newEnv(t)
	if created, _ := e2.store.EnsureOrg(ctx, Config{AdminEmails: []string{"boss@x.test"}}); !created {
		t.Fatal("create")
	}
	o2, _ := e2.store.OrgSettingsFor(ctx, DefaultOrg)
	if o2.Access != AccessEveryone {
		t.Fatalf("empty pilot list: %v", o2.Access)
	}
}

func TestAM3TheLastAdminStays(t *testing.T) {
	e := newEnvCfg(t, func(c *Config) { c.AdminEmails = nil })
	if e == nil {
		return
	}
	ctx := context.Background()
	if _, err := e.store.EnsureOrg(ctx, Config{AllowedEmails: []string{am3Admin}}); err != nil { // the caller: the only admin
		t.Fatal(err)
	}
	code, body := e.do("PUT", "/admin/members/"+am3Admin, map[string]any{"role": "member"})
	if c, _ := errMsg(body); code != 409 || c != "LAST_ADMIN" {
		t.Fatalf("demoting the last admin: %d %v", code, body)
	}
	if code, body := e.do("DELETE", "/admin/members/"+am3Admin, nil); code != 409 {
		t.Fatalf("removing the last admin: %d %v", code, body)
	}
	e.mustDo(200, "PUT", "/admin/members/second@example.test", map[string]any{"role": "admin"})
	e.mustDo(200, "PUT", "/admin/members/"+am3Admin, map[string]any{"role": "member"})
	if code, _ := e.do("PUT", "/admin/members/not-an-email", map[string]any{"role": "member"}, as("second@example.test")...); code != 400 {
		t.Fatalf("a bad email: %d", code)
	}
}

// ── budgets ──────────────────────────────────────────────────────────────

func TestAM3BudgetsNameWhoToAskAndParkedRunsGoOn(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.publishFinancial()
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"access": "list", "user_monthly_credits": 1000,
		"contact_name": "ฝ่ายไอที", "contact_email": "it@example.test"})
	u, v := "u@example.test", "v@example.test"
	e.mustDo(200, "PUT", "/admin/members/"+u, map[string]any{"department": "บัญชี"})
	e.mustDo(200, "PUT", "/admin/members/"+v, map[string]any{"department": "บัญชี"})
	e.mustDo(200, "PUT", "/admin/departments/บัญชี", map[string]any{"monthly_credits": 5000, "contact_name": "หัวหน้าบัญชี",
		"contact_email": "head@example.test"})
	e.mustDo(200, "GET", "/availability", nil, as(u)...) // first sight: the member row learns the user id
	e.mustDo(200, "GET", "/availability", nil, as(v)...)
	in := e.installAs(u)
	e.charge(in, 1200)
	st, err := e.store.Budget(ctx, in)
	if err != nil || st.Exhausted != BudgetUser || st.Contact.Name != "ฝ่ายไอที" {
		t.Fatalf("the person's month is used up, ask the organisation's contact: %+v %v", st, err)
	}
	note := BudgetNotice(st).String()
	for _, want := range []string{"งบเครดิตของคุณ", "1,200", "1,000", "ฝ่ายไอที", "it@example.test", "ลองต่อ", "ยังอยู่ครบ"} {
		if !strings.Contains(note, want) {
			t.Fatalf("notice lacks %q: %s", want, note)
		}
	}
	// a run waiting for budget: "ลองต่อ" says who to ask while it is still out
	run, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input', status_reason = 'budget_user',
		awaiting_since = now() WHERE id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}
	code, body := e.do("POST", "/runs/"+run.ID.String()+"/resume", nil, as(u)...)
	if c, m := errMsg(body); code != 409 || c != "BUDGET_STILL_EXHAUSTED" || !strings.Contains(m, "ฝ่ายไอที") {
		t.Fatalf("still out: %d %v", code, body)
	}
	// the admin raises this person's budget: the waiting run goes on by itself
	out := e.mustDo(200, "PUT", "/admin/members/"+u, map[string]any{"monthly_credits": "3,000"})
	if out["resumed"] != float64(1) {
		t.Fatalf("raising the budget resumes the parked run: %v", out)
	}
	if r, _ := e.store.RunByID(ctx, run.ID); r.Status != RunQueued {
		t.Fatalf("resumed run: %s", r.Status)
	}
	// the department: v spends the rest of the department's month
	inV := e.installAs(v)
	e.charge(inV, 4000)
	st, _ = e.store.Budget(ctx, in)
	if st.Exhausted != BudgetDepartment || st.Contact.Name != "หัวหน้าบัญชี" || !strings.Contains(BudgetNotice(st).String(), "แผนกบัญชี") {
		t.Fatalf("the department's month is used up, ask the department's contact: %+v", st)
	}
	// "none" is a valid department limit
	e.mustDo(200, "PUT", "/admin/departments/บัญชี", map[string]any{"monthly_credits": nil})
	if st, _ = e.store.Budget(ctx, in); st.Exhausted != "" {
		t.Fatalf("no department limit: %+v", st)
	}
	if code, _ := e.do("PUT", "/admin/members/"+u, map[string]any{"monthly_credits": "ประมาณ 2,000"}); code != 400 {
		t.Fatalf("a budget that is not a number: %d", code)
	}
	b := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/budget", nil, as(u)...)
	if b["budget"].(map[string]any)["user_limit"] != float64(3000) {
		t.Fatalf("the workspace banner: %v", b)
	}
}

// ── the catalog's owner ──────────────────────────────────────────────────

func TestAM3UnpublishedAgentsAreForPublishersOnly(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.publishFinancial()
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: "sran.bank-rec", Version: "0.1.0", Name: "กระทบยอดธนาคาร",
		Manifest: json.RawMessage(strings.Replace(testManifest, "sran.financial-report", "sran.bank-rec", 1)), Checksum: "b", EngineChecksum: "e"}); err != nil {
		t.Fatal(err)
	}
	member := "m@example.test"
	e.mustDo(200, "PUT", "/admin/members/"+member, map[string]any{})
	list := e.mustDo(200, "GET", "/catalog", nil, as(member)...)["agents"].([]any)
	if len(list) != 1 || list[0].(map[string]any)["slug"] != "sran.financial-report" || list[0].(map[string]any)["status"] != "published" {
		t.Fatalf("a member sees the published agent only: %v", list)
	}
	if code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.bank-rec"}, as(member)...); code != 403 {
		t.Fatalf("installing an unpublished agent: %d %v", code, body)
	}
	if code, _ := e.do("GET", "/catalog/sran.bank-rec", nil, as(member)...); code != 404 {
		t.Fatalf("an unpublished agent's page: %d", code)
	}
	// a publisher sees it and publishes it
	pub := "p@example.test"
	e.mustDo(200, "PUT", "/admin/members/"+pub, map[string]any{"role": "publisher"})
	all := e.mustDo(200, "GET", "/catalog", nil, as(pub)...)["agents"].([]any)
	if len(all) != 2 {
		t.Fatalf("a publisher sees every agent: %v", all)
	}
	e.mustDo(200, "PUT", "/admin/catalog/sran.bank-rec", map[string]any{"status": "published"}, as(pub)...)
	if list := e.mustDo(200, "GET", "/catalog", nil, as(member)...)["agents"].([]any); len(list) != 2 {
		t.Fatalf("published for everyone: %v", list)
	}
	// ...but only an admin switches one off for everyone
	code, body := e.do("PUT", "/admin/catalog/sran.bank-rec", map[string]any{"status": "disabled", "reason": "x"}, as(pub)...)
	if c, _ := errMsg(body); code != 403 || c != "NOT_ADMIN" {
		t.Fatalf("a publisher disabling: %d %v", code, body)
	}
}

func TestAM3DisabledAgentStopsNewRunsAndKeepsHistory(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	done, _ := e.succeededRun(in)
	queued, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	code, body := e.do("PUT", "/admin/catalog/sran.financial-report", map[string]any{"status": "disabled"})
	if c, _ := errMsg(body); code != 400 || c != "REASON_REQUIRED" {
		t.Fatalf("disabling needs a reason people will read: %d %v", code, body)
	}
	out := e.mustDo(200, "PUT", "/admin/catalog/sran.financial-report", map[string]any{"status": "disabled", "reason": "กำลังแก้ยอดภาษีที่คำนวณผิด"})
	if out["stopped_runs"] != float64(1) {
		t.Fatalf("the queued run stops: %v", out)
	}
	if r, _ := e.store.RunByID(ctx, queued.ID); r.Status != RunCancelled || r.StatusReason != "agent_disabled" {
		t.Fatalf("queued run: %s %s", r.Status, r.StatusReason)
	}
	// history stays readable
	e.mustDo(200, "GET", "/runs/"+done.ID.String(), nil)
	th := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/thread", nil)
	found := false
	for _, it := range th["items"].([]any) {
		p, _ := it.(map[string]any)["payload"].(map[string]any)
		if p["reason"] == "agent_disabled" && strings.Contains(p["text"].(string), "กำลังแก้ยอดภาษี") {
			found = true
		}
	}
	if !found {
		t.Fatal("the stopped run's thread says why")
	}
	// no new run: the message says what happened and whom to ask
	code, body = e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "ทำรอบใหม่"})
	c, msg := errMsg(body)
	if code != 409 || c != "AGENT_DISABLED" || !strings.Contains(msg, "กำลังแก้ยอดภาษี") || !strings.Contains(msg, am3Admin) ||
		!strings.Contains(msg, "ยังอยู่ครบ") {
		t.Fatalf("a message to a disabled agent: %d %s %q", code, c, msg)
	}
	if code, _ := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"}, as("n@example.test")...); code != 409 {
		t.Fatalf("installing a disabled agent: %d", code)
	}
	cat := e.mustDo(200, "GET", "/catalog", nil)["agents"].([]any)
	if cat[0].(map[string]any)["status"] != "disabled" {
		t.Fatalf("the catalog says it is off: %v", cat)
	}
	e.mustDo(200, "PUT", "/admin/catalog/sran.financial-report", map[string]any{"status": "published"})
	e.mustDo(200, "POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "ทำรอบใหม่"})
}

// ── the rail ─────────────────────────────────────────────────────────────

func TestAM3RailPinsRecentAndWaiting(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	rail := e.mustDo(200, "GET", "/rail", nil)
	rows := rail["installs"].([]any)
	if len(rows) != 1 || rail["waiting_total"] != float64(0) || rows[0].(map[string]any)["pinned_at"] != nil {
		t.Fatalf("one agent, nothing waiting: %v", rail)
	}
	run, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input' WHERE id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}
	rail = e.mustDo(200, "GET", "/rail", nil)
	row := rail["installs"].([]any)[0].(map[string]any)
	if rail["waiting_total"] != float64(1) || row["waiting"] != float64(1) || row["last_used_at"] == nil {
		t.Fatalf("a run waiting for an answer shows: %v", rail)
	}
	e.mustDo(200, "PUT", "/installs/"+in.ID.String()+"/pin", nil)
	row = e.mustDo(200, "GET", "/rail", nil)["installs"].([]any)[0].(map[string]any)
	if row["pinned_at"] == nil {
		t.Fatal("pinned")
	}
	e.mustDo(200, "DELETE", "/installs/"+in.ID.String()+"/pin", nil)
	e.mustDo(200, "POST", "/installs/"+in.ID.String()+"/opened", nil)
	out := e.mustDo(200, "PUT", "/prefs", map[string]any{"rail_collapsed": true, "anything": "else"})
	prefs := out["prefs"].(map[string]any)
	if prefs["rail_collapsed"] != true || len(prefs) != 1 {
		t.Fatalf("only the rail's keys are kept: %v", prefs)
	}
	if p := e.mustDo(200, "GET", "/rail", nil)["prefs"].(map[string]any); p["rail_collapsed"] != true {
		t.Fatalf("remembered per user: %v", p)
	}
	// another person's install is not theirs to pin
	if code, _ := e.do("PUT", "/installs/"+in.ID.String()+"/pin", nil, as("x@example.test")...); code != 404 {
		t.Fatalf("pinning someone else's agent: %d", code)
	}
}

// ── sharing and sign-off ─────────────────────────────────────────────────

func TestAM3ShareStaysInsideTheOrganisation(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"domains": []string{"example.test"}, "access": "list"})
	run, art := e.succeededRun(in)
	other, _ := e.succeededRun(in)
	path := "/runs/" + run.ID.String() + "/shares"
	for email, code := range map[string]string{"x@elsewhere.com": "NOT_IN_ORG", am3Admin: "SHARE_SELF", "not an email": "BAD_EMAIL"} {
		c, body := e.do("POST", path, map[string]any{"email": email})
		if got, _ := errMsg(body); c != 400 || got != code {
			t.Fatalf("share to %q: %d %v", email, c, body)
		}
	}
	colleague := "manager@example.test" // not on the access list: needs none to read what was shared with them
	out := e.mustDo(200, "POST", path, map[string]any{"email": colleague, "expires_days": 30})
	sh := out["share"].(map[string]any)
	id := sh["id"].(string)
	if out["path"] != "/chat/agents/shared/"+id || sh["can_review"] != false {
		t.Fatalf("share: %v", out)
	}
	list := e.mustDo(200, "GET", "/shared", nil, as(colleague)...)["shared"].([]any)
	if len(list) != 1 {
		t.Fatalf("shared with me: %v", list)
	}
	view := e.mustDo(200, "GET", "/shared/"+id, nil, as(colleague)...)
	if len(view["artifacts"].([]any)) != 1 || view["run"].(map[string]any)["status"] != "succeeded" {
		t.Fatalf("the colleague sees the run's files: %v", view)
	}
	// nobody else, even inside the organisation
	if code, body := e.do("GET", "/shared/"+id, nil, as("nosy@example.test")...); code != 403 {
		t.Fatalf("someone else opening the link: %d %v", code, body)
	}
	// the file of this run only
	req, _ := http.NewRequest("GET", e.srv.URL+"/agent-market/shared/"+id+"/artifacts/"+art.ID.String()+"/download", nil)
	req.Header.Set("X-Test-Email", colleague)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 || !strings.Contains(string(b), run.ID.String()) {
		t.Fatalf("download through the share: %d", resp.StatusCode)
	}
	arts, _ := e.store.RunArtifacts(context.Background(), other.ID)
	if code, _ := e.do("GET", "/shared/"+id+"/artifacts/"+arts[0].ID.String()+"/download", nil, as(colleague)...); code != 404 {
		t.Fatalf("another run's file through this share: %d", code)
	}
	// read only
	if code, body := e.do("POST", "/shared/"+id+"/review", map[string]any{}, as(colleague)...); code != 403 {
		t.Fatalf("a read-only share signs off: %d %v", code, body)
	}
	e.mustDo(200, "GET", path, nil)
	e.mustDo(200, "DELETE", "/shares/"+id, nil)
	if code, _ := e.do("GET", "/shared/"+id, nil, as(colleague)...); code != 403 {
		t.Fatalf("a revoked share still opens: %d", code)
	}
}

func TestAM3SignOffRecordsTheNameAndQueuesTheStamp(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"domains": []string{"example.test"}})
	run, _ := e.succeededRun(in)
	if _, err := e.store.ReplaceRunExceptions(ctx, run.ID, expenseExceptions()); err != nil {
		t.Fatal(err)
	}
	code, body := e.do("POST", "/runs/"+run.ID.String()+"/review", map[string]any{"name": "สมชาย ผู้ตรวจ"})
	if c, m := errMsg(body); code != 409 || c != "NOT_REVIEWABLE" || !strings.Contains(m, "ข้อยกเว้น") {
		t.Fatalf("open exceptions first: %d %v", code, body)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_run_exceptions SET status = 'resolved' WHERE run_id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}
	out := e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/review", map[string]any{"name": "  สมชาย   ผู้ตรวจ ", "note": "ตรวจกับงบทดลองแล้ว"})
	rev := out["review"].(map[string]any)
	job := out["job"].(map[string]any)
	if rev["reviewer_name"] != "สมชาย ผู้ตรวจ" || job["kind"] != "review" || job["parent_run_id"] != run.ID.String() {
		t.Fatalf("review: %v", out)
	}
	var p map[string]any
	j, _ := e.store.RunByID(ctx, uuid.MustParse(job["id"].(string)))
	_ = json.Unmarshal(j.Params, &p)
	if p["reviewer"] != "สมชาย ผู้ตรวจ" || p["note"] != "ตรวจกับงบทดลองแล้ว" || !strings.Contains(p["at_label"].(string), "เวลา") ||
		p["run_ref"] != run.ID.String()[:8] {
		t.Fatalf("stamp params: %v", p)
	}
	revs := e.mustDo(200, "GET", "/runs/"+run.ID.String()+"/reviews", nil)["reviews"].([]any)
	if len(revs) != 1 || revs[0].(map[string]any)["stamp_status"] != "queued" {
		t.Fatalf("the audit trail: %v", revs)
	}
	// through a share that allows it, the colleague's own name
	out = e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/shares", map[string]any{"email": "cfo@example.test", "can_review": true})
	sid := out["share"].(map[string]any)["id"].(string)
	out = e.mustDo(200, "POST", "/shared/"+sid+"/review", map[string]any{}, as("cfo@example.test")...)
	if out["review"].(map[string]any)["via_share_id"] != sid || out["review"].(map[string]any)["reviewer_email"] != "cfo@example.test" {
		t.Fatalf("review through a share: %v", out)
	}
	// only a finished run
	queued, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	if code, _ := e.do("POST", "/runs/"+queued.ID.String()+"/review", map[string]any{}); code != 409 {
		t.Fatalf("reviewing an unfinished run: %d", code)
	}
}

func TestThaiDateTime(t *testing.T) {
	at := time.Date(2026, 9, 28, 3, 5, 0, 0, time.UTC) // 10:05 in Bangkok
	if got := ThaiDateTime(at); got != "28 ก.ย. 2569 เวลา 10:05 น." {
		t.Fatalf("got %q", got)
	}
}

// ── report a problem ─────────────────────────────────────────────────────

func TestAM3ReportAProblemCarriesTheRunAndTheStep(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	st, _, _ := e.store.BeginStep(ctx, run.ID, "compute", "compute", "k1", nil)
	_ = e.store.FinishStep(ctx, st.ID, "failed", nil, "KeyError: 'debit'", "", "", 0)
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'failed', status_reason = 'compute_failed' WHERE id = $1`, run.ID)
	if code, body := e.do("POST", "/runs/"+run.ID.String()+"/problem", map[string]any{"text": "  "}); code != 400 {
		t.Fatalf("an empty report: %d %v", code, body)
	}
	out := e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/problem", map[string]any{"text": "ยอดขายในรายงานไม่ตรงกับ GL",
		"page": "/chat/agents/runs/" + run.ID.String()})
	rep := out["report"].(map[string]any)
	if rep["step"] != "compute" || rep["step_error"] != "KeyError: 'debit'" || rep["run_status"] != "failed" ||
		rep["slug"] != "sran.financial-report" || len(rep["ref"].(string)) != 6 {
		t.Fatalf("captured: %v", rep)
	}
	if !strings.Contains(out["message"].(string), "#"+rep["ref"].(string)) || !strings.Contains(out["message"].(string), "ยังอยู่ครบ") {
		t.Fatalf("the person is told where it went: %v", out["message"])
	}
	if n, _ := e.store.OpenProblems(ctx); n != 1 {
		t.Fatalf("open reports: %d", n)
	}
	e.mustDo(200, "POST", "/installs/"+in.ID.String()+"/problem", map[string]any{"text": "ไม่รู้ว่าต้องส่งไฟล์อะไร"})
	e.mustDo(200, "PUT", "/admin/members/member@example.test", map[string]any{})
	if code, _ := e.do("GET", "/admin/problems", nil, as("member@example.test")...); code != 403 {
		t.Fatalf("a member reads the reports: %d", code)
	}
	list := e.mustDo(200, "GET", "/admin/problems", nil)["problems"].([]any)
	if len(list) != 2 {
		t.Fatalf("admin list: %v", list)
	}
	e.mustDo(200, "POST", "/admin/problems/"+rep["id"].(string)+"/close", map[string]any{"note": "แก้แล้วใน 1.2.1"})
	if n, _ := e.store.OpenProblems(ctx); n != 1 {
		t.Fatalf("closed one: %d", n)
	}
}

// ── spend ────────────────────────────────────────────────────────────────

func TestAM3SpendPerPersonAndPerAgent(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	e.publishFinancial()
	e.mustDo(200, "PUT", "/admin/members/a@example.test", map[string]any{"display_name": "เอ", "department": "บัญชี"})
	e.mustDo(200, "GET", "/availability", nil, as("a@example.test")...)
	inA := e.installAs("a@example.test")
	inB := e.installAs("b@example.test")
	e.charge(inA, 700)
	e.charge(inA, 300)
	e.charge(inB, 50)
	out := e.mustDo(200, "GET", "/admin/spend?days=30", nil)
	users := out["by_user"].([]any)
	if len(users) != 2 || out["total_credits"] != float64(1050) {
		t.Fatalf("by person: %v", out)
	}
	top := users[0].(map[string]any)
	if top["email"] != "a@example.test" || top["name"] != "เอ" || top["department"] != "บัญชี" || top["credits"] != float64(1000) ||
		top["runs"] != float64(2) || top["month_spent"] != float64(1000) {
		t.Fatalf("the person who spent most first, named: %v", top)
	}
	agents := out["by_agent"].([]any)
	if len(agents) != 1 || agents[0].(map[string]any)["users"] != float64(2) || agents[0].(map[string]any)["credits"] != float64(1050) {
		t.Fatalf("by agent: %v", agents)
	}
	if code, _ := e.do("GET", "/admin/spend", nil, as("a@example.test")...); code != 403 {
		t.Fatalf("a member reads the spend: %d", code)
	}
}

// ── the catalog a stranger reads ─────────────────────────────────────────

const guideManifest = `{"schema_version":"agent.market.v1","id":"sran.financial-report","version":"1.2.0","name":"รายงานการเงิน",
"summary":"อัปโหลดบัญชีแยกประเภท","python_package":"financial_report","narrative":"",
"inputs":[{"role":"primary","label":"บัญชีแยกประเภท","required":true,"accept":[".xlsx"]}],
"outputs":[{"format":"docx","label":"รายงาน","default":true}],
"capabilities":{"required":["spreadsheet.read.xlsx","model.text"]},"permissions":{"default":"read_only","denied":["email_send"]},
"guide":{"model_inputs":{"model.text":"ตารางงบกำไรขาดทุนที่คำนวณแล้ว ไม่ใช่ไฟล์ต้นฉบับ"},"never":["ไม่เขียนกลับระบบบัญชี"]},
"cannot":"ไม่ส่งอีเมล"}`

const visionManifest = `{"schema_version":"agent.market.v1","id":"sran.invoice-processing","version":"1.1.0","name":"ใบกำกับภาษี",
"python_package":"invoice_processing","narrative":"rules","document_index":{"retention_days":730},
"inputs":[{"role":"documents","label":"เอกสาร","required":true,"accept":[".pdf"]}],"outputs":[{"format":"xlsx","label":"ทะเบียน"}],
"capabilities":{"required":["document.extract.e2"],"optional":["model.vision"]},"permissions":{"default":"read_only"}}`

func TestAM3DatasheetSaysWhichModelSeesWhat(t *testing.T) {
	text, _ := json.Marshal(map[string]any{"primary": "qwen3.8-27b-fp8", "fallback": "deepseek-v4-flash",
		"self_hosted": map[string]bool{"qwen3.8-27b-fp8": true}})
	caps := map[string]Binding{"model.text": {Capability: "model.text", Detail: text},
		"platform.storage": {Capability: "platform.storage", Detail: json.RawMessage(`{"backend":"local","staged_hours":24,"scratch_days":7,"removed_days":30}`)}}
	ds, err := BuildDatasheet(Definition{Slug: "sran.financial-report", Version: "1.2.0", Name: "รายงานการเงิน", Manifest: json.RawMessage(guideManifest)},
		caps, StatusPublished, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(ds.Models) != 2 || !ds.Models[0].SelfHosted || ds.Models[1].SelfHosted || ds.Models[1].Model != "deepseek-v4-flash" ||
		ds.Models[1].Role != "fallback" || !strings.Contains(ds.Models[0].Sees, "ไม่ใช่ไฟล์ต้นฉบับ") ||
		!strings.Contains(ds.Models[1].Sees, "พิมพ์ถาม") {
		t.Fatalf("the text model and its fallback, which runs outside: %+v", ds.Models)
	}
	// AM4: a finished round's evidence for the organisation's years; unused files by the runner's own setting
	if ds.Retention[0]["unit"] != "years" || ds.Retention[0]["keep"] != float64(5) || ds.Retention[2]["keep"] != float64(1) ||
		len(ds.Never) != 1 || ds.Storage["backend"] != "local" {
		t.Fatalf("retention and promises: %+v %+v", ds.Retention, ds.Never)
	}
	// AM4: while documents may not leave the organisation, the outside fallback is not used at all
	inside := ds
	inside.Models = append([]DatasheetModel{}, ds.Models...)
	inside.NoModel = append([]string{}, ds.NoModel...)
	inside.Retention = []map[string]any{{"unit": "years", "keep": float64(5)}}
	inside.ApplyOrg(OrgSettings{RetentionYears: 7})
	if len(inside.Models) != 1 || inside.Models[0].Model != "qwen3.8-27b-fp8" || inside.Retention[0]["keep"] != float64(7) ||
		!strings.Contains(strings.Join(inside.NoModel, " "), "deepseek-v4-flash") || inside.Notes["not_archive"] == "" {
		t.Fatalf("the organisation's own decisions: %+v %v %v", inside.Models, inside.NoModel, inside.Retention)
	}
	vis, _ := json.Marshal(map[string]any{"model": "qwen3.8-27b-fp8", "self_hosted": true})
	ds, _ = BuildDatasheet(Definition{Slug: "sran.invoice-processing", Manifest: json.RawMessage(visionManifest)},
		map[string]Binding{"model.vision": {Capability: "model.vision", Detail: vis}, "model.text": {Capability: "model.text", Detail: text}},
		StatusPublished, time.Now())
	// an extraction agent: no model writes or reads its documents' text; a
	// person's typed words (and that run's table or card) go only to the
	// in-organisation text model, never to the external fallback; scans and
	// photos to the self-hosted vision model
	if len(ds.Models) != 2 || ds.Models[0].Role != "questions" || ds.Models[0].Model != "qwen3.8-27b-fp8" || !ds.Models[0].SelfHosted ||
		ds.Models[1].Role != "vision" || !ds.Models[1].SelfHosted || !strings.Contains(ds.NoModel[0], "กฎ") {
		t.Fatalf("an extraction agent: %+v %v", ds.Models, ds.NoModel)
	}
	for _, m := range ds.Models {
		if m.Model == "deepseek-v4-flash" {
			t.Fatal("the external fallback never sees an extraction agent's data")
		}
	}
	if !strings.Contains(ds.Models[0].Sees, "พิมพ์") || !strings.Contains(ds.Models[0].Sees, "ในองค์กรเท่านั้น") {
		t.Fatalf("what the questions model sees, and when: %q", ds.Models[0].Sees)
	}
	last := ds.Retention[len(ds.Retention)-1]
	if last["keep"] != float64(730) {
		t.Fatalf("the duplicate index keeps 730 days: %v", last)
	}
	ds, _ = BuildDatasheet(Definition{Slug: "sran.invoice-processing", Manifest: json.RawMessage(visionManifest)}, map[string]Binding{},
		StatusPublished, time.Now())
	if len(ds.Models) != 0 || !strings.Contains(strings.Join(ds.NoModel, " "), "OCR") ||
		!strings.Contains(strings.Join(ds.NoModel, " "), "ไม่ถูกส่งให้โมเดลใด") {
		t.Fatalf("no self-hosted model: OCR only, and typed words go nowhere: %+v", ds.NoModel)
	}
}

func TestAM3CatalogSampleIsReadableByEveryoneAndOnlyItsOwnFiles(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.publishFinancial()
	d, _ := e.store.DefinitionBySlug(ctx, "sran.financial-report")
	if miss, _ := e.store.MissingSamples(ctx); len(miss) != 1 {
		t.Fatalf("no sample yet: %v", miss)
	}
	run, err := e.store.CreateCatalogSample(ctx, d, nil, 1)
	if err != nil {
		t.Fatal(err)
	}
	if miss, _ := e.store.MissingSamples(ctx); len(miss) != 0 {
		t.Fatal("a sample is queued")
	}
	sample, _ := e.store.InstallByID(ctx, run.InstallID)
	if sample.Status != "catalog" || sample.UserID != uuid.Nil || run.Kind != KindSelfTest {
		t.Fatalf("an install nobody owns: %+v", sample)
	}
	if list, _ := e.store.ListInstalls(ctx, uuid.Nil, uuid.Nil); len(list) != 0 {
		t.Fatal("the sample install never shows as anyone's agent")
	}
	sr, art := e.succeededRunOn(sample, run)
	member := "m@example.test"
	e.mustDo(200, "PUT", "/admin/members/"+member, map[string]any{})
	out := e.mustDo(200, "GET", "/catalog/sran.financial-report/sample", nil, as(member)...)
	if len(out["artifacts"].([]any)) != 1 || out["sample"].(map[string]any)["status"] != "succeeded" {
		t.Fatalf("sample: %v", out)
	}
	if code, _ := e.do("GET", "/sample-artifacts/"+art.ID.String()+"/download", nil, as(member)...); code != 200 {
		t.Fatalf("downloading the sample: %d", code)
	}
	// a person's own file is never reachable through the sample route
	in := e.installAs(member)
	_, mine := e.succeededRun(in)
	if code, _ := e.do("GET", "/sample-artifacts/"+mine.ID.String()+"/download", nil); code != 404 {
		t.Fatalf("someone's artifact through the sample route: %d", code)
	}
	cat := e.mustDo(200, "GET", "/catalog", nil, as(member)...)["agents"].([]any)[0].(map[string]any)
	if cat["sample_run_id"] != sr.ID.String() {
		t.Fatalf("the catalog links the sample: %v", cat)
	}
	e.mustDo(200, "GET", "/catalog/sran.financial-report/datasheet", nil, as(member)...)
}

func (e *testEnv) succeededRunOn(in Install, run Run) (Run, Artifact) {
	e.t.Helper()
	ctx := context.Background()
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded', started_at = now() - interval '1 minute',
		finished_at = now() WHERE id = $1`, run.ID)
	key := ArtifactKey(in.TenantID, run.ID, "sample.docx")
	_ = e.api.files.Upload(ctx, key, strings.NewReader("sample"), "", 6)
	art, _, err := e.store.InsertArtifact(ctx, Artifact{RunID: run.ID, InstallID: in.ID, Format: "docx", Kind: "report", Name: "sample.docx",
		Version: 1, StorageKey: key, Size: 6, SHA256: "s", ProducedByStep: "sample-" + run.ID.String()}, in.TenantID, in.UserID)
	if err != nil {
		e.t.Fatal(err)
	}
	run, _ = e.store.RunByID(ctx, run.ID)
	return run, art
}

// ── messages a person can act on ─────────────────────────────────────────

func TestAM3EveryErrorIsAThaiSentenceWithANextStep(t *testing.T) {
	thai := regexp.MustCompile(`[\x{0E00}-\x{0E7F}]`)
	for code, m := range userMessages {
		if !thai.MatchString(m) {
			t.Errorf("%s is not Thai: %q", code, m)
		}
		if code != "NO_TEMPLATE" && !strings.Contains(m, "—") {
			t.Errorf("%s does not say what to do next: %q", code, m)
		}
	}
	// no handler writes its own (English) sentence any more: every error goes through say()
	files, _ := filepath.Glob("*.go")
	re := regexp.MustCompile(`writeErr\(w, http\.Status`)
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") || f == "messages.go" {
			continue
		}
		b, _ := os.ReadFile(f)
		if loc := re.FindIndex(b); loc != nil {
			t.Errorf("%s writes an error sentence of its own: %s", f, strings.SplitN(string(b[loc[0]:]), "\n", 2)[0])
		}
	}
}

func TestAM3Migration003DownUndoesAll(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run, _ := e.succeededRun(in)
	e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/review", map[string]any{"name": "ผู้ตรวจ"})
	d, _ := e.store.DefinitionBySlug(ctx, "sran.financial-report")
	if _, err := e.store.CreateCatalogSample(ctx, d, nil, 1); err != nil {
		t.Fatal(err)
	}
	down, err := migrationFS.ReadFile("migrations/003_self_serve.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatalf("down: %v", err)
	}
	var left int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM information_schema.tables WHERE table_schema = 'agent_market'
		AND table_name IN ('org_settings','org_members','org_departments','catalog_state','user_prefs','catalog_samples',
		'run_reviews','run_shares','problem_reports')`).Scan(&left)
	var cols int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market'
		AND table_name = 'agent_installs' AND column_name IN ('org_id','pinned_at','last_opened_at')`).Scan(&cols)
	var rec int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.schema_migrations WHERE version = '003_self_serve'`).Scan(&rec)
	if left != 0 || cols != 0 || rec != 0 {
		t.Fatalf("down leaves tables %d, columns %d, record %d", left, cols, rec)
	}
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_runs (install_id, tenant_id, user_id, kind, definition_version, checksum)
		VALUES ($1, $2, $3, 'review', '1', 'x')`, in.ID, in.TenantID, in.UserID); err == nil {
		t.Fatal("the AM2 kind constraint is back")
	}
	if r, err := e.store.RunByID(ctx, run.ID); err != nil || r.Status != RunSucceeded {
		t.Fatalf("the run itself stays: %v", err)
	}
	did, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(did) != 1 || did[0] != "003_self_serve" {
		t.Fatalf("up again: %v %v", did, err)
	}
}

// ── export and delete cover what AM3 keeps about a person ────────────────

func TestAM3ExportAndDeletionCoverTheNewRows(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"domains": []string{"example.test"}})
	me := "me@example.test"
	e.mustDo(200, "PUT", "/admin/members/"+me, map[string]any{"display_name": "ฉัน"})
	e.publishFinancial()
	in := e.installAs(me)
	run, _ := e.succeededRun(in)
	e.mustDo(200, "PUT", "/prefs", map[string]any{"rail_collapsed": true}, as(me)...)
	e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/problem", map[string]any{"text": "ตัวเลขผิด"}, as(me)...)
	e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/shares", map[string]any{"email": "boss@example.test", "can_review": true}, as(me)...)
	// a sign-off I gave on someone else's run
	theirs := e.install()
	theirRun, _ := e.succeededRun(theirs)
	out := e.mustDo(200, "POST", "/runs/"+theirRun.ID.String()+"/shares", map[string]any{"email": me, "can_review": true})
	sid := out["share"].(map[string]any)["id"].(string)
	e.mustDo(200, "GET", "/shared/"+sid, nil, as(me)...) // opened: the share learns my user id
	e.mustDo(200, "POST", "/shared/"+sid+"/review", map[string]any{"name": "ฉัน ผู้ตรวจ"}, as(me)...)

	p := NewAccountProvider(e.store, e.api.files)
	got := map[string]string{}
	if _, err := p.Export(ctx, in.TenantID, in.UserID, func(path string, r io.Reader) error {
		b, _ := io.ReadAll(r)
		got[path] = string(b)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	for doc, want := range map[string]string{"membership.json": me, "preferences.json": "rail_collapsed", "problem_reports.json": "ตัวเลขผิด",
		"shares.json": "boss@example.test", "reviews.json": "ฉัน ผู้ตรวจ"} {
		if !strings.Contains(got[doc], want) {
			t.Fatalf("export %s lacks %q: %s", doc, want, got[doc])
		}
	}
	if _, err := p.Purge(ctx, in.UserID); err != nil {
		t.Fatal(err)
	}
	for _, q := range []string{`SELECT count(*) FROM agent_market.user_prefs WHERE user_id = $1`,
		`SELECT count(*) FROM agent_market.problem_reports WHERE user_id = $1`,
		`SELECT count(*) FROM agent_market.run_shares WHERE recipient_user_id = $1 OR owner_user_id = $1`,
		`SELECT count(*) FROM agent_market.run_reviews WHERE reviewer_user_id = $1`,
		`SELECT count(*) FROM agent_market.org_members WHERE user_id = $1`} {
		var n int
		_ = e.store.Pool.QueryRow(ctx, q, in.UserID).Scan(&n)
		if n != 0 {
			t.Fatalf("%s: %d left", q, n)
		}
	}
	var name, email string
	if err := e.store.Pool.QueryRow(ctx, `SELECT reviewer_name, reviewer_email FROM agent_market.run_reviews WHERE run_id = $1`,
		theirRun.ID).Scan(&name, &email); err != nil || name != "ฉัน ผู้ตรวจ" || email != "" {
		t.Fatalf("my sign-off on their run stays on their record without my email: %q %q %v", name, email, err)
	}
}
