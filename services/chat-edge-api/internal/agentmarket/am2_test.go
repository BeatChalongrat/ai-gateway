package agentmarket

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"mime/multipart"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// AM2: the agent's manifest decides what may be uploaded, and the duplicate
// index (migration 002) is per install, expires, and goes with the install.

const invoiceManifest = `{"schema_version":"agent.market.v1","id":"sran.invoice-processing","version":"1.0.0","name":"ใบกำกับภาษี",
"python_package":"invoice_processing","greeting":{"text":"สวัสดีครับ"},
"inputs":[{"role":"documents","label":"เอกสาร","required":true,"accept":[".pdf",".xml",".jpg",".jpeg",".png"]}],
"outputs":[{"format":"xlsx","label":"ทะเบียน","default":true}],
"capabilities":{"required":["spreadsheet.read.xlsx"]},"install":{"defaults":{"formats":["xlsx"]}},
"narrative":"rules","document_index":{"retention_days":730},"permissions":{"default":"read_only"},"cannot":"อ่านอย่างเดียว"}`

func TestManifestSaysWhatItReadsAndKeeps(t *testing.T) {
	m, err := ParseManifest(json.RawMessage(invoiceManifest))
	if err != nil {
		t.Fatal(err)
	}
	if got := m.UploadTypes(); len(got) != 5 || got[0] != ".pdf" || got[2] != ".jpg" {
		t.Fatalf("upload types: %v", got)
	}
	if m.IndexRetention() != 730*24*time.Hour || m.Narrative != "rules" {
		t.Fatalf("retention %v narrative %q", m.IndexRetention(), m.Narrative)
	}
	old, _ := ParseManifest(json.RawMessage(testManifest))
	if got := old.UploadTypes(); len(got) != len(defaultUploadTypes) || old.IndexRetention() != 0 || old.Narrative != "" {
		t.Fatalf("an agent that declares nothing keeps AM1's behaviour: %v %v", got, old.IndexRetention())
	}
}

func (e *testEnv) upload(in Install, name string) int {
	e.t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, _ := mw.CreateFormFile("file", name)
	_, _ = fw.Write([]byte("%PDF-1.4 not really"))
	_ = mw.Close()
	req, _ := http.NewRequest("POST", e.srv.URL+"/agent-market/installs/"+in.ID.String()+"/files", &buf)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	resp.Body.Close()
	return resp.StatusCode
}

func TestUploadAcceptsWhatTheAgentReads(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	fin := e.install()
	if code := e.upload(fin, "receipt.jpg"); code != http.StatusUnsupportedMediaType {
		t.Fatalf("the financial report takes no photos: %d", code)
	}
	if code := e.upload(fin, "gl.xlsx"); code != http.StatusOK {
		t.Fatalf("the financial report takes .xlsx: %d", code)
	}
	if _, err := e.store.UpsertDefinition(context.Background(), Definition{Slug: "sran.invoice-processing", Version: "1.0.0",
		Name: "ใบกำกับภาษี", Manifest: json.RawMessage(invoiceManifest), Checksum: "inv", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.invoice-processing"})
	if code != 200 {
		t.Fatalf("install %d %v", code, body)
	}
	inv, _ := e.store.GetInstall(context.Background(), e.user.TenantUUID, e.user.UserID,
		uuid.MustParse(body["install"].(map[string]any)["id"].(string)))
	for _, name := range []string{"receipt.jpg", "slip.PNG", "etax.xml", "invoice.pdf"} {
		if code := e.upload(inv, name); code != http.StatusOK {
			t.Fatalf("the invoice agent takes %s: %d", name, code)
		}
	}
	if code := e.upload(inv, "ledger.xlsx"); code != http.StatusUnsupportedMediaType {
		t.Fatalf("the invoice agent does not take a spreadsheet: %d", code)
	}
}

func strp(s string) *string { return &s }

func TestDocumentIndexIsPerInstallExpiresAndGoesWithTheInstall(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	a := e.install()
	run1 := Run{ID: uuid.New(), InstallID: a.ID, TenantID: a.TenantID, UserID: a.UserID}
	entries := []IndexEntry{
		{DocKey: "d1", Name: "a.pdf", SHA256: "aa", SellerTaxID: strp("0105545009875"), DocumentNumber: strp("IV1"),
			DocumentDate: strp("2026-09-15"), Total: strp("12850.70")},
		{DocKey: "d2", Name: "b.pdf", SHA256: "bb", Total: strp("100.00")},
	}
	if n, err := e.store.AddDocumentIndex(ctx, run1, entries, 730*24*time.Hour); err != nil || n != 2 {
		t.Fatalf("add: %d %v", n, err)
	}
	if n, _ := e.store.AddDocumentIndex(ctx, run1, entries, 730*24*time.Hour); n != 0 {
		t.Fatalf("a resumed run writes nothing twice: %d", n)
	}
	got, err := e.store.DocumentIndex(ctx, a.ID, uuid.New(), 100)
	if err != nil || len(got) != 2 {
		t.Fatalf("history for another run of the install: %v %v", got, err)
	}
	for _, g := range got {
		if g.DocKey == "d1" && (g.DocumentDate == nil || *g.DocumentDate != "2026-09-15" || g.Total == nil || *g.Total != "12850.70" ||
			*g.SellerTaxID != "0105545009875") {
			t.Fatalf("the values the duplicate check compares: %+v", g)
		}
	}
	if own, _ := e.store.DocumentIndex(ctx, a.ID, run1.ID, 100); len(own) != 0 {
		t.Fatalf("a run never sees its own documents as history: %v", own)
	}
	other := uuid.New()
	if b, _ := e.store.DocumentIndex(ctx, other, uuid.New(), 100); len(b) != 0 {
		t.Fatalf("another install sees nothing: %v", b)
	}
	// expired rows are not history and are purged
	run2 := Run{ID: uuid.New(), InstallID: a.ID, TenantID: a.TenantID, UserID: a.UserID}
	if _, err := e.store.AddDocumentIndex(ctx, run2, []IndexEntry{{DocKey: "d3", Name: "c.pdf", SHA256: "cc"}}, -time.Hour); err != nil {
		t.Fatal(err)
	}
	if h, _ := e.store.DocumentIndex(ctx, a.ID, uuid.New(), 100); len(h) != 2 {
		t.Fatalf("an expired row is not history: %d", len(h))
	}
	if n, err := e.store.PurgeExpiredIndex(ctx); err != nil || n != 1 {
		t.Fatalf("purge: %d %v", n, err)
	}
	// removing the install removes its index at once
	if err := e.store.RemoveInstall(ctx, a.TenantID, a.UserID, a.ID); err != nil {
		t.Fatal(err)
	}
	var left int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.document_index WHERE install_id = $1`, a.ID).Scan(&left)
	if left != 0 {
		t.Fatalf("index rows left after uninstall: %d", left)
	}
}

func TestCompanyTaxIDIsCheckedBeforeItIsKept(t *testing.T) {
	for _, c := range []struct {
		key, value string
		ok         bool
	}{
		{"company_tax_id", `"0105563012343"`, true}, {"company_tax_id", `"0105545009875"`, true},
		{"company_tax_id", `"0105545009870"`, false}, {"company_tax_id", `"010556301234"`, false},
		{"company_tax_id", `"01055630123a3"`, false}, {"company_tax_id", `105563012343`, false},
		{"company_branch", `"00000"`, true}, {"company_branch", `"00012"`, true}, {"company_branch", `"12"`, false},
		{"company", `"anything"`, true},
	} {
		if got := validSettingValue(c.key, json.RawMessage(c.value)); got != c.ok || !validSettingKey(c.key) {
			t.Fatalf("%s=%s: %v", c.key, c.value, got)
		}
	}
}

func TestExpensePolicyIsCheckedBeforeItIsKept(t *testing.T) {
	good := `{"schema":"am2.expense_policy.v1","currency":"THB","receipt_required_above":"500.00","approval_threshold":"3,000",
"categories":{"meals":{"label":"ค่าอาหาร","per_claim":"1500.00","per_person_month":5000},
"contractor":{"per_claim":"20000","wht_rate":"3","wht_from":"1000"}},"prohibited_merchants":["ผับ"],"weekend_holiday":"flag"}`
	if p := PolicyProblems(json.RawMessage(good)); len(p) != 0 || !validSettingValue("policy", json.RawMessage(good)) {
		t.Fatalf("a usable table: %v", p)
	}
	for _, c := range []struct{ table, want string }{
		{`{"schema":"am2.expense_policy.v1","receipt_required_above":"500","approval_threshold":"3000","categories":{"meals":{"per_claim":"ประมาณ 2,000"}}}`, "categories.meals.per_claim"},
		{`{"schema":"am2.expense_policy.v1","receipt_required_above":"500","approval_threshold":"3000","categories":{}}`, "categories:"},
		{`{"schema":"am2.expense_policy.v1","approval_threshold":"3000","categories":{"meals":{"per_claim":"1500"}}}`, "receipt_required_above"},
		{`{"schema":"am2.expense_policy.v1","receipt_required_above":"500","approval_threshold":"3000","categories":{"c":{"wht_rate":"3"}}}`, "categories.c.wht_from"},
		{`{"schema":"am2.expense_policy.v1","receipt_required_above":"500","approval_threshold":"3000","categories":{"c":{"wht_rate":"300","wht_from":"1"}}}`, "categories.c.wht_rate"},
		{`{"schema":"am2.expense_policy.v1","receipt_required_above":"500","approval_threshold":"3000","currency":"USD","categories":{"m":{}}}`, "currency"},
		{`{"schema":"am2.expense_policy.v1","receipt_required_above":"500","approval_threshold":"3000","weekend_holiday":"reject","categories":{"m":{}}}`, "weekend_holiday"},
		{`{"schema":"am2.expense_policy.v1","receipt_required_above":"500","approval_threshold":"3000","prohibited_merchants":"ผับ","categories":{"m":{}}}`, "prohibited_merchants"},
		{`{"receipt_required_above":"500","approval_threshold":"3000","categories":{"m":{}}}`, "schema"},
		{`[1,2]`, "JSON object"},
	} {
		p := strings.Join(PolicyProblems(json.RawMessage(c.table)), " | ")
		if !strings.Contains(p, c.want) || validSettingValue("policy", json.RawMessage(c.table)) {
			t.Fatalf("%s: want a problem about %q, got %q", c.table, c.want, p)
		}
	}
	for v, ok := range map[string]bool{`{"2026-10-13":"วันนวมินทรมหาราช"}`: true, `{"schema":"am2.holidays.v1","dates":{"2026-10-23":"x"}}`: true,
		`{"13/10/2569":"x"}`: false, `{}`: false, `null`: false, `["2026-10-13"]`: false} {
		if validSettingValue("holidays", json.RawMessage(v)) != ok {
			t.Fatalf("holidays %s: want %v", v, ok)
		}
	}
}

func TestPolicySettingSaysWhatIsWrong(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	bad := json.RawMessage(`{"schema":"am2.expense_policy.v1","receipt_required_above":"500","approval_threshold":"3000","categories":{"meals":{"per_claim":"ประมาณ 2,000"}}}`)
	code, body := e.do("PUT", "/installs/"+in.ID.String()+"/defaults/policy", map[string]any{"value": bad})
	if code != 400 || !strings.Contains(fmt.Sprint(body), "categories.meals.per_claim") {
		t.Fatalf("a limit that is not an amount: %d %v", code, body)
	}
	// at install too: refused and said, never silently dropped
	code, body = e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report", "idempotency_key": "am2-bad-policy",
		"settings": map[string]any{"policy": bad}})
	if code != 400 || !strings.Contains(fmt.Sprint(body), "categories.meals.per_claim") {
		t.Fatalf("install with a bad policy: %d %v", code, body)
	}
	code, _ = e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report", "idempotency_key": "am2-bad-tax",
		"settings": map[string]any{"company_tax_id": "0105563012340", "unknown_key": 1}})
	if code != 400 {
		t.Fatalf("install with a bad tax ID: %d", code)
	}
}
