package agentmarket

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// ── The expense policy table (AM2 Phase F) ───────────────────────────────
//
// The Expense Audit agent takes every limit from this structured table,
// never from a document. It is checked here when it is saved, with the same
// rules the package applies when it loads it (expense_audit/checks/policy.py
// load_policy): a person hears what is wrong at once instead of at the next
// run. A money value is a plain amount — "ประมาณ 2,000" is not one.

var (
	policyCode  = regexp.MustCompile(`^[A-Za-z0-9_\-]+$`)
	policyMoney = regexp.MustCompile(`^\d+(?:\.\d+)?$`)
)

// PolicyProblems lists what is wrong with a policy table (none = usable).
func PolicyProblems(raw json.RawMessage) []string {
	var p map[string]json.RawMessage
	if json.Unmarshal(raw, &p) != nil || p == nil {
		return []string{"ตารางนโยบายการเบิกต้องเป็นตาราง (JSON object)"}
	}
	var out []string
	if s := str(p["schema"]); s != "am2.expense_policy.v1" {
		out = append(out, "schema: ต้องเป็น am2.expense_policy.v1")
	}
	var cats map[string]json.RawMessage
	if json.Unmarshal(p["categories"], &cats) != nil || len(cats) == 0 {
		out = append(out, "categories: ยังไม่มีหมวดค่าใช้จ่าย")
	}
	codes := make([]string, 0, len(cats))
	for c := range cats {
		codes = append(codes, c)
	}
	sort.Strings(codes)
	for _, code := range codes {
		where := "categories." + code
		var c map[string]json.RawMessage
		if !policyCode.MatchString(code) || json.Unmarshal(cats[code], &c) != nil || c == nil {
			out = append(out, where+": รหัสหมวดต้องเป็นตัวอักษรอังกฤษ ตัวเลข หรือ _ และมีรายละเอียดเป็นตาราง")
			continue
		}
		for _, k := range []string{"per_claim", "per_person_month", "wht_from"} {
			out = append(out, policyAmount(c[k], where+"."+k, false)...)
		}
		if r, ok := c["wht_rate"]; ok && !isBlank(r) {
			if v, err := strconv.ParseFloat(strings.TrimSuffix(strings.TrimSpace(plain(r)), "%"), 64); err != nil || v <= 0 || v > 100 {
				out = append(out, where+".wht_rate: ต้องเป็นอัตราร้อยละระหว่าง 0 ถึง 100")
			} else if _, has := c["wht_from"]; !has {
				out = append(out, where+".wht_from: มีอัตราหัก ณ ที่จ่ายแต่ยังไม่ได้กำหนดว่าหักตั้งแต่ยอดเท่าใด")
			}
		}
	}
	out = append(out, policyAmount(p["receipt_required_above"], "receipt_required_above", true)...)
	out = append(out, policyAmount(p["approval_threshold"], "approval_threshold", true)...)
	if v, ok := p["weekend_holiday"]; ok && !oneOf(str(v), "flag", "exception", "allow") {
		out = append(out, "weekend_holiday: ใช้ได้ flag, exception, allow")
	}
	if v, ok := p["vat_claim_requires"]; ok && !oneOf(str(v), "full_tax_invoice", "abbreviated_tax_invoice") {
		out = append(out, "vat_claim_requires: ใช้ได้ full_tax_invoice, abbreviated_tax_invoice")
	}
	if v, ok := p["currency"]; ok && str(v) != "THB" {
		out = append(out, "currency: การเบิกตรวจเป็นเงินบาทเท่านั้น")
	}
	if v, ok := p["prohibited_merchants"]; ok {
		var words []any
		good := json.Unmarshal(v, &words) == nil
		for _, w := range words {
			s, isStr := w.(string)
			good = good && isStr && strings.TrimSpace(s) != ""
		}
		if !good {
			out = append(out, "prohibited_merchants: ต้องเป็นรายการคำ")
		}
	}
	return out
}

// HolidayProblems checks an install's holiday calendar: {"YYYY-MM-DD": name}
// (or {"dates": {...}}).
func HolidayProblems(raw json.RawMessage) []string {
	var cal map[string]json.RawMessage
	if json.Unmarshal(raw, &cal) != nil || len(cal) == 0 {
		return []string{"holidays: ต้องเป็นตารางวันที่กับชื่อวันหยุด"}
	}
	if d, ok := cal["dates"]; ok {
		cal = nil
		if json.Unmarshal(d, &cal) != nil || len(cal) == 0 {
			return []string{"holidays.dates: ต้องเป็นตารางวันที่กับชื่อวันหยุด"}
		}
	}
	var out []string
	for k := range cal {
		if !isoDay(k) {
			out = append(out, fmt.Sprintf("holidays: %q ไม่ใช่วันที่ (YYYY-MM-DD)", k))
		}
	}
	sort.Strings(out)
	return out
}

func isoDay(s string) bool {
	if len(s) != 10 || s[4] != '-' || s[7] != '-' {
		return false
	}
	y, e1 := strconv.Atoi(s[:4])
	m, e2 := strconv.Atoi(s[5:7])
	d, e3 := strconv.Atoi(s[8:])
	return e1 == nil && e2 == nil && e3 == nil && y >= 1900 && m >= 1 && m <= 12 && d >= 1 && d <= 31
}

func policyAmount(v json.RawMessage, where string, required bool) []string {
	if isBlank(v) {
		if required {
			return []string{where + ": ยังไม่ได้กำหนด"}
		}
		return nil
	}
	t := strings.TrimSpace(strings.NewReplacer(",", "", "บาท", "", "฿", "").Replace(arabicDigits(plain(v))))
	if string(v) == "true" || string(v) == "false" || !policyMoney.MatchString(t) {
		return []string{where + ": ไม่ใช่จำนวนเงิน"}
	}
	return nil
}

// arabicDigits reads Thai digits as the package does (engine/e2 thai_text.arabic).
func arabicDigits(s string) string {
	return strings.Map(func(r rune) rune {
		if r >= 0x0E50 && r <= 0x0E59 {
			return '0' + (r - 0x0E50)
		}
		return r
	}, s)
}

// plain is a JSON string's text or a JSON number's digits.
func plain(v json.RawMessage) string {
	var s string
	if json.Unmarshal(v, &s) == nil {
		return s
	}
	return strings.TrimSpace(string(v))
}

func str(v json.RawMessage) string {
	var s string
	_ = json.Unmarshal(v, &s)
	return s
}

func isBlank(v json.RawMessage) bool {
	t := strings.TrimSpace(string(v))
	return t == "" || t == "null" || t == `""`
}

func oneOf(s string, xs ...string) bool {
	for _, x := range xs {
		if s == x {
			return true
		}
	}
	return false
}
