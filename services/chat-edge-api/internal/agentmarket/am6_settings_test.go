package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM6 — nothing about a company lives in code. Until now every setting an
// install keeps was a constant of api.go; the invoice ↔ PO agent declares its
// own in its manifest (install.settings), and the platform validates, labels
// and lists them from there.

const declaringManifest = `{"schema_version":"agent.market.v1","id":"sran.invoice-po-matching","version":"1.0.0",
"name":"ตรวจใบกำกับภาษีกับใบสั่งซื้อ","short_name":"ใบกำกับภาษี ↔ PO","scope":"generic","jurisdiction":"TH",
"python_package":"po_matching","greeting":{"text":"สวัสดีครับ"},
"inputs":[{"role":"po","label":"ใบสั่งซื้อ","required":true,"accept":[".xlsx"]}],
"outputs":[{"format":"xlsx","label":"ผลตรวจ","default":true}],"narrative":"rules",
"capabilities":{"required":["spreadsheet.read.xlsx"]},
"install":{"defaults":{"formats":["xlsx"],"currency":"THB","ap_price_tolerance_pct":"2.00","ap_date_window_days":"0","ap_fuzzy_threshold":"0.80"},
 "settings":[
  {"key":"ap_gr_practice","label":"บริษัทออกใบรับของหรือไม่","kind":"choice",
   "choices":[{"value":"yes","label":"ออกใบรับของ"},{"value":"no","label":"ไม่ออกใบรับของ"}],"help":"ถ้ายังไม่ได้ตั้ง agent จะถามครั้งเดียวในรอบแรก"},
  {"key":"ap_price_tolerance_pct","label":"ราคาต่อหน่วยสูงกว่า PO ได้ไม่เกิน (ร้อยละ)","kind":"percent","min":0,"max":50,"default":"2.00"},
  {"key":"ap_price_tolerance_abs","label":"ส่วนต่างราคารวมต่อบรรทัดไม่เกิน (บาท)","kind":"money","min":0,"max":1000000000,"default":"500.00"},
  {"key":"ap_date_window_days","label":"ใบแจ้งหนี้ลงวันที่ก่อนวันที่ PO ได้ไม่เกิน (วัน)","kind":"int","min":0,"max":366,"default":"0"},
  {"key":"ap_fuzzy_threshold","label":"ความคล้ายของชื่อสินค้าอย่างน้อย (0.50–1.00)","kind":"ratio","min":0.5,"max":1,"default":"0.80"},
  {"key":"ap_unit_conversions","label":"การแปลงหน่วยนับ","kind":"text","max_length":2000,"help":"บรรทัดละหนึ่งรายการ เช่น 1 กล่อง = 12 ชิ้น"},
  {"key":"ap_note","label":"หมายเหตุของฝ่ายบัญชี","kind":"text"}
 ]},
"permissions":{"default":"read_only"},"cannot":"ไม่อนุมัติจ่าย"}`

func (e *testEnv) installDeclaring(settings map[string]any) (int, map[string]any) {
	e.t.Helper()
	if _, err := e.store.UpsertDefinition(context.Background(), Definition{Slug: "sran.invoice-po-matching", Version: "1.0.0",
		Name: "ตรวจใบกำกับภาษีกับใบสั่งซื้อ", Manifest: json.RawMessage(declaringManifest), Checksum: "posum", EngineChecksum: "engsum"}); err != nil {
		e.t.Fatal(err)
	}
	e.heartbeat(fullCaps())
	body := map[string]any{"slug": "sran.invoice-po-matching"}
	if settings != nil {
		body["settings"] = settings
	}
	return e.do("POST", "/installs", body)
}

// declaredOf reads GET /installs/{id}/defaults: the declared settings by key, and the rows by key.
func (e *testEnv) declaredOf(id string) (map[string]map[string]any, map[string]map[string]any) {
	e.t.Helper()
	code, out := e.do("GET", "/installs/"+id+"/defaults", nil)
	if code != 200 {
		e.t.Fatalf("defaults: %d %v", code, out)
	}
	list, ok := out["declared"].([]any)
	if !ok {
		e.t.Fatalf("the settings the agent declares are not listed: %v", out)
	}
	declared, rows := map[string]map[string]any{}, map[string]map[string]any{}
	for _, x := range list {
		m := x.(map[string]any)
		declared[m["key"].(string)] = m
	}
	for _, x := range out["defaults"].([]any) {
		m := x.(map[string]any)
		rows[m["key"].(string)] = m
	}
	return declared, rows
}

func TestAnAgentsDeclaredSettingsAreListedWithTheirValues(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	code, body := e.installDeclaring(map[string]any{"company": "บริษัท ตัวอย่าง จำกัด"})
	if code != 200 {
		t.Fatalf("install: %d %v", code, body)
	}
	id := body["install"].(map[string]any)["id"].(string)
	declared, rows := e.declaredOf(id)
	if len(declared) != 7 {
		t.Fatalf("every declared setting, in the manifest's order: %v", declared)
	}
	// a setting with a default is set when the agent is installed, under the label its manifest gives it
	pct := declared["ap_price_tolerance_pct"]
	if pct["label"] != "ราคาต่อหน่วยสูงกว่า PO ได้ไม่เกิน (ร้อยละ)" || pct["kind"] != "percent" || pct["min"] != 0.0 || pct["max"] != 50.0 ||
		pct["default"] != "2.00" || pct["value"] != "2.00" || pct["set"] != true || pct["choices"] != nil || pct["max_length"] != nil {
		t.Fatalf("a percentage: %v", pct)
	}
	if rows["ap_price_tolerance_pct"]["label"] != "ราคาต่อหน่วยสูงกว่า PO ได้ไม่เกิน (ร้อยละ)" {
		t.Fatalf("the row is labelled as the manifest says: %v", rows["ap_price_tolerance_pct"])
	}
	// one without a default is not set: the agent asks once in its first round
	gr := declared["ap_gr_practice"]
	choices, _ := gr["choices"].([]any)
	if gr["set"] != false || gr["value"] != nil || gr["kind"] != "choice" || len(choices) != 2 ||
		choices[1].(map[string]any)["value"] != "no" || choices[1].(map[string]any)["label"] != "ไม่ออกใบรับของ" || gr["help"] == "" {
		t.Fatalf("a choice nobody made yet: %v", gr)
	}
	if _, kept := rows["ap_gr_practice"]; kept {
		t.Fatalf("a setting nobody set has no row: %v", rows)
	}
	if n := declared["ap_unit_conversions"]["max_length"]; n != 2000.0 {
		t.Fatalf("a text's length: %v", declared["ap_unit_conversions"])
	}
	// the platform's own settings are kept as before, and are not the agent's to declare
	if rows["company"]["label"] != "ชื่อบริษัทบนเอกสาร" || declared["company"] != nil {
		t.Fatalf("the platform's settings: %v", rows["company"])
	}
}

func TestADeclaredSettingIsCheckedByItsKindAndBounds(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	code, body := e.installDeclaring(nil)
	if code != 200 {
		t.Fatalf("install: %d %v", code, body)
	}
	id := body["install"].(map[string]any)["id"].(string)
	put := func(key string, value any) (int, string, string) {
		code, out := e.do("PUT", "/installs/"+id+"/defaults/"+key, map[string]any{"value": value})
		c, m := errMsg(out)
		return code, c, m
	}
	for _, c := range []struct {
		key   string
		value any
		kept  string // what is kept ("" = refused)
		says  []string
	}{
		{"ap_price_tolerance_pct", "3.5", "3.50", nil},
		{"ap_price_tolerance_pct", 12, "12.00", nil},
		{"ap_price_tolerance_pct", "51", "", []string{"ราคาต่อหน่วยสูงกว่า PO ได้ไม่เกิน (ร้อยละ)", "0", "50"}},
		{"ap_price_tolerance_pct", "-1", "", []string{"ราคาต่อหน่วยสูงกว่า PO"}},
		{"ap_price_tolerance_pct", "2.345", "", []string{"ทศนิยมไม่เกิน 2 ตำแหน่ง"}},
		{"ap_price_tolerance_pct", "สองเปอร์เซ็นต์", "", []string{"ตัวเลข"}},
		{"ap_price_tolerance_pct", true, "", []string{"ตัวเลข"}},
		{"ap_price_tolerance_abs", "1,250.5", "1250.50", nil},
		{"ap_price_tolerance_abs", "1000000001", "", []string{"ส่วนต่างราคารวมต่อบรรทัดไม่เกิน (บาท)", "1,000,000,000"}},
		{"ap_date_window_days", "7", "7", nil},
		{"ap_date_window_days", 30, "30", nil},
		{"ap_date_window_days", "7.5", "", []string{"จำนวนเต็ม", "366"}},
		{"ap_date_window_days", "367", "", []string{"จำนวนเต็ม", "366"}},
		{"ap_fuzzy_threshold", "0.85", "0.85", nil},
		{"ap_fuzzy_threshold", "0.4", "", []string{"ความคล้ายของชื่อสินค้า", "0.5", "1"}},
		{"ap_gr_practice", "no", "no", nil},
		{"ap_gr_practice", "maybe", "", []string{"บริษัทออกใบรับของหรือไม่", "ออกใบรับของ", "ไม่ออกใบรับของ"}},
		{"ap_gr_practice", 1, "", []string{"บริษัทออกใบรับของหรือไม่"}},
		{"ap_unit_conversions", "1 กล่อง = 12 ชิ้น\nA-100: 1 ลัง = 24 ขวด", "1 กล่อง = 12 ชิ้น\nA-100: 1 ลัง = 24 ขวด", nil},
		{"ap_unit_conversions", strings.Repeat("ก", 2001), "", []string{"การแปลงหน่วยนับ", "2,000"}},
		{"ap_note", strings.Repeat("ก", 2000), strings.Repeat("ก", 2000), nil},
		{"ap_note", strings.Repeat("ก", 2001), "", []string{"หมายเหตุของฝ่ายบัญชี", "2,000"}},
		{"ap_note", []string{"a"}, "", []string{"หมายเหตุของฝ่ายบัญชี"}},
	} {
		code, errCode, msg := put(c.key, c.value)
		if c.kept == "" {
			if code != 400 || errCode != "BAD_SETTING" || !ReadsForPerson(msg) {
				t.Fatalf("%s = %v: %d %s %q", c.key, c.value, code, errCode, msg)
			}
			for _, s := range c.says {
				if !strings.Contains(msg, s) {
					t.Fatalf("%s = %v: the sentence names the setting and what is allowed (%q): %q", c.key, c.value, s, msg)
				}
			}
			continue
		}
		if code != 200 {
			t.Fatalf("%s = %v: %d %s %q", c.key, c.value, code, errCode, msg)
		}
		declared, _ := e.declaredOf(id)
		if d := declared[c.key]; d["value"] != c.kept || d["set"] != true {
			t.Fatalf("%s = %v is kept as %q: %v", c.key, c.value, c.kept, d)
		}
	}
	// a refused value changed nothing
	declared, _ := e.declaredOf(id)
	if declared["ap_price_tolerance_pct"]["value"] != "12.00" {
		t.Fatalf("the value before the refused one: %v", declared["ap_price_tolerance_pct"])
	}
	// un-set: the default is the manifest's again, and the setting says it is not set
	code, out := e.do("DELETE", "/installs/"+id+"/defaults/ap_price_tolerance_pct", nil)
	if code != 200 || out["removed"] != true {
		t.Fatalf("un-set: %d %v", code, out)
	}
	declared, _ = e.declaredOf(id)
	if d := declared["ap_price_tolerance_pct"]; d["set"] != false || d["value"] != nil || d["default"] != "2.00" {
		t.Fatalf("un-set: %v", d)
	}
	// a key nobody declared is not a setting; the platform's own keep working
	if code, c, _ := put("ap_anything", "1"); code != 400 || c != "BAD_KEY" {
		t.Fatalf("a key nobody declared: %d %s", code, c)
	}
	if code, c, m := put("company", "บริษัท ตัวอย่าง จำกัด"); code != 200 {
		t.Fatalf("the platform's own setting: %d %s %q", code, c, m)
	}
	if code, c, m := put("company_tax_id", "0105545009870"); code != 400 || c != "BAD_VALUE" || !strings.Contains(m, "เลขประจำตัวผู้เสียภาษี") {
		t.Fatalf("the platform's own check: %d %s %q", code, c, m)
	}
}

// A setting is the agent's that declares it: another agent's install does not take it.
func TestADeclaredSettingBelongsToTheAgentThatDeclaresIt(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	other := e.install() // the financial report declares none
	code, out := e.do("PUT", "/installs/"+other.ID.String()+"/defaults/ap_price_tolerance_pct", map[string]any{"value": "2.00"})
	if c, _ := errMsg(out); code != 400 || c != "BAD_KEY" {
		t.Fatalf("another agent's setting: %d %v", code, out)
	}
	code, out = e.do("GET", "/installs/"+other.ID.String()+"/defaults", nil)
	if list, ok := out["declared"].([]any); code != 200 || !ok || len(list) != 0 {
		t.Fatalf("an agent that declares nothing lists nothing: %d %v", code, out)
	}
	// a value refused at install is refused, not dropped
	code, out = e.installDeclaring(map[string]any{"ap_price_tolerance_pct": "70"})
	if c, m := errMsg(out); code != 400 || c != "BAD_SETTING" || !strings.Contains(m, "ราคาต่อหน่วยสูงกว่า PO") {
		t.Fatalf("a setting out of its bounds at install: %d %v", code, out)
	}
	code, out = e.installDeclaring(map[string]any{"ap_price_tolerance_pct": "5", "ap_gr_practice": "yes"})
	if code != 200 {
		t.Fatalf("install: %d %v", code, out)
	}
	declared, _ := e.declaredOf(out["install"].(map[string]any)["id"].(string))
	if declared["ap_price_tolerance_pct"]["value"] != "5.00" || declared["ap_gr_practice"]["value"] != "yes" ||
		declared["ap_date_window_days"]["value"] != "0" {
		t.Fatalf("what the person set at install, over the manifest's defaults: %v", declared)
	}
}

func TestCatalogInstallAndDatasheetSayScopeAndJurisdiction(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	plain := e.install() // its manifest says neither
	code, body := e.installDeclaring(nil)
	if code != 200 {
		t.Fatalf("install: %d %v", code, body)
	}
	said := func(where string, m map[string]any, scope, jurisdiction string) {
		t.Helper()
		if m["scope"] != scope || m["jurisdiction"] != jurisdiction {
			t.Fatalf("%s: scope %v jurisdiction %v, want %q %q", where, m["scope"], m["jurisdiction"], scope, jurisdiction)
		}
	}
	made := body["install"].(map[string]any)
	said("the install as it is made", made, "generic", "TH")
	id := made["id"].(string)
	_, got := e.do("GET", "/installs/"+id, nil)
	said("the install", got["install"].(map[string]any), "generic", "TH")
	_, got = e.do("GET", "/installs/"+plain.ID.String(), nil)
	// an agent that does not say is not offered as generic
	said("an install of an agent that says nothing", got["install"].(map[string]any), "org-specific", "")
	_, list := e.do("GET", "/installs", nil)
	for _, x := range list["installs"].([]any) {
		m := x.(map[string]any)
		if uuid.MustParse(m["id"].(string)) == plain.ID {
			said("the list of installs", m, "org-specific", "")
		} else {
			said("the list of installs", m, "generic", "TH")
		}
	}
	_, cat := e.do("GET", "/catalog", nil)
	seen := 0
	for _, x := range cat["agents"].([]any) {
		m := x.(map[string]any)
		switch m["slug"] {
		case "sran.invoice-po-matching":
			said("the catalog", m, "generic", "TH")
			seen++
		case "sran.financial-report":
			said("the catalog", m, "org-specific", "")
			seen++
		}
	}
	if seen != 2 {
		t.Fatalf("catalog: %v", cat)
	}
	_, item := e.do("GET", "/catalog/sran.invoice-po-matching", nil)
	said("the agent's page", item, "generic", "TH")
	_, sheet := e.do("GET", "/catalog/sran.invoice-po-matching/datasheet", nil)
	said("the data sheet", sheet, "generic", "TH")
	_, sheet = e.do("GET", "/catalog/sran.financial-report/datasheet", nil)
	said("the data sheet of an agent that says nothing", sheet, "org-specific", "")
}
