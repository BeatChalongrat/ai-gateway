package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM5 phase E — the bank reconciliation's gate, its sign-off and its settings.
//
//   - A round whose package says its work does not pass (the two adjusted
//     balances differ) is not a success: what the person reads is the
//     package's own sentence — "ยังไม่ลงตัว ต่างกัน X บาท" — and what to do
//     next, never an error's text.
//   - A reconciliation is signed by a reviewer who is not its preparer.
//   - The tolerances of the match are the install's settings.

const bankManifest = `{"schema_version":"agent.market.v1","id":"sran.bank-reconciliation","version":"1.0.0","name":"กระทบยอดเงินฝากธนาคาร",
"short_name":"กระทบยอดธนาคาร","python_package":"bank_reconciliation","greeting":{"text":"สวัสดีครับ"},
"inputs":[{"role":"bank","label":"รายการเดินบัญชี","required":true},{"role":"book","label":"สมุดบัญชี","required":true}],
"outputs":[{"format":"xlsx","label":"งบกระทบยอด","default":true}],
"capabilities":{"required":["document.match.e3","spreadsheet.read.xlsx"],"optional":[]},
"install":{"defaults":{"formats":["xlsx"],"match_date_window_days":"3"}},"narrative":"rules","completion_gate":true,
"review":{"separate_reviewer":true},"permissions":{"default":"read_only"},"cannot":"อ่านอย่างเดียว"}`

func mustUUID(t *testing.T, s string) uuid.UUID {
	t.Helper()
	id, err := uuid.Parse(s)
	if err != nil {
		t.Fatalf("%q: %v", s, err)
	}
	return id
}

func (e *testEnv) installBank() Install {
	e.t.Helper()
	ctx := context.Background()
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: "sran.bank-reconciliation", Version: "1.0.0", Name: "กระทบยอดเงินฝากธนาคาร",
		Manifest: json.RawMessage(bankManifest), Checksum: "banksum", EngineChecksum: "engsum"}); err != nil {
		e.t.Fatal(err)
	}
	caps := fullCaps()
	caps["document.match.e3"] = Binding{Capability: "document.match.e3", Impl: "engine:e3", Version: "z"}
	e.heartbeat(caps)
	out := e.mustDo(200, "POST", "/installs", map[string]any{"slug": "sran.bank-reconciliation"})
	in, err := e.store.InstallByID(ctx, mustUUID(e.t, out["install"].(map[string]any)["id"].(string)))
	if err != nil {
		e.t.Fatal(err)
	}
	return in
}

func TestTheManifestSaysItsGateAndWhoMaySignIt(t *testing.T) {
	m, err := ParseManifest(json.RawMessage(bankManifest))
	if err != nil {
		t.Fatal(err)
	}
	if !m.CompletionGate || !m.Review.SeparateReviewer {
		t.Fatalf("completion_gate %v · separate_reviewer %v", m.CompletionGate, m.Review.SeparateReviewer)
	}
	plain, _ := ParseManifest(json.RawMessage(testManifest))
	if plain.CompletionGate || plain.Review.SeparateReviewer {
		t.Fatal("a package that says neither has neither")
	}
}

func TestAGateThatDoesNotPassReadsAsThePackageSaidIt(t *testing.T) {
	facts := FailureFacts{Kind: KindRun, HasFiles: true, KeepDays: 7, Credits: 3,
		GateWhat: "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท", GateNext: "ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร", Draft: true}
	f := FailureFor("not_tied", facts)
	if f.What != "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท" || f.Next != "ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร" {
		t.Fatalf("the package's own sentences: %q · %q", f.What, f.Next)
	}
	if f.CanRetry {
		t.Fatal("the same files give the same difference: trying again is not offered")
	}
	if !strings.Contains(f.Files, "ร่าง") || !strings.Contains(f.Files, "7 วัน") {
		t.Fatalf("the draft was delivered and the files are kept: %q", f.Files)
	}
	if !strings.Contains(f.Charged, "3") {
		t.Fatalf("what was charged: %q", f.Charged)
	}
	if !strings.HasPrefix(f.Text(), "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท") {
		t.Fatalf("the round's message starts with it: %q", f.Text())
	}
	// a package cannot put an error's text on the screen through its gate
	for _, bad := range []string{"TypeError: 'NoneType' object is not iterable", "compute_failed: KeyError 'statement'", ""} {
		g := FailureFor("not_tied", FailureFacts{Kind: KindRun, HasFiles: true, GateWhat: bad, GateNext: bad, Draft: true})
		if g.What == "" || g.Next == "" || LooksTechnical(g.What) || LooksTechnical(g.Next) || strings.Contains(g.What, "Error") {
			t.Fatalf("%q reached the person: %q · %q", bad, g.What, g.Next)
		}
	}
	// a reason the runner has no sentence for still reads as a round that did not pass
	h := FailureFor("some_other_gate", FailureFacts{Kind: KindRun, HasFiles: true, GateWhat: "งานยังไม่ครบ", GateNext: "ส่งไฟล์ที่ขาด", Draft: false})
	if h.What != "งานยังไม่ครบ" || h.CanRetry {
		t.Fatalf("%+v", h)
	}
	found := false
	for _, r := range FailureReasons() {
		found = found || r == "not_tied"
	}
	if !found {
		t.Fatal("not_tied has a sentence of its own")
	}
}

func TestAReconciliationIsSignedBySomeoneWhoDidNotPrepareIt(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.installBank()
	e.mustDo(200, "PUT", "/admin/org", map[string]any{"domains": []string{"example.test"}})
	run, _ := e.succeededRun(in)
	// the preparer cannot review their own reconciliation — whatever name they type
	for _, body := range []map[string]any{{}, {"name": "สมชาย ผู้ตรวจ"}} {
		code, out := e.do("POST", "/runs/"+run.ID.String()+"/review", body)
		if c, m := errMsg(out); code != 409 || c != "SAME_PERSON" || !strings.Contains(m, "คนละคน") {
			t.Fatalf("the preparer signing their own work: %d %v", code, out)
		}
	}
	var n int
	_ = e.store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM agent_market.run_reviews WHERE run_id = $1`, run.ID).Scan(&n)
	if n != 0 {
		t.Fatalf("nothing was recorded: %d", n)
	}
	// a colleague the run was shared with, with the right to review
	out := e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/shares", map[string]any{"email": "reviewer@example.test", "can_review": true})
	sid := out["share"].(map[string]any)["id"].(string)
	out = e.mustDo(200, "POST", "/shared/"+sid+"/review", map[string]any{}, as("reviewer@example.test")...)
	if out["review"].(map[string]any)["reviewer_email"] != "reviewer@example.test" {
		t.Fatalf("the reviewer: %v", out)
	}
	var p map[string]any
	j, _ := e.store.RunByID(context.Background(), mustUUID(t, out["job"].(map[string]any)["id"].(string)))
	_ = json.Unmarshal(j.Params, &p)
	if p["preparer"] == p["reviewer"] || p["reviewer"] == "" {
		t.Fatalf("two names on the stamp: %v", p)
	}
	// an agent that does not ask for it keeps AM3's rule: its owner may sign
	fin := e.install()
	own, _ := e.succeededRun(fin)
	e.mustDo(200, "POST", "/runs/"+own.ID.String()+"/review", map[string]any{"name": "ผู้ตรวจ"})
}

func TestTheTolerancesOfTheMatchAreInstallSettings(t *testing.T) {
	for _, c := range []struct {
		key, value string
		ok         bool
	}{
		{"match_amount_tolerance", `"1.00"`, true}, {"match_amount_tolerance", `0`, true}, {"match_amount_tolerance", `"0.50"`, true},
		{"match_amount_tolerance", `"มาก"`, false}, {"match_amount_tolerance", `"1000.01"`, false}, {"match_amount_tolerance", `"-1"`, false},
		{"match_date_window_days", `"3"`, true}, {"match_date_window_days", `7`, true}, {"match_date_window_days", `"32"`, false},
		{"match_date_window_days", `"2.5"`, false},
		{"match_fuzzy_threshold", `"0.80"`, true}, {"match_fuzzy_threshold", `"0.49"`, false}, {"match_fuzzy_threshold", `"1.01"`, false},
		{"match_fuzzy_window_days", `"35"`, true}, {"match_fuzzy_window_days", `"367"`, false},
		{"match_ask_above", `"50000.00"`, true}, {"match_ask_above", `"50,000"`, true}, {"match_ask_above", `"-5"`, false},
		{"match_max_group", `"4"`, true}, {"match_max_group", `"1"`, false}, {"match_max_group", `"7"`, false},
		{"match_auto_same_day", `"true"`, true}, {"match_auto_same_day", `false`, true}, {"match_auto_same_day", `"บางครั้ง"`, false},
		{"bank_name", `"ธนาคารกสิกรไทย"`, true}, {"bank_account_number", `"123-4-56789-0"`, true},
		{"bank_account_number", `"` + strings.Repeat("1", 41) + `"`, false}, {"bank_account_name", `"บจก. ตัวอย่าง"`, true},
		{"bank_gl_code", `"1120-01"`, true}, {"bank_gl_code", `{"a":1}`, false},
	} {
		if !validSettingKey(c.key) {
			t.Fatalf("%s is a setting an install keeps", c.key)
		}
		if got := validSettingValue(c.key, json.RawMessage(c.value)); got != c.ok {
			t.Fatalf("%s = %s: valid %v, want %v", c.key, c.value, got, c.ok)
		}
		if l := settingLabel(c.key); l == c.key || LooksTechnical(l) {
			t.Fatalf("%s is named in words: %q", c.key, l)
		}
	}
	if validSettingKey("match_anything") || validSettingKey("bank_pin") {
		t.Fatal("only the settings the engine reads")
	}
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.installBank()
	e.mustDo(200, "PUT", "/installs/"+in.ID.String()+"/defaults/match_date_window_days", map[string]any{"value": "7"})
	code, out := e.do("PUT", "/installs/"+in.ID.String()+"/defaults/match_date_window_days", map[string]any{"value": "400"})
	if c, m := errMsg(out); code != 400 || c != "BAD_VALUE" || !strings.Contains(m, "วันที่ห่างกัน") {
		t.Fatalf("a window the engine cannot use is refused where it is typed: %d %v", code, out)
	}
	d := e.mustDo(200, "GET", "/installs/"+in.ID.String(), nil)
	seen := map[string]any{}
	for _, x := range d["defaults"].([]any) {
		m := x.(map[string]any)
		seen[m["key"].(string)] = m["value"]
		if strings.HasPrefix(m["key"].(string), "match_") && (m["label"] == m["key"] || m["label"] == "") {
			t.Fatalf("a setting without words: %v", m)
		}
	}
	if seen["match_date_window_days"] != "7" {
		t.Fatalf("the install's own window: %v", seen)
	}
}
