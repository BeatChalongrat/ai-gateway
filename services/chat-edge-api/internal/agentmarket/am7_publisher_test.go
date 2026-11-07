package agentmarket

import (
	"context"
	"encoding/json"
	"testing"
)

// AM7 phase 2 — a scoped role, so the finance lead doesn't need IT. An
// admin-ui account with the role agent_publisher may run the catalog and
// the problems queue; chat-edge refuses it everything else on the door —
// not the egress switch, not the access list, not budgets, not spend, not
// other people's runs. The menu in admin-ui is a soft gate; this is the
// boundary, tested endpoint by endpoint.

// am7Forbidden are the door's endpoints an agent_publisher must never reach.
func am7Forbidden(run, art, file string) [][3]string {
	return [][3]string{
		{"GET", "/org", ""},
		{"PUT", "/org", `{"documents_may_leave":true}`},
		{"PUT", "/org", `{"access":"everyone"}`},
		{"PUT", "/org", `{"retention_years":1}`},
		{"PUT", "/org", `{"user_monthly_credits":999999999}`},
		{"PUT", "/members/someone@example.test", `{"role":"admin"}`},
		{"PUT", "/members/" + uiPublisher.Email, `{"role":"admin"}`},
		{"DELETE", "/members/someone@example.test", ""},
		{"PUT", "/departments/บัญชี", `{"monthly_credits":null}`},
		{"DELETE", "/departments/บัญชี", ""},
		{"GET", "/spend?days=30", ""},
		{"GET", "/runs", ""},
		{"POST", "/runs/" + run + "/open", `{"reason":"อยากดูตัวเลขของเพื่อน"}`},
		{"GET", "/runs/" + run, ""},
		{"GET", "/runs/" + run + "/computed", ""},
		{"GET", "/runs/" + run + "/artifacts/" + art + "/download", ""},
		{"GET", "/runs/" + run + "/files/" + file + "/download", ""},
		{"POST", "/runs/" + run + "/remove-evidence", `{"reason":"ลบทิ้งเพื่อทดสอบสิทธิ์"}`},
		{"PUT", "/catalog/sran.financial-report", `{"status":"disabled","reason":"ปิดชั่วคราว"}`},
	}
}

func TestAM7APublisherIsRefusedEverythingButTheCatalogAndProblems(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.seedDefinition()
	in := e.install()
	run, art := e.succeededRun(in)
	file := "00000000-0000-4000-8000-000000000009"
	before, _ := e.store.OrgSettingsFor(ctx, DefaultOrg)

	for _, c := range am7Forbidden(run.ID.String(), art.ID.String(), file) {
		var body any
		if c[2] != "" {
			body = jsonBody(c[2])
		}
		code, out := e.adminDo(uiPublisher, c[0], c[1], body)
		if code != 403 {
			t.Fatalf("agent_publisher %s %s = %d, want 403: %v", c[0], c[1], code, out)
		}
		if ec, msg := errMsg(out); ec != "NOT_ADMIN" || looksEnglish(msg) {
			t.Fatalf("agent_publisher %s %s: refusal %s %q", c[0], c[1], ec, msg)
		}
	}
	// nothing moved
	after, _ := e.store.OrgSettingsFor(ctx, DefaultOrg)
	if after.DocumentsMayLeave != before.DocumentsMayLeave || after.Access != before.Access ||
		after.RetentionYears != before.RetentionYears || after.UserMonthlyCredits != before.UserMonthlyCredits {
		t.Fatalf("a refused request changed the organisation: %+v → %+v", before, after)
	}
	var opened int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.run_access_log`).Scan(&opened)
	if opened != 0 {
		t.Fatalf("a refused opening was recorded: %d", opened)
	}

	// what the role is for: the catalog …
	me := e.mustAdmin(uiPublisher, 200, "GET", "/me", nil)
	if me["role"] != RolePublisher || me["can_disable"] != false {
		t.Fatalf("me = %v", me)
	}
	if s, _ := me["sections"].([]any); len(s) != 2 || s[0] != "catalog" || s[1] != "problems" {
		t.Fatalf("sections = %v", me["sections"])
	}
	e.mustAdmin(uiPublisher, 200, "GET", "/catalog", nil)
	e.mustAdmin(uiPublisher, 200, "PUT", "/catalog/sran.financial-report", map[string]any{"status": "unpublished"})
	st := e.mustAdmin(uiPublisher, 200, "PUT", "/catalog/sran.financial-report", map[string]any{"status": "published"})
	if st["state"].(map[string]any)["changed_by"] != uiPublisher.Email {
		t.Fatalf("published by %v", st["state"])
	}
	if code, out := e.adminDo(uiPublisher, "POST", "/catalog/sran.financial-report/sample", nil); code == 403 {
		t.Fatalf("remaking a sample refused: %v", out)
	}
	// … and the problems queue
	rep := e.mustDo(200, "POST", "/installs/"+in.ID.String()+"/problem", map[string]any{"text": "ตัวเลขในรายงานไม่ตรง"})["report"].(map[string]any)
	list := e.mustAdmin(uiPublisher, 200, "GET", "/problems?status=open", nil)["problems"].([]any)
	if len(list) != 1 {
		t.Fatalf("problems: %v", list)
	}
	closed := e.mustAdmin(uiPublisher, 200, "POST", "/problems/"+rep["id"].(string)+"/close", map[string]any{"note": "แก้ใน 1.0.2"})
	if p := closed["problem"].(map[string]any); p["status"] != "closed" || p["closed_by"] != uiPublisher.Email {
		t.Fatalf("closed = %v", p)
	}

	// an admin does all of it
	for _, c := range am7Forbidden(run.ID.String(), art.ID.String(), file)[:1] {
		e.mustAdmin(uiAdmin, 200, c[0], c[1], nil)
	}
	e.mustAdmin(uiAdmin, 200, "GET", "/spend?days=30", nil)
	e.mustAdmin(uiAdmin, 200, "GET", "/runs", nil)
}

func jsonBody(s string) any {
	var v any
	_ = json.Unmarshal([]byte(s), &v)
	return v
}
