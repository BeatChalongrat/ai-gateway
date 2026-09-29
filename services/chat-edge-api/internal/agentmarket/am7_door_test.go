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
// wrong.

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

func TestAM7BothDoorsReadAndWriteTheSameRow(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	// a write through the admin app's door, read back through both
	e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{
		"name": "บริษัท ทดสอบ จำกัด", "access": "list", "domains": []string{"example.test", "@Example.org"},
		"user_monthly_credits": "2,500,000", "contact_name": "คุณสมหญิง", "contact_email": "Admin@Example.test",
		"documents_may_leave": true, "retention_years": 7, "storage_warn_mb": 4096,
	})
	chat := orgOf(t, e.mustDo(200, "GET", "/admin/org", nil))
	admin := orgOf(t, e.mustAdmin(uiAdmin, 200, "GET", "/org", nil))
	sameOrg(t, "after the admin app wrote", chat, admin)
	if admin["documents_may_leave"] != true || admin["retention_years"] != float64(7) || admin["user_monthly_credits"] != float64(2500000) {
		t.Fatalf("the admin app's write did not land: %v", admin)
	}
	if admin["updated_by"] != uiAdmin.Email {
		t.Fatalf("updated_by = %v, want the admin-ui account %s", admin["updated_by"], uiAdmin.Email)
	}

	// a write through the chat app's door, read back through both
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"documents_may_leave": false, "retention_years": 5,
		"storage_warn_mb": 2048, "access": "everyone", "contact_name": "ฝ่ายไอที"})
	chat = orgOf(t, e.mustDo(200, "GET", "/admin/org", nil))
	admin = orgOf(t, e.mustAdmin(uiAdmin, 200, "GET", "/org", nil))
	sameOrg(t, "after the chat app wrote", chat, admin)
	if admin["documents_may_leave"] != false || admin["access"] != "everyone" {
		t.Fatalf("the chat app's write is not what the admin app reads: %v", admin)
	}

	// the people and departments are the same lists too
	e.mustAdmin(uiAdmin, 200, "PUT", "/members/somchai@example.test", map[string]any{"department": "บัญชี", "role": "publisher"})
	e.mustAdmin(uiAdmin, 200, "PUT", "/departments/บัญชี", map[string]any{"monthly_credits": 5000})
	c := e.mustDo(200, "GET", "/admin/org", nil)
	a := e.mustAdmin(uiAdmin, 200, "GET", "/org", nil)
	for _, k := range []string{"members", "departments"} {
		if !reflect.DeepEqual(c[k], a[k]) {
			t.Fatalf("%s differ — chat app %v, admin app %v", k, c[k], a[k])
		}
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
