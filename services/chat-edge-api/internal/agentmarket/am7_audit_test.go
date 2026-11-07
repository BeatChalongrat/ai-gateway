package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// AM7 phase 3 — every change to the organisation's Agent Market settings
// leaves a trail in the admin audit log auditors already read (the gateway's
// admin_audit_log, through the backend's internal endpoint): who, when,
// which setting, from what value to what value. The egress switch above all
// — "who allowed our ledger to go to an outside model" is one search.
//
// A change the log cannot record does not happen: the row is written in a
// transaction that commits only after the log took the entry.

// memAuditor keeps what was recorded; fail makes it refuse.
type memAuditor struct {
	mu      sync.Mutex
	entries []AuditEntry
	fail    bool
}

func (m *memAuditor) Record(_ context.Context, entries []AuditEntry) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.fail {
		return errors.New("audit log unreachable")
	}
	m.entries = append(m.entries, entries...)
	return nil
}

func (m *memAuditor) take() []AuditEntry {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := m.entries
	m.entries = nil
	return out
}

func (m *memAuditor) setFail(v bool) { m.mu.Lock(); m.fail = v; m.mu.Unlock() }

// auditEnv is a test env whose control plane records into a memAuditor.
func auditEnv(t *testing.T) (*testEnv, *memAuditor) {
	t.Helper()
	e := newEnv(t)
	if e == nil {
		return nil, nil
	}
	m := &memAuditor{}
	setter, ok := any(e.api).(interface{ SetAuditor(Auditor) })
	if !ok {
		t.Fatal("the control plane takes no auditor (AM7 phase 3 not built)")
	}
	setter.SetAuditor(m)
	return e, m
}

func find(entries []AuditEntry, action string) (AuditEntry, bool) {
	for _, x := range entries {
		if x.Action == action {
			return x, true
		}
	}
	return AuditEntry{}, false
}

func TestAM7TheEgressSwitchLeavesATrail(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{"documents_may_leave": true, "retention_years": 5})
	got := log.take()
	x, ok := find(got, "agent_market.org.documents_may_leave")
	if !ok {
		t.Fatalf("no audit entry for the egress switch: %+v", got)
	}
	if x.Actor != "session:"+uiAdmin.ID || x.ResourceType != "agent_market_org" || x.ResourceID != DefaultOrg || x.Outcome != "success" {
		t.Fatalf("entry = %+v", x)
	}
	if x.Details["from"] != false || x.Details["to"] != true || x.Details["setting"] != "documents_may_leave" ||
		x.Details["by"] != uiAdmin.Email || x.Details["via"] != "admin-ui" {
		t.Fatalf("details = %v", x.Details)
	}
	if lbl, _ := x.Details["label"].(string); !strings.Contains(lbl, "นอกองค์กร") {
		t.Fatalf("label = %q", lbl)
	}
	// an unchanged value is no change: retention was 5 already
	if _, ok := find(got, "agent_market.org.retention_years"); ok || len(got) != 1 {
		t.Fatalf("logged a value that did not change: %+v", got)
	}

	// back off, by the other admin: the entry names them, and the way back
	other := testAdmin{ID: "0b8f5a8e-0000-4000-8000-000000000004", Email: "security@example.test", Name: "Security", Role: "super_admin"}
	e.mustAdmin(other, 200, "PUT", "/org", map[string]any{"documents_may_leave": false})
	x, ok = find(log.take(), "agent_market.org.documents_may_leave")
	if !ok || x.Details["from"] != true || x.Details["to"] != false || x.Actor != "session:"+other.ID ||
		x.Details["by"] != other.Email || x.Details["admin_role"] != "super_admin" {
		t.Fatalf("the second entry = %+v", x)
	}
}

func TestAM7EveryOrgSettingIsLoggedWithItsOldAndNewValue(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	before := orgOf(t, e.mustAdmin(uiAdmin, 200, "GET", "/org", nil))
	change := map[string]any{
		"name": "บริษัท ทดสอบ จำกัด", "access": "list", "domains": []string{"example.org"},
		"user_monthly_credits": 1234567, "contact_name": "ฝ่ายไอที", "contact_email": "it@example.test",
		"documents_may_leave": true, "retention_years": 9, "storage_warn_mb": 777,
	}
	after := orgOf(t, e.mustAdmin(uiAdmin, 200, "PUT", "/org", change))
	got := log.take()
	for field := range change {
		x, ok := find(got, "agent_market.org."+field)
		if !ok {
			t.Fatalf("%s changed without an entry: %+v", field, got)
		}
		if !sameJSON(x.Details["from"], before[field]) || !sameJSON(x.Details["to"], after[field]) {
			t.Fatalf("%s: %v → %v, want %v → %v", field, x.Details["from"], x.Details["to"], before[field], after[field])
		}
	}
}

func TestAM7AChangeTheLogCannotRecordDoesNotHappen(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.mustAdmin(uiAdmin, 200, "GET", "/org", nil) // the organisation exists
	before, _ := e.store.OrgSettingsFor(ctx, DefaultOrg)
	log.setFail(true)
	code, body := e.adminDo(uiAdmin, "PUT", "/org", map[string]any{"documents_may_leave": !before.DocumentsMayLeave})
	if code != 503 {
		t.Fatalf("PUT with the log down = %d %v, want 503", code, body)
	}
	if c, msg := errMsg(body); c != "AUDIT_UNAVAILABLE" || looksEnglish(msg) || !strings.Contains(msg, "ยังไม่ได้เปลี่ยน") {
		t.Fatalf("refusal = %s %q", c, msg)
	}
	after, _ := e.store.OrgSettingsFor(ctx, DefaultOrg)
	if after.DocumentsMayLeave != before.DocumentsMayLeave || !after.UpdatedAt.Equal(before.UpdatedAt) {
		t.Fatalf("the switch moved without a trail: %v → %v", before.DocumentsMayLeave, after.DocumentsMayLeave)
	}
	// the same for a person on the list and a department
	if code, _ := e.adminDo(uiAdmin, "PUT", "/members/somchai@example.test", map[string]any{"role": "admin"}); code != 503 {
		t.Fatalf("member change with the log down = %d", code)
	}
	if _, err := e.store.MemberByEmail(ctx, DefaultOrg, "somchai@example.test"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("a person was added without a trail: %v", err)
	}
	log.setFail(false)
}

func TestAM7EveryAdminChangeIsLogged(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	e.seedDefinition()
	in := e.install()
	run, _ := e.succeededRun(in)
	rep := e.mustDo(200, "POST", "/installs/"+in.ID.String()+"/problem", map[string]any{"text": "ตัวเลขไม่ตรง"})["report"].(map[string]any)
	log.take()

	steps := []struct {
		who                  testAdmin
		method, path         string
		body                 map[string]any
		action, resourceType string
	}{
		{uiAdmin, "PUT", "/members/somchai@example.test", map[string]any{"department": "บัญชี", "role": "publisher"}, "agent_market.member.added", "agent_market_member"},
		{uiAdmin, "PUT", "/members/somchai@example.test", map[string]any{"role": "member"}, "agent_market.member.changed", "agent_market_member"},
		{uiAdmin, "DELETE", "/members/somchai@example.test", nil, "agent_market.member.removed", "agent_market_member"},
		{uiAdmin, "PUT", "/departments/บัญชี", map[string]any{"monthly_credits": 5000}, "agent_market.department.set", "agent_market_department"},
		{uiAdmin, "DELETE", "/departments/บัญชี", nil, "agent_market.department.removed", "agent_market_department"},
		{uiPublisher, "PUT", "/catalog/sran.financial-report", map[string]any{"status": "unpublished"}, "agent_market.catalog.unpublished", "agent_market_agent"},
		{uiAdmin, "PUT", "/catalog/sran.financial-report", map[string]any{"status": "disabled", "reason": "กำลังแก้สูตร"}, "agent_market.catalog.disabled", "agent_market_agent"},
		{uiPublisher, "POST", "/problems/" + rep["id"].(string) + "/close", map[string]any{"note": "แก้แล้ว"}, "agent_market.problem.closed", "agent_market_problem"},
		{uiAdmin, "POST", "/runs/" + run.ID.String() + "/open", map[string]any{"reason": "ตรวจสอบตามคำขอของผู้สอบบัญชี"}, "agent_market.run.opened", "agent_market_run"},
		{uiAdmin, "POST", "/runs/" + run.ID.String() + "/remove-evidence", map[string]any{"reason": "ลูกค้าขอให้ลบไฟล์ทดสอบ"}, "agent_market.run.evidence_removed", "agent_market_run"},
	}
	for _, s := range steps {
		code, out := e.adminDo(s.who, s.method, s.path, s.body)
		if code >= 300 {
			t.Fatalf("%s %s = %d %v", s.method, s.path, code, out)
		}
		got := log.take()
		x, ok := find(got, s.action)
		if !ok {
			t.Fatalf("%s %s: no %s entry in %+v", s.method, s.path, s.action, got)
		}
		if x.ResourceType != s.resourceType || x.Actor != "session:"+s.who.ID || x.Details["by"] != s.who.Email || len(x.ResourceID) > 36 {
			t.Fatalf("%s entry = %+v", s.action, x)
		}
	}
}

// sameJSON compares a logged value with one read off the wire.
func sameJSON(a, b any) bool {
	return jsonText(a) == jsonText(b)
}

func TestAM7TheHTTPAuditorWritesThroughTheBackend(t *testing.T) {
	var got struct {
		Entries []AuditEntry `json:"entries"`
	}
	var auth, user string
	status := http.StatusCreated
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/internal/admin-audit" {
			http.NotFound(w, r)
			return
		}
		auth, user = r.Header.Get("Authorization"), r.Header.Get("X-User-ID")
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.WriteHeader(status)
	}))
	defer srv.Close()
	h := NewHTTPAuditor(srv.URL+"/", func() (string, error) { return "svc-token", nil }, "")
	one := AuditEntry{Actor: "session:a1", Tenant: auditTenant, Action: "agent_market.org.documents_may_leave",
		ResourceType: "agent_market_org", ResourceID: "default", Details: map[string]any{"from": false, "to": true}, Outcome: "success"}
	if err := h.Record(context.Background(), []AuditEntry{one}); err != nil {
		t.Fatal(err)
	}
	if auth != "Bearer svc-token" || user == "" || len(got.Entries) != 1 || got.Entries[0].Action != one.Action ||
		got.Entries[0].Details["to"] != true {
		t.Fatalf("sent auth=%q user=%q entries=%+v", auth, user, got.Entries)
	}
	status = http.StatusUnauthorized
	if err := h.Record(context.Background(), []AuditEntry{one}); err == nil {
		t.Fatal("a refused write reads as written")
	}
	srv.Close()
	if err := h.Record(context.Background(), []AuditEntry{one}); err == nil {
		t.Fatal("an unreachable backend reads as written")
	}
}
