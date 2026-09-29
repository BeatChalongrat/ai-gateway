package agentmarket

import (
	"context"
	"net/http"
	"testing"
)

// AM7 phase 4 — the chat app and the desktop app are for users. The
// Agent Market admin surface is gone from them: not hidden behind a role —
// gone. On chat-edge that means the chat session's API has no admin route
// at all: every one of them is only on the admin app's door (admindoor.go),
// behind the admin bearer. An Agent Market admin signed in to the chat app
// (the env admin of these tests) is answered 404, like anybody else.

// formerChatAdminPaths are the admin routes the chat app's page used.
func formerChatAdminPaths(run string) [][2]string {
	return [][2]string{
		{"GET", "/admin/org"}, {"PUT", "/admin/org"},
		{"PUT", "/admin/members/someone@example.test"}, {"DELETE", "/admin/members/someone@example.test"},
		{"PUT", "/admin/departments/บัญชี"}, {"DELETE", "/admin/departments/บัญชี"},
		{"GET", "/admin/spend"}, {"GET", "/admin/problems"}, {"POST", "/admin/problems/" + run + "/close"},
		{"GET", "/admin/catalog"}, {"PUT", "/admin/catalog/sran.financial-report"}, {"POST", "/admin/catalog/sran.financial-report/sample"},
		{"GET", "/admin/runs"}, {"POST", "/admin/runs/" + run + "/open"}, {"GET", "/admin/runs/" + run},
		{"GET", "/admin/runs/" + run + "/computed"}, {"GET", "/admin/runs/" + run + "/artifacts/" + run + "/download"},
		{"GET", "/admin/runs/" + run + "/files/" + run + "/download"}, {"POST", "/admin/runs/" + run + "/remove-evidence"},
		{"GET", "/admin/mail"}, {"PUT", "/admin/mail"}, {"POST", "/admin/mail/test"},
	}
}

func TestAM7TheChatAppHasNoAdminDoor(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.seedDefinition()
	in := e.install()
	run, _ := e.succeededRun(in)
	before, _ := e.store.OrgSettingsFor(ctx, DefaultOrg)
	// the signed-in person is an Agent Market admin: still nothing
	if acc, _ := e.store.AccessFor(ctx, e.api.cfg, e.user.Email, e.user.UserID); !acc.Admin() {
		t.Fatal("the test's person should be an Agent Market admin")
	}
	for _, c := range formerChatAdminPaths(run.ID.String()) {
		code, body := e.chatDo(c[0], c[1], map[string]any{"documents_may_leave": !before.DocumentsMayLeave, "status": "disabled",
			"reason": "ทดสอบว่าประตูนี้ไม่มีแล้ว", "role": "admin", "to": "x@example.test"})
		if code != http.StatusNotFound && code != http.StatusMethodNotAllowed {
			t.Fatalf("chat app %s %s = %d %v, want 404 — the admin surface is still in the chat app", c[0], c[1], code, body)
		}
	}
	after, _ := e.store.OrgSettingsFor(ctx, DefaultOrg)
	if after.DocumentsMayLeave != before.DocumentsMayLeave {
		t.Fatal("the egress switch moved through the chat app")
	}
	// what a user keeps: browse, install, their own runs, report a problem
	e.mustDo(200, "GET", "/catalog", nil)
	e.mustDo(200, "GET", "/installs", nil)
	e.mustDo(200, "GET", "/runs/"+run.ID.String(), nil)
	e.mustDo(200, "POST", "/installs/"+in.ID.String()+"/problem", map[string]any{"text": "ทดสอบการแจ้งปัญหา"})
	// and the same settings live on the admin app's door
	e.mustAdmin(uiAdmin, 200, "GET", "/org", nil)
}
