package agentmarket

import (
	"encoding/json"
	"reflect"
	"sort"
	"testing"
	"unicode"
)

// AM7 phase 1 — the admin app (admin-ui) gets its own door to the Agent
// Market admin handlers, and both apps read and write the one
// agent_market.org_settings row: no copied setting, no second place to be
// wrong. Until phase 4 the test compared the two admin doors field by field
// (red run: scripts/probes/AM7/results/red_phase1_door.txt; both apps on the
// test stack: results/surfaces/both.json). Phase 4 removed the chat app's
// door; what stays true is that what the admin app sets is what the chat app
// shows the people who use agents.

// orgFields are every field of the organisation's settings as the wire has them.
func orgFields(t *testing.T) []string {
	t.Helper()
	b, _ := json.Marshal(OrgSettings{})
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func orgOf(t *testing.T, body map[string]any) map[string]any {
	t.Helper()
	o, ok := body["org"].(map[string]any)
	if !ok {
		t.Fatalf("no org in %v", body)
	}
	return o
}

// sameOrg fails on the first field the two doors report differently.
func sameOrg(t *testing.T, when string, chat, admin map[string]any) {
	t.Helper()
	for _, f := range orgFields(t) {
		if _, ok := admin[f]; !ok {
			t.Fatalf("%s: the admin app's door does not report %q", when, f)
		}
		if !reflect.DeepEqual(chat[f], admin[f]) {
			t.Fatalf("%s: %q differs — chat app %v, admin app %v", when, f, chat[f], admin[f])
		}
	}
}

func TestAM7TheChatAppShowsWhatTheAdminAppSet(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	e.seedDefinition()
	in := e.install()
	// the admin app writes; its own read gives back every field it wrote
	set := orgOf(t, e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{
		"name": "บริษัท ทดสอบ จำกัด", "access": "list", "domains": []string{"example.test", "@Example.org"},
		"user_monthly_credits": "2,500,000", "contact_name": "คุณสมหญิง", "contact_email": "Admin@Example.test",
		"documents_may_leave": true, "retention_years": 7, "storage_warn_mb": 4096,
	}))
	read := orgOf(t, e.mustAdmin(uiAdmin, 200, "GET", "/org", nil))
	sameOrg(t, "the admin app's write and read", set, read)
	if read["updated_by"] != uiAdmin.Email || read["user_monthly_credits"] != float64(2500000) || read["contact_email"] != "admin@example.test" {
		t.Fatalf("the admin app's write did not land: %v", read)
	}
	// the chat app shows the people who use agents the same values
	av := e.mustDo(200, "GET", "/availability", nil)
	contact, _ := av["contact"].(map[string]any)
	if av["access"] != read["access"] || contact["name"] != read["contact_name"] || contact["email"] != read["contact_email"] {
		t.Fatalf("availability says access=%v contact=%v, the admin app set %v / %v / %v", av["access"], contact,
			read["access"], read["contact_name"], read["contact_email"])
	}
	st := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/storage", nil)
	warn := st["storage"].(map[string]any)["warn_bytes"]
	if st["retention_years"] != read["retention_years"] || warn != read["storage_warn_mb"].(float64)*1024*1024 {
		t.Fatalf("storage says %v years, warn at %v bytes; the admin app set %v years, %v MB", st["retention_years"], warn,
			read["retention_years"], read["storage_warn_mb"])
	}
	b := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/budget", nil)["budget"].(map[string]any)
	if b["user_own_limit"] == false && b["user_limit"] != read["user_monthly_credits"] {
		t.Fatalf("budget says %v, the admin app set %v", b["user_limit"], read["user_monthly_credits"])
	}
}

func TestAM7TheAdminDoorNeedsANamedAdmin(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	// nobody named (a bearer call the proxy did not make): nothing
	if code, body := e.adminDo(testAdmin{}, "GET", "/org", nil); code != 403 {
		t.Fatalf("no admin named: %d %v, want 403", code, body)
	}
	// an admin-ui account whose role is not an Agent Market one: nothing
	for _, path := range []string{"/org", "/catalog", "/problems", "/runs", "/spend"} {
		if code, body := e.adminDo(uiViewer, "GET", path, nil); code != 403 {
			t.Fatalf("viewer GET %s: %d %v, want 403", path, code, body)
		}
	}
	// an admin: everything, and it says who they are and what they may do
	me := e.mustAdmin(uiAdmin, 200, "GET", "/me", nil)
	if me["role"] != "admin" || me["email"] != uiAdmin.Email || me["org_id"] != DefaultOrg {
		t.Fatalf("me = %v", me)
	}
	for _, path := range []string{"/org", "/catalog", "/problems", "/runs", "/spend?days=30"} {
		e.mustAdmin(uiAdmin, 200, "GET", path, nil)
	}
	// the Thai sentence a refusal carries names no English developer text
	_, body := e.adminDo(uiViewer, "GET", "/org", nil)
	code, msg := errMsg(body)
	if code != "NOT_ADMIN" || msg == "" || looksEnglish(msg) {
		t.Fatalf("refusal = %s %q", code, msg)
	}
}

// looksEnglish: a sentence meant for a Thai reader that has no Thai in it.
func looksEnglish(s string) bool {
	for _, r := range s {
		if unicode.Is(unicode.Thai, r) {
			return false
		}
	}
	return true
}
