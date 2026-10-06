package agentmarket

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM12: an agent that starts from a request and works on the web. What the
// control plane owns of it: the door (nothing starts until an admin opens the
// agent, and the thread says why in words), an admin's two lists and one
// policy, and the stored pages served to the round's owner as inert files.

const webSlug = "test.web-agent"

const webManifest = `{"schema_version":"agent.market.v1","id":"test.web-agent","version":"1.0.0","name":"ผู้ช่วยค้นหา",
"name_en":"Search assistant","short_name":"ค้นหา","python_package":"procurement","category":"purchasing","starts_from":"request",
"web":{"prompts":{"brief":"prompts/brief.md"},"caps":{"searches":4,"pages":8,"pages_per_host":3,"seconds":300,"source_mb":10,"results":8}},
"greeting":{"text":"สวัสดีครับ"},"inputs":[],"outputs":[{"format":"xlsx","label":"ตาราง","default":true}],
"capabilities":{"required":["spreadsheet.read.xlsx"],"optional":["web.search","web.fetch"]},
"install":{"defaults":{"formats":["xlsx"]}},"permissions":{"default":"read_only"},"cannot":"อ่านอย่างเดียว",
"templates":{"fields":{"company":{"label":"ชื่อบริษัท"}}},
"strings":{"drop.robots_disallow":"เว็บไซต์นี้ประกาศไม่ให้โปรแกรมอัตโนมัติอ่านหน้านี้ จึงไม่เปิด","drop.other":"ไม่ได้ใช้แหล่งนี้"},
"en":{"เว็บไซต์นี้ประกาศไม่ให้โปรแกรมอัตโนมัติอ่านหน้านี้ จึงไม่เปิด":"This site states that automated programs may not read this page, so it was not opened.",
"ไม่ได้ใช้แหล่งนี้":"This source was not used."}}`

func webCaps(search, fetch bool) map[string]Binding {
	caps := fullCaps()
	if search {
		caps["web.search"] = Binding{Capability: "web.search", Impl: "gateway:/search", Version: "t"}
	}
	if fetch {
		caps["web.fetch"] = Binding{Capability: "web.fetch", Impl: "web-fetcher", Version: "t"}
	}
	return caps
}

func (e *testEnv) installWebAgent() Install {
	e.t.Helper()
	ctx := context.Background()
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: webSlug, Version: "1.0.0", Name: "ผู้ช่วยค้นหา",
		Manifest: json.RawMessage(webManifest), Checksum: "websum", EngineChecksum: "engsum"}); err != nil {
		e.t.Fatal(err)
	}
	e.heartbeat(webCaps(true, true))
	code, body := e.do("POST", "/installs", map[string]any{"slug": webSlug})
	if code != 200 {
		e.t.Fatalf("an agent on the web installs while its switch is off: %d %v", code, body)
	}
	in, err := e.store.GetInstall(ctx, e.user.TenantUUID, e.user.UserID, uuid.MustParse(body["install"].(map[string]any)["id"].(string)))
	if err != nil {
		e.t.Fatal(err)
	}
	return in
}

func (e *testEnv) runsOf(in Install) int {
	return e.countRows(fmt.Sprintf(`SELECT count(*) FROM agent_market.agent_runs WHERE install_id = '%s' AND kind = 'run'`, in.ID))
}

// lastNotice is the newest notice of an install's thread: its words and its reason.
func (e *testEnv) lastNotice(in Install, hdr ...string) (string, string) {
	e.t.Helper()
	_, body := e.do("GET", "/installs/"+in.ID.String()+"/thread", nil, hdr...)
	items, _ := body["items"].([]any)
	for i := len(items) - 1; i >= 0; i-- {
		it := items[i].(map[string]any)
		if it["kind"] == "notice" {
			p := it["payload"].(map[string]any)
			text, _ := p["text"].(string)
			reason, _ := p["reason"].(string)
			return text, reason
		}
	}
	return "", ""
}

func TestAM12AnAgentOnTheWebIsOffUntilAnAdminTurnsItOn(t *testing.T) {
	web := Manifest{Category: "purchasing", Web: &WebSpec{}}
	plain := Manifest{Category: "purchasing"}
	yes, no := true, false
	for _, c := range []struct {
		name       string
		org        bool
		m          Manifest
		set        *bool
		agent, eff bool
	}{
		{"never set: off, whatever its category", true, web, nil, false, false},
		{"an agent of the same category that does not use the web is unchanged", true, plain, nil, true, true},
		{"an admin turned it on", true, web, &yes, true, true},
		{"an admin turned it on, the organisation's switch is off", false, web, &yes, true, false},
		{"an admin turned it off again", true, web, &no, false, false},
	} {
		e := EgressOf(c.org, c.m, c.set)
		if e.Agent != c.agent || e.Effective != c.eff || e.Web != c.m.UsesWeb() || e.Words.Empty() {
			t.Fatalf("%s: %+v", c.name, e)
		}
		for _, l := range []Lang{Thai, English} {
			if c.m.UsesWeb() && (LooksLikeError(e.Words.In(l)) || strings.Contains(e.Words.In(l), "เอกสาร") || strings.Contains(e.Words.In(l), "document")) {
				t.Fatalf("%s: an agent on the web is said to search, not to send documents (%s): %q", c.name, l, e.Words.In(l))
			}
		}
	}
}

func TestAM12ARequestStartsNothingWhileTheDoorIsClosedAndTheThreadSaysWhy(t *testing.T) {
	e, _ := auditEnv(t)
	if e == nil {
		return
	}
	in := e.installWebAgent()
	msg := "/installs/" + in.ID.String() + "/messages"
	say := func(text, client string, hdr ...string) map[string]any {
		t.Helper()
		code, body := e.do("POST", msg, map[string]any{"text": text, "client_id": client}, hdr...)
		if code != 200 {
			t.Fatalf("a request is always taken: %d %v", code, body)
		}
		return body
	}
	// the page of the install says so before anything is typed
	_, page := e.do("GET", "/installs/"+in.ID.String(), nil)
	web, _ := page["web"].(map[string]any)
	if web == nil || web["uses"] != true || web["open"] != false || web["why"] != "organisation_closed" || web["words"] == "" {
		t.Fatalf("the install says it cannot search yet, and why: %v", web)
	}

	// 1. nobody opened anything: the organisation's own switch is off
	if b := say("ขอซื้อเก้าอี้ 20 ตัว", "m1"); b["route"] != "web_closed" || b["run_id"] != nil {
		t.Fatalf("nothing starts: %v", b)
	}
	text, why := e.lastNotice(in)
	if why != "organisation_closed" || !strings.Contains(text, "ยังค้นหาไม่ได้") || !strings.Contains(text, "ผู้ดูแลระบบ") || LooksLikeError(text) {
		t.Fatalf("the thread says why, in words: %q (%s)", text, why)
	}
	if n := e.runsOf(in); n != 0 {
		t.Fatalf("no round exists: %d", n)
	}

	// 2. the organisation allows it; the agent's own switch is still off — its default
	e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{"documents_may_leave": true})
	if b := say("ขอซื้อเก้าอี้ 20 ตัว", "m2", "Accept-Language", "en"); b["route"] != "web_closed" {
		t.Fatalf("an agent on the web is off by default: %v", b)
	}
	text, why = e.lastNotice(in, "Accept-Language", "en")
	if why != "agent_closed" || !strings.Contains(text, "cannot search yet") || strings.ContainsAny(text, "กขคง") {
		t.Fatalf("said in the screen's language: %q (%s)", text, why)
	}

	// 3. an admin turns this agent on: the request starts a round, from the words alone
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+webSlug, map[string]any{"documents_may_leave": true})
	b := say("ขอซื้อเก้าอี้ 20 ตัว ส่งที่สำนักงานใหญ่", "m3")
	if b["route"] != "new_run" || b["run_id"] == nil {
		t.Fatalf("a request with no file starts a round of an agent that works from requests: %v", b)
	}
	if n := e.runsOf(in); n != 1 {
		t.Fatalf("one round: %d", n)
	}
	if _, err := e.store.Pool.Exec(context.Background(), `UPDATE agent_market.agent_runs SET status = 'cancelled', finished_at = now() WHERE install_id = $1`, in.ID); err != nil {
		t.Fatal(err)
	}

	// 4. the fetcher is not there: no page can be captured, so nothing starts
	e.heartbeat(webCaps(true, false))
	if b := say("ขอซื้อโต๊ะ 5 ตัว", "m4"); b["route"] != "web_closed" {
		t.Fatalf("without a fetcher nothing starts: %v", b)
	}
	if _, why := e.lastNotice(in); why != "no_fetcher" {
		t.Fatalf("why: %s", why)
	}

	// 5. no search provider: the agent cannot search by itself, but it opens what the person names
	e.heartbeat(webCaps(false, true))
	if b := say("ขอซื้อโต๊ะ 5 ตัว", "m5"); b["route"] != "web_closed" {
		t.Fatalf("no provider and no link: %v", b)
	}
	text, why = e.lastNotice(in)
	if why != "no_search" || !strings.Contains(text, "ลิงก์") {
		t.Fatalf("the thread says to paste a link: %q (%s)", text, why)
	}
	if b := say("ขอซื้อโต๊ะ 5 ตัว จาก https://shop.example.com/p/desk-150", "m6"); b["route"] != "new_run" {
		t.Fatalf("a link the person typed can be opened without a search provider: %v", b)
	}
	if n := e.runsOf(in); n != 2 {
		t.Fatalf("two rounds in all: %d", n)
	}
	// a word of thanks never starts a round
	if _, err := e.store.Pool.Exec(context.Background(), `UPDATE agent_market.agent_runs SET status = 'cancelled', finished_at = now() WHERE install_id = $1`, in.ID); err != nil {
		t.Fatal(err)
	}
	if b := say("ขอบคุณครับ", "m7"); b["route"] == "new_run" {
		t.Fatalf("thanks is not a request: %v", b)
	}
}

func TestAM12AnAgentThatWorksFromARequestTakesNoFileAsData(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.installWebAgent()
	for name, want := range map[string]int{"ราคา.csv": http.StatusUnsupportedMediaType, "quote.pdf": http.StatusUnsupportedMediaType,
		"ใบขอซื้อ.docx": http.StatusOK, "form.xlsx": http.StatusOK} {
		if code := e.upload(in, name); code != want {
			t.Fatalf("%s: %d, want %d — the only file such an agent takes is the organisation's own form", name, code, want)
		}
	}
}

func TestAM12AnAdminKeepsTheTwoListsAndThePolicyAndEveryChangeIsLogged(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	e.installWebAgent()
	// only an admin
	if code, _ := e.adminDo(uiPublisher, "GET", "/web", nil); code != http.StatusForbidden {
		t.Fatalf("a publisher does not hold these controls: %d", code)
	}
	if code, _ := e.chatDo("GET", "/admin/web", nil); code != http.StatusNotFound {
		t.Fatalf("the chat app has no such door: %d", code)
	}
	start := e.mustAdmin(uiAdmin, 200, "GET", "/web", nil)
	if len(start["deny"].([]any)) != 0 || len(start["suppliers"].([]any)) != 0 || len(start["agents"].([]any)) != 1 {
		t.Fatalf("nothing is listed until an admin lists it; the agents that use the web are named: %v", start)
	}
	agent := start["agents"].([]any)[0].(map[string]any)
	if agent["slug"] != webSlug || agent["effective"] != false || agent["web"] != true {
		t.Fatalf("the agent's switch is shown with it, off: %v", agent)
	}
	log.take()

	// ── the deny list: a host, read out of whatever was typed ──
	out := e.mustAdmin(uiAdmin, 200, "PUT", "/web/deny", map[string]any{"host": "https://WWW.Never-Open.example/p/1?x=2", "note": "ไม่ซื้อจากที่นี่"})
	deny := out["deny"].([]any)
	if len(deny) != 1 || deny[0].(map[string]any)["host"] != "never-open.example" {
		t.Fatalf("the host is kept, not the address: %v", deny)
	}
	if x, ok := find(log.take(), "agent_market.web.host_denied"); !ok || x.Details["host"] != "never-open.example" || x.Details["by"] != uiAdmin.Email {
		t.Fatalf("the change is in the log with who made it: %+v", x)
	}
	for _, bad := range []string{"", "not a host", "localhost", "http://", "a..b", "exa mple.com"} {
		if code, b := e.adminDo(uiAdmin, "PUT", "/web/deny", map[string]any{"host": bad}); code != 400 || errCode(b) != "BAD_HOST" {
			t.Fatalf("%q is not a host: %d %v", bad, code, b)
		}
	}
	e.mustAdmin(uiAdmin, 200, "DELETE", "/web/deny/never-open.example", nil)
	if _, ok := find(log.take(), "agent_market.web.host_allowed_again"); !ok {
		t.Fatal("taking a host off the list is logged too")
	}

	// ── the organisation's suppliers ──
	out = e.mustAdmin(uiAdmin, 200, "PUT", "/web/suppliers", map[string]any{"name": "บริษัท ตัวอย่างการค้า จำกัด", "tax_id": "0105556012345",
		"website": "https://www.supplier.example/shop"})
	sup := out["suppliers"].([]any)[0].(map[string]any)
	if sup["website"] != "supplier.example" || sup["tax_id"] != "0105556012345" {
		t.Fatalf("a supplier is kept with its tax ID and its site's host: %v", sup)
	}
	if _, ok := find(log.take(), "agent_market.web.supplier_added"); !ok {
		t.Fatal("adding a supplier is logged")
	}
	if code, b := e.adminDo(uiAdmin, "PUT", "/web/suppliers", map[string]any{"name": "บริษัท ตัวอย่างการค้า จำกัด"}); code != 409 || errCode(b) != "SUPPLIER_EXISTS" {
		t.Fatalf("the same name twice: %d %v", code, b)
	}
	if code, b := e.adminDo(uiAdmin, "PUT", "/web/suppliers", map[string]any{"name": "อีกราย", "tax_id": "12345"}); code != 400 || errCode(b) != "BAD_TAX_ID" {
		t.Fatalf("a tax ID is 13 digits: %d %v", code, b)
	}
	e.mustAdmin(uiAdmin, 200, "PUT", "/web/suppliers", map[string]any{"id": sup["id"], "name": "บริษัท ตัวอย่างการค้า จำกัด", "note": "ผู้ขายหลัก"})
	e.mustAdmin(uiAdmin, 200, "DELETE", "/web/suppliers/"+sup["id"].(string), nil)
	entries := log.take()
	if _, ok := find(entries, "agent_market.web.supplier_changed"); !ok {
		t.Fatal("changing a supplier is logged")
	}
	if _, ok := find(entries, "agent_market.web.supplier_removed"); !ok {
		t.Fatal("removing a supplier is logged")
	}

	// ── the policy: within its bounds, and it only ever lowers ──
	if code, b := e.adminDo(uiAdmin, "PUT", "/web/policy", map[string]any{"max_pages": 99}); code != 400 || errCode(b) != "BAD_WEB_POLICY" {
		t.Fatalf("more pages than the platform allows: %d %v", code, b)
	}
	out = e.mustAdmin(uiAdmin, 200, "PUT", "/web/policy", map[string]any{"max_pages": 5, "max_searches": 2, "unused_keep_days": 14})
	if p := out["policy"].(map[string]any); p["max_pages"] != float64(5) || p["unused_keep_days"] != float64(14) || p["max_seconds"] != nil {
		t.Fatalf("what was set is kept; what was not is the agent's own: %v", p)
	}
	if x, ok := find(log.take(), "agent_market.web.policy"); !ok || x.Details["to"] == nil {
		t.Fatalf("the policy's change is logged with its values: %+v", x)
	}
	after := e.mustAdmin(uiAdmin, 200, "GET", "/web", nil)
	caps := after["agents"].([]any)[0].(map[string]any)["caps"].(map[string]any)
	if caps["pages"] != float64(5) || caps["searches"] != float64(2) || caps["seconds"] != float64(300) {
		t.Fatalf("a round of the agent may now use: %v", caps)
	}

	// a change the log cannot take does not happen
	log.setFail(true)
	if code, _ := e.adminDo(uiAdmin, "PUT", "/web/deny", map[string]any{"host": "another.example"}); code != http.StatusServiceUnavailable {
		t.Fatalf("no trail, no change: %d", code)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.web_deny_hosts`); n != 0 {
		t.Fatalf("the list is as it was: %d", n)
	}
}

func errCode(body map[string]any) string {
	e, _ := body["error"].(map[string]any)
	s, _ := e["code"].(string)
	return s
}

func TestAM12APolicyOnlyLowersWhatARoundMayUse(t *testing.T) {
	m := Manifest{Web: &WebSpec{Caps: WebCaps{Searches: 4, Pages: 8, PagesPerHost: 3, Seconds: 300, SourceMB: 10, Results: 8}}}
	if got := WebCapsOf(m, WebPolicy{}); got != m.Web.Caps {
		t.Fatalf("no policy: the agent's own caps: %+v", got)
	}
	two, many := 2, 500
	got := WebCapsOf(m, WebPolicy{MaxPages: &two, MaxSearches: &many, MaxSeconds: &many})
	if got.Pages != 2 || got.Searches != 4 || got.Seconds != 300 {
		t.Fatalf("a policy lowers a cap and never raises one: %+v", got)
	}
	greedy := Manifest{Web: &WebSpec{Caps: WebCaps{Searches: 500, Pages: 500, PagesPerHost: 50, Seconds: 99999, SourceMB: 900, Results: 100}}}
	if got := WebCapsOf(greedy, WebPolicy{}); got != webCeiling {
		t.Fatalf("no agent's round uses more than the platform's ceiling, whatever its manifest asks: %+v", got)
	}
	if got := WebCapsOf(Manifest{Web: &WebSpec{}}, WebPolicy{}); got.Pages <= 0 || got.Seconds <= 0 || got.SourceMB <= 0 {
		t.Fatalf("a manifest that names no cap is still bounded: %+v", got)
	}
}

func TestAM12HostsAndLinksAreReadByTheirShape(t *testing.T) {
	for typed, want := range map[string]string{"example.com": "example.com", " HTTPS://www.Example.com/a/b?c=1 ": "example.com",
		"shop.example.co.th": "shop.example.co.th", "http://xn--12c1fe0br.example/": "xn--12c1fe0br.example"} {
		if got, ok := HostOf(typed); !ok || got != want {
			t.Fatalf("HostOf(%q) = %q %v, want %q", typed, got, ok, want)
		}
	}
	for _, typed := range []string{"", "localhost", "10.0.0.1", "169.254.169.254", "a b.com", "-bad.example", "http:///x"} {
		if got, ok := HostOf(typed); ok {
			t.Fatalf("HostOf(%q) = %q: not a site's name", typed, got)
		}
	}
	deny := []DenyHost{{Host: "never-open.example"}}
	for host, want := range map[string]bool{"never-open.example": true, "www.never-open.example": true, "shop.never-open.example.": true,
		"notnever-open.example": false, "never-open.example.evil.test": false, "example": false} {
		if got := HostDenied(host, deny); got != want {
			t.Fatalf("HostDenied(%q) = %v", host, got)
		}
	}
	links := LinksIn("ขอราคาจาก https://shop.example.com/p/1, และ (http://b.example/x?y=1) ไม่ใช่ ftp://c.example/z หรือ example.org")
	if len(links) != 2 || links[0] != "https://shop.example.com/p/1" || links[1] != "http://b.example/x?y=1" {
		t.Fatalf("the web addresses a person typed, and nothing else: %v", links)
	}
}

func TestAM12APickOffersAsManyOptionsAsItsTableHasRows(t *testing.T) {
	opts := func(n int) []Option {
		var out []Option
		for i := 0; i < n; i++ {
			out = append(out, Option{Label: fmt.Sprintf("ตัวเลือกที่ %d", i+1), Value: json.RawMessage(fmt.Sprintf(`"c%d"`, i+1))})
		}
		return out
	}
	f := Finding{Key: "proc_pick:i1", Code: "proc_pick", Severity: "blocking", Header: "เลือกตัวเลือก", Question: "จะร่างจากตัวเลือกไหน",
		Kind: "pick", Options: opts(9), Default: json.RawMessage(`"c9"`)}
	if err := f.Validate(); err != nil {
		t.Fatalf("eight candidates and “none of these”: %v", err)
	}
	f.Options = opts(10)
	if err := f.Validate(); err == nil {
		t.Fatal("a pick is still bounded")
	}
	f.Kind, f.Options, f.Default = "question", opts(5), json.RawMessage(`"c1"`)
	if err := f.Validate(); err == nil {
		t.Fatal("a question still offers two to four answers")
	}
}

// Migration 009 on the test database: up (a newly migrated database has it), down — it takes away the four
// tables and nothing else, and its own record — and up again.
func TestAM12Migration009DownUndoesAllAndComesBack(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.installWebAgent()
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		Message: "x", DefinitionVersion: "1.0.0", Checksum: "websum"}, "")
	if err != nil {
		t.Fatal(err)
	}
	// what 009 holds
	for _, q := range []string{
		`INSERT INTO agent_market.web_deny_hosts (org_id, host) VALUES ('default', 'never-open.example')`,
		`INSERT INTO agent_market.org_suppliers (org_id, name) VALUES ('default', 'ผู้ขายตัวอย่าง')`,
		`INSERT INTO agent_market.web_policy (org_id, max_pages) VALUES ('default', 5)`,
		fmt.Sprintf(`INSERT INTO agent_market.web_calls (org_id, install_id, run_id, kind, step_key) VALUES ('default', '%s', '%s', 'fetch', 'k1')`, in.ID, run.ID),
	} {
		if _, err := e.store.Pool.Exec(ctx, q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	tables := func() int {
		return e.countRows(`SELECT count(*) FROM information_schema.tables WHERE table_schema = 'agent_market'
			AND table_name IN ('web_deny_hosts', 'org_suppliers', 'web_policy', 'web_calls')`)
	}
	others := func() int {
		return e.countRows(`SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market'
			AND table_name NOT IN ('web_deny_hosts', 'org_suppliers', 'web_policy', 'web_calls')`)
	}
	rows := func() int { return e.countRows(`SELECT count(*) FROM agent_market.agent_runs`) }
	if tables() != 4 {
		t.Fatalf("009 makes four tables: %d", tables())
	}
	before, runs := others(), rows()
	down, err := migrationFS.ReadFile("migrations/009_am12.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatalf("down: %v", err)
	}
	if tables() != 0 || others() != before || rows() != runs {
		t.Fatalf("the down takes its own four tables and nothing else: %d tables left, %d→%d other columns, %d→%d runs",
			tables(), before, others(), runs, rows())
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.schema_migrations WHERE version = '009_am12'`); n != 0 {
		t.Fatal("the down forgets that 009 was applied")
	}
	applied, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(applied) != 1 || applied[0] != "009_am12" {
		t.Fatalf("up again: %v %v", applied, err)
	}
	if tables() != 4 || others() != before || e.countRows(`SELECT count(*) FROM agent_market.web_calls`) != 0 {
		t.Fatal("the second up brings the shape back, empty")
	}
}

func TestAM12AStoredPageIsServedOnlyToItsRoundsOwnerAndNeverAsAPage(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.installWebAgent()
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		Message: "x", DefinitionVersion: "1.0.0", Checksum: "websum"}, "")
	if err != nil {
		t.Fatal(err)
	}
	const name = "evidence_0123456789abcdef0123456789abcdef.jpg"
	put := func(key, body string) {
		if err := e.api.files.Upload(ctx, key, strings.NewReader(body), "", int64(len(body))); err != nil {
			t.Fatal(err)
		}
	}
	put(BlobKey(run.TenantID, run.ID, WebEvidenceStep, name), "\xff\xd8\xffpicture")
	prefix := "agent-market/" + run.TenantID.String() + "/runs/" + run.ID.String() + "/steps/abcdef0123456789"
	put(prefix+"/screenshot.jpg", "\xff\xd8\xffwhole page")
	put(prefix+"/page.html", "<html><script>alert(1)</script></html>")
	put(prefix+"/text.json", `{"blocks":[]}`)
	if err := e.store.RecordWebCall(ctx, WebCall{InstallID: in.ID, RunID: run.ID, Kind: "fetch", StepKey: "k1", Source: "s01",
		Host: "shop.example", URL: "http://shop.example/p/1", Outcome: "captured", BlobPrefix: prefix}); err != nil {
		t.Fatal(err)
	}
	base := "/runs/" + run.ID.String()
	get := func(path string, hdr ...string) (*http.Response, string) {
		t.Helper()
		req, _ := http.NewRequest("GET", e.srv.URL+"/agent-market"+path, nil)
		for i := 0; i+1 < len(hdr); i += 2 {
			req.Header.Set(hdr[i], hdr[i+1])
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		var b strings.Builder
		buf := make([]byte, 4096)
		for {
			n, err := resp.Body.Read(buf)
			b.Write(buf[:n])
			if err != nil {
				break
			}
		}
		return resp, b.String()
	}
	if resp, body := get(base + "/evidence/" + name); resp.StatusCode != 200 || resp.Header.Get("Content-Type") != "image/jpeg" || !strings.HasSuffix(body, "picture") {
		t.Fatalf("the picture of what the page showed: %d %s", resp.StatusCode, resp.Header.Get("Content-Type"))
	}
	for _, bad := range []string{"evidence_0123.jpg", "computed.json", "..%2Fcomputed.json", "evidence_0123456789abcdef0123456789abcdef.png"} {
		if resp, _ := get(base + "/evidence/" + bad); resp.StatusCode == 200 {
			t.Fatalf("%s is not a picture of evidence and is not served", bad)
		}
	}
	if resp, _ := get(base+"/evidence/"+name, "X-Test-User", uuid.NewString()); resp.StatusCode != http.StatusNotFound {
		t.Fatalf("another person's round: %d", resp.StatusCode)
	}
	resp, body := get(base + "/sources/s01/markup")
	if resp.StatusCode != 200 || !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/plain") ||
		!strings.Contains(resp.Header.Get("Content-Security-Policy"), "sandbox") || resp.Header.Get("X-Content-Type-Options") != "nosniff" ||
		!strings.Contains(body, "<script>") {
		t.Fatalf("a stored page's markup is handed over as text to read, never as a page to run: %d %v", resp.StatusCode, resp.Header)
	}
	if resp, _ := get(base + "/sources/s01/picture"); resp.StatusCode != 200 || resp.Header.Get("Content-Type") != "image/jpeg" {
		t.Fatalf("the whole page's picture: %d", resp.StatusCode)
	}
	for _, bad := range []string{"/sources/s01/capture.json", "/sources/s02/picture", "/sources/s01/..%2f..%2fcomputed"} {
		if resp, _ := get(base + bad); resp.StatusCode == 200 {
			t.Fatalf("%s is served", bad)
		}
	}
	// a stored page that was deleted at the end of its days says so
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.web_calls SET purged_at = now() WHERE run_id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}
	if resp, _ := get(base + "/sources/s01/picture"); resp.StatusCode != http.StatusGone {
		t.Fatalf("a deleted page: %d", resp.StatusCode)
	}
	_, list := e.do("GET", base+"/sources", nil)
	if calls, _ := list["calls"].([]any); len(calls) != 1 || calls[0].(map[string]any)["host"] != "shop.example" {
		t.Fatalf("the record of what the round opened stays: %v", list)
	}
}

func TestAM12AStoredPageThatDidNotBecomeACandidateIsMarkedForDeletionAndACandidatesIsNot(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.installWebAgent()
	run, _, _ := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		Message: "x", DefinitionVersion: "1.0.0", Checksum: "websum"}, "")
	for i, c := range []WebCall{
		{Kind: "fetch", StepKey: "a", Source: "s01", Outcome: "captured", BlobPrefix: "p/a"},
		{Kind: "fetch", StepKey: "b", Source: "s02", Outcome: "captured", BlobPrefix: "p/b"},
		{Kind: "fetch", StepKey: "c", Source: "s03", Outcome: "refused", Reason: "robots_disallow"},
		{Kind: "search", StepKey: "d", Outcome: "answered"},
	} {
		c.InstallID, c.RunID = in.ID, run.ID
		if err := e.store.RecordWebCall(ctx, c); err != nil {
			t.Fatalf("%d: %v", i, err)
		}
		// the same step read back by a resumed round writes nothing again
		if err := e.store.RecordWebCall(ctx, c); err != nil {
			t.Fatal(err)
		}
	}
	if err := e.store.MarkWebUsed(ctx, run.ID, []string{"s01"}, 7); err != nil {
		t.Fatal(err)
	}
	calls, _ := e.store.RunWebCalls(ctx, run.ID)
	if len(calls) != 4 {
		t.Fatalf("four calls, each once: %d", len(calls))
	}
	for _, c := range calls {
		switch c.Source {
		case "s01":
			if c.Used == nil || !*c.Used || c.PurgeAfter != nil {
				t.Fatalf("a candidate's page is the run's evidence: %+v", c)
			}
		case "s02":
			if c.Used == nil || *c.Used || c.PurgeAfter == nil {
				t.Fatalf("a page that did not become a candidate is deleted after its days: %+v", c)
			}
		default:
			if c.PurgeAfter != nil {
				t.Fatalf("a refusal stored nothing to delete: %+v", c)
			}
		}
	}
	// the choice changes which pages are candidates: one that becomes used is no longer marked
	if err := e.store.MarkWebUsed(ctx, run.ID, []string{"s01", "s02"}, 7); err != nil {
		t.Fatal(err)
	}
	calls, _ = e.store.RunWebCalls(ctx, run.ID)
	for _, c := range calls {
		if c.Source == "s02" && c.PurgeAfter != nil {
			t.Fatalf("now a candidate: kept: %+v", c)
		}
	}
	if due, _ := e.store.PurgeableWebCalls(ctx, 10); len(due) != 0 {
		t.Fatalf("nothing is due yet: %v", due)
	}
}

// A request typed together with the organisation's form: the words start the round and the form is that
// round's — an agent that works from a request has no data file to wait for. With a round already there,
// the form joins it and the words are not taken for an answer to its card.
func TestAM12ARequestTypedWithItsFormStartsARoundAndTheFormIsThatRounds(t *testing.T) {
	e, _ := auditEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.installWebAgent()
	msg := "/installs/" + in.ID.String() + "/messages"
	const words = "ขอซื้อเก้าอี้ 20 ตัว ส่งที่สำนักงานใหญ่ ใส่ในแบบฟอร์มนี้ให้ด้วย"
	form := e.stagedFile(in, "ใบขอซื้อ.docx")

	// the door is closed: nothing starts, the reason is said once, and nothing is said of the form
	code, b := e.do("POST", msg, map[string]any{"text": words, "file_ids": []string{form.ID.String()}, "client_id": "f1"})
	if code != 200 || b["route"] != "web_closed" || b["run_id"] != nil {
		t.Fatalf("closed: %d %v", code, b)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_thread_items WHERE install_id = $1 AND kind = 'notice'`, in.ID); n != 1 {
		t.Fatalf("one notice says why, and no second one about the form: %d", n)
	}

	e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{"documents_may_leave": true})
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+webSlug, map[string]any{"documents_may_leave": true})
	form = e.stagedFile(in, "ใบขอซื้อ 2.docx")
	code, b = e.do("POST", msg, map[string]any{"text": words, "file_ids": []string{form.ID.String()}, "client_id": "f2"})
	if code != 200 || b["route"] != "new_run" || b["run_id"] == nil {
		t.Fatalf("the words start the round: %d %v", code, b)
	}
	run := uuid.MustParse(b["run_id"].(string))
	rf, err := e.store.RoundForm(ctx, e.store.Pool, run)
	if err != nil || rf == nil || rf.FileID != form.ID || rf.Status != FormInspecting {
		t.Fatalf("the form is the new round's, and is being read: %+v %v", rf, err)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'run'`, in.ID); n != 1 {
		t.Fatalf("one round: %d", n)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_runs WHERE install_id = $1 AND kind = $2`, in.ID, KindMappingPreview); n != 1 {
		t.Fatalf("one job reads the form: %d", n)
	}
	// the form is not the round's data
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_files WHERE run_id = $1 AND status = 'active'`, run); n != 0 {
		t.Fatalf("the form was taken for a data file: %d", n)
	}

	// another form while that round is under way: it joins the round, and no second round starts
	again := e.stagedFile(in, "ใบขอซื้อ 3.docx")
	code, b = e.do("POST", msg, map[string]any{"text": "ใช้แบบฟอร์มนี้แทน", "file_ids": []string{again.ID.String()}, "client_id": "f3"})
	if code != 200 || b["route"] != "form" || b["run_id"] != run.String() {
		t.Fatalf("a form for the round that is there: %d %v", code, b)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'run'`, in.ID); n != 1 {
		t.Fatalf("still one round: %d", n)
	}

	// that round is finished. A form sent with words now is the finished round's: nothing new starts
	if err := e.store.Finish(ctx, run, "", RunSucceeded, ""); err != nil {
		t.Fatal(err)
	}
	const next = "ขอซื้อโต๊ะประชุม 2 ตัว ส่งที่สำนักงานใหญ่ ใส่ในแบบฟอร์มนี้ให้ด้วย"
	after := e.stagedFile(in, "ใบขอซื้อ 4.docx")
	code, b = e.do("POST", msg, map[string]any{"text": next, "file_ids": []string{after.ID.String()}, "client_id": "f4"})
	if code != 200 || b["route"] != "form" || b["run_id"] != run.String() {
		t.Fatalf("a form after a finished round, with no new round started: %d %v", code, b)
	}
	// "เริ่มรอบใหม่" is pressed: the notice tells a person to type the request — this agent has no files to send
	started := e.mustDo(200, "POST", "/installs/"+in.ID.String()+"/rounds", map[string]any{})
	said, _ := started["item"].(map[string]any)["payload"].(map[string]any)["text"].(string)
	if !strings.Contains(said, "พิมพ์คำขอของรอบนี้ได้เลย") || strings.Contains(said, "ส่งไฟล์ของรอบนี้") {
		t.Fatalf("the notice of a new round, for an agent that works from a request: %q", said)
	}
	// … and now the same kind of message is the next round: the words start it, and the form is its form
	// (found on the test stack's web app: the form was read for the finished round and the request was dropped)
	fresh := e.stagedFile(in, "ใบขอซื้อ 5.docx")
	code, b = e.do("POST", msg, map[string]any{"text": next, "file_ids": []string{fresh.ID.String()}, "client_id": "f5"})
	if code != 200 || b["route"] != "new_run" || b["run_id"] == nil || b["run_id"] == run.String() {
		t.Fatalf("after “เริ่มรอบใหม่”, the words start the next round: %d %v", code, b)
	}
	second := uuid.MustParse(b["run_id"].(string))
	rf, err = e.store.RoundForm(ctx, e.store.Pool, second)
	if err != nil || rf == nil || rf.FileID != fresh.ID {
		t.Fatalf("the form is the next round's: %+v %v", rf, err)
	}
	if got, err := e.store.RunByID(ctx, second); err != nil || got.Message != next {
		t.Fatalf("the next round's request is the words that came with the form: %q %v", got.Message, err)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'run'`, in.ID); n != 2 {
		t.Fatalf("two rounds: %d", n)
	}
}

// The other agents are told to send the round's files, as before.
func TestAM12TheNoticeOfANewRoundStillAsksAFilesAgentForItsFiles(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.installBank()
	e.endedRun(in, RunSucceeded, "", 30, 100)
	started := e.mustDo(200, "POST", "/installs/"+in.ID.String()+"/rounds", map[string]any{})
	said, _ := started["item"].(map[string]any)["payload"].(map[string]any)["text"].(string)
	if !strings.Contains(said, "ส่งไฟล์ของรอบนี้ได้เลย") {
		t.Fatalf("the notice of a new round, for an agent that works from files: %q", said)
	}
}

// The same for an agent that reads files (found while building AM12, present since a form could be
// dropped into a round): a form sent in the SAME message as the round's first files is that round's form.
// The round the message had just started was looked for outside the message's transaction, was not
// found there, and the thread was told there was no round to put the form in.
func TestAM12AFormSentWithARoundsFirstFilesIsThatRounds(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.install() // seeds the catalogue and the runner's capabilities
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: "sran.invoice-processing", Version: "1.0.0", Name: "ใบกำกับภาษี",
		Manifest: json.RawMessage(invoiceTemplatesManifest), Checksum: "inv", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	_, body := e.do("POST", "/installs", map[string]any{"slug": "sran.invoice-processing"})
	in, _ := e.store.GetInstall(ctx, e.user.TenantUUID, e.user.UserID, uuid.MustParse(body["install"].(map[string]any)["id"].(string)))
	data, form := e.stagedFile(in, "invoices.pdf"), e.stagedFile(in, "ฟอร์มสรุป.docx")
	code, b := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{data.ID.String(), form.ID.String()},
		"text": "สรุปใบกำกับภาษีเดือนนี้ ใส่ในฟอร์มนี้ให้หน่อย", "client_id": "both"})
	if code != 200 || b["route"] != "new_run" || b["run_id"] == nil {
		t.Fatalf("the files start the round: %d %v", code, b)
	}
	run := uuid.MustParse(b["run_id"].(string))
	rf, err := e.store.RoundForm(ctx, e.store.Pool, run)
	if err != nil || rf == nil || rf.FileID != form.ID {
		t.Fatalf("the form sent with the files is the new round's: %+v %v", rf, err)
	}
	if fs, _ := e.store.RunFiles(ctx, run, "active"); len(fs) != 1 || fs[0].ID != data.ID {
		t.Fatalf("the round's data is the data file alone: %v", fs)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_thread_items WHERE install_id = $1 AND kind = 'notice'`, in.ID); n != 0 {
		t.Fatalf("the thread was told there is no round for the form (%d notices)", n)
	}
}

// A PDF is a form only for an agent whose manifest says it reads PDF forms. For every other agent a PDF is
// what it was: a document to read (an invoice), or nothing the agent takes.
func TestAM12APdfIsAFormOnlyForAnAgentThatSaysItReadsPdfForms(t *testing.T) {
	reads := Manifest{Templates: json.RawMessage(`{"reads":["rows","lines","pdf"],"fields":{"total":{"label":"รวม"}}}`)}
	plain := Manifest{Templates: json.RawMessage(`{"fields":{"total":{"label":"รวม"}}}`)}
	none := Manifest{}
	for name, want := range map[string][3]string{
		"ใบขอซื้อ.pdf": {"pdf", "", ""}, "FORM.PDF": {"pdf", "", ""}, "form.docx": {"docx", "docx", "docx"}, "form.xlsm": {"xlsx", "xlsx", "xlsx"},
		"notes.txt": {"", "", ""},
	} {
		got := [3]string{FormKindOf(reads, name), FormKindOf(plain, name), FormKindOf(none, name)}
		if got != want {
			t.Fatalf("%s: %v, want %v", name, got, want)
		}
	}
	if len(TemplateKindsOf(plain)) != len(TemplateKinds) || len(TemplateKindsOf(reads)) != len(TemplateKinds)+1 || len(TemplateKinds) != 4 {
		t.Fatal("the kinds every agent's form can be are unchanged; one more for an agent that asks")
	}
	// an agent that reads PDFs as its data keeps them as data, whatever the message says about a form
	var invoices Manifest
	if err := json.Unmarshal([]byte(invoiceTemplatesManifest), &invoices); err != nil {
		t.Fatal(err)
	}
	pdf, docx := File{ID: uuid.New(), Name: "invoices.pdf"}, File{ID: uuid.New(), Name: "ฟอร์มสรุป.docx"}
	forms, data := SplitForms(invoices, []File{pdf, docx}, nil, "สรุปให้หน่อย ใส่ในฟอร์มนี้")
	if len(forms) != 1 || forms[0].ID != docx.ID || len(data) != 1 || data[0].ID != pdf.ID {
		t.Fatalf("the invoice agent's PDF is its data and the Word file its form: forms %v data %v", forms, data)
	}
	// for an agent that works from a request and reads PDF forms, a PDF sent with the request is its form
	reads.StartsFrom = "request"
	forms, data = SplitForms(reads, []File{pdf}, nil, "ขอซื้อเก้าอี้ 20 ตัว")
	if len(forms) != 1 || len(data) != 0 {
		t.Fatalf("a request agent's PDF: forms %v data %v", forms, data)
	}
}

// Why an address was not opened is said in words — the agent package's own, by the reason's code, in the
// language of the screen that asks — on the run's page and on the admin's; a reason the package has no
// words for is said as "not used", never as its code. And what a run searched for is kept with the call.
func TestAM12WhyAnAddressWasNotOpenedIsSaidInWords(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.installWebAgent()
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		Message: "x", DefinitionVersion: "1.0.0", Checksum: "websum"}, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []WebCall{
		{Kind: "search", StepKey: "s", URL: "office chair with armrests", Outcome: "answered", Results: 6, Provider: "mock", Credits: 24000},
		{Kind: "fetch", StepKey: "a", Source: "s01", Host: "www.norobots.example", URL: "http://www.norobots.example/p/1", Outcome: "refused", Reason: "robots_disallow"},
		{Kind: "fetch", StepKey: "b", Source: "s02", Host: "odd.example", URL: "http://odd.example/", Outcome: "refused", Reason: "a_reason_nobody_wrote_words_for"},
		{Kind: "fetch", StepKey: "c", Source: "s03", Host: "shop.example", URL: "http://shop.example/p/1", Outcome: "captured", BlobPrefix: "p/c"},
	} {
		c.OrgID, c.InstallID, c.RunID = DefaultOrg, in.ID, run.ID // (the runner records a call under its install's organisation)
		if err := e.store.RecordWebCall(ctx, c); err != nil {
			t.Fatal(err)
		}
	}
	words := func(hdr ...string) map[string]string {
		_, body := e.do("GET", "/runs/"+run.ID.String()+"/sources", nil, hdr...)
		out := map[string]string{}
		for _, x := range body["calls"].([]any) {
			c := x.(map[string]any)
			w, _ := c["words"].(string)
			out[c["kind"].(string)+":"+c["host"].(string)] = w
			if c["kind"] == "search" && c["url"] != "office chair with armrests" {
				t.Fatalf("what was searched for is kept with the call: %v", c)
			}
		}
		return out
	}
	th := words()
	if th["fetch:www.norobots.example"] != "เว็บไซต์นี้ประกาศไม่ให้โปรแกรมอัตโนมัติอ่านหน้านี้ จึงไม่เปิด" || th["fetch:odd.example"] != "ไม่ได้ใช้แหล่งนี้" ||
		th["fetch:shop.example"] != "" || th["search:"] != "" {
		t.Fatalf("the run page, in Thai: %v", th)
	}
	en := words("Accept-Language", "en")
	if en["fetch:www.norobots.example"] != "This site states that automated programs may not read this page, so it was not opened." ||
		en["fetch:odd.example"] != "This source was not used." {
		t.Fatalf("the run page, in English: %v", en)
	}
	// the admin's page: both languages with each address that was not opened, and the sites reached
	body := e.mustAdmin(uiAdmin, 200, "GET", "/web/calls?days=7", nil)
	runs := body["runs"].([]any)
	if len(runs) != 1 {
		t.Fatalf("one run went somewhere: %v", body)
	}
	row := runs[0].(map[string]any)
	if row["searches"] != float64(1) || row["fetches"] != float64(3) || len(row["reached"].([]any)) != 1 || row["search_credits"] != float64(24000) {
		t.Fatalf("what the run did: %v", row)
	}
	for _, x := range row["refused"].([]any) {
		no := x.(map[string]any)
		w := no["words"].(map[string]any)
		if w["th"] == "" || w["en"] == "" || strings.Contains(w["th"].(string), "_") || strings.Contains(w["en"].(string), "_") {
			t.Fatalf("an address not opened, on the admin's page: %v", no)
		}
	}
	// the Spend tab says what part of an agent's spend was searches
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_budget_ledger (install_id, run_id, step_key, credits, model)
		VALUES ($1, $2, 'k-search', 24000, 'search:mock'), ($1, $2, 'k-model', 900, 'deepseek-v4-flash')`, in.ID, run.ID); err != nil {
		t.Fatal(err)
	}
	spend := e.mustAdmin(uiAdmin, 200, "GET", "/spend?days=7", nil)
	for _, x := range spend["by_agent"].([]any) {
		a := x.(map[string]any)
		if a["key"] == webSlug && (a["credits"] != float64(24900) || a["search_credits"] != float64(24000)) {
			t.Fatalf("an agent's spend, and the part of it that was searches: %v", a)
		}
	}
}

// What a run costs is said before it starts — from the organisation's own runs, else from the catalog's
// sample run. The sample of an agent on the web runs on pages kept with its package: it searches nothing,
// opens nothing and asks no model, so its cost and its seconds say nothing of a real run. Until there are
// runs of the organisation's own, the workspace says the cost is not known yet, and nothing smaller.
func TestAM12TheSampleOfAnAgentOnTheWebIsNoBasisForWhatARunCosts(t *testing.T) {
	e := newEnvCfg(t, func(c *Config) { c.CreditsPerUSD, c.THBPerUSD = 1000, 35 })
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.installWebAgent()
	d, _ := e.store.DefinitionBySlug(ctx, webSlug)
	sample, err := e.store.CreateCatalogSample(ctx, d, nil, 1)
	if err != nil {
		t.Fatal(err)
	}
	if err := e.store.Charge(ctx, sample.InstallID, sample.ID, "k-sample", 800, "", nil); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET started_at = now() - interval '25 seconds' WHERE id = $1`, sample.ID); err != nil {
		t.Fatal(err)
	}
	if err := e.store.Finish(ctx, sample.ID, "", RunSucceeded, ""); err != nil {
		t.Fatal(err)
	}
	// the sample IS what the store would go by for any agent …
	if est, err := e.store.EstimateFor(ctx, e.api.cfg, in.OrgID, webSlug, false); err != nil || est.Basis != EstimateSample || est.Credits == nil {
		t.Fatalf("the store's own estimate, from the sample: %+v %v", est, err)
	}
	// … and is not what the workspace of an agent on the web is told
	out := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/estimate", nil)
	line, _ := out["line"].(string)
	if out["basis"] != "none" || out["credits"] != nil || out["seconds"] != nil || !strings.HasPrefix(line, "ยังไม่มีข้อมูลค่าใช้จ่ายของ agent นี้") {
		t.Fatalf("an agent on the web, with a sample and no run of its own: %v", out)
	}
}

// "Which files do you want" is a question about shapes of one report. An agent that works from a
// request makes the things the request was for — a comparison, and the draft made from the choice —
// and asks only what stops it: the question is never put on its choice card.
func TestAM12AnAgentThatWorksFromARequestIsNotAskedWhichFiles(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", "procurement", "agent.json"))
	if err != nil {
		t.Fatal(err)
	}
	m, err := ParseManifest(raw)
	if err != nil {
		t.Fatal(err)
	}
	copied := []Default{{Key: "formats", Value: json.RawMessage(`["xlsx","docx"]`), Source: "install"}}
	if !m.FromRequest() || len(OutputShapes(Thai, m)) < 2 {
		t.Fatalf("the agent works from a request and makes more than one file: %v %d", m.FromRequest(), len(OutputShapes(Thai, m)))
	}
	if f := OutputSetFinding(Thai, m, copied, []string{"xlsx", "docx"}); f != nil {
		t.Fatalf("asked which files: %s", f.Question)
	}
	// the same files from an agent that works from documents are still asked about
	m.StartsFrom = ""
	if OutputSetFinding(Thai, m, copied, []string{"xlsx", "docx"}) == nil {
		t.Fatal("an agent that works from documents is no longer asked which files")
	}
}
