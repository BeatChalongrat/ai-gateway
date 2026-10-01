package runner

import (
	"encoding/json"
	"strings"
	"testing"
)

// AM9 (A): the print proof finds a row's identity and its amounts on one
// rendered page, or names the row.

func TestAPrintedRowIsFoundOnlyWhenItsDescriptionAndAmountsShareAPage(t *testing.T) {
	rows := []printRow{
		{Sheet: "รายการที่จับคู่", Row: 5, Identity: []string{"1", "จ่ายค่าเช่าสำนักงาน"}, Amounts: []string{"(45,000.00)"}},
		{Sheet: "รายการที่จับคู่", Row: 6, Identity: []string{"2", "จ่ายค่าวัสดุสำนักงาน ร้านเครื่องเขียนสมใจ"}, Amounts: []string{"(8,560.00)"}},
	}
	// AM8's layout: the descriptions on page 5, the amounts on page 6
	am8 := CheckResult{Pages: 2, RawText: "คู่ที่ 1 จ่ายค่าเช่าสำนักงาน\n2 จ่ายค่าวัสดุสำนักงาน ร้านเครื่องเขียนสมใจ\f" +
		"คู่ที่\n1\n2\n(45,000.00)\n(8,560.00)\n"}
	c := checkPrinted(am8, rows)
	if c.OK || !strings.Contains(c.Detail.String(), "แถว 5") || !strings.Contains(c.Detail.String(), "แถว 6") {
		t.Fatalf("a description and its amount on different pages must fail, naming the rows: %+v", c)
	}
	// AM9's: the identity repeats and the amount is beside it; pdftotext splits Thai and wraps a long
	// description over two lines — the comparison is in thaiKey form, and a long text is looked for by
	// its first words
	am9 := CheckResult{Pages: 2, RawText: "1 จ่ายค่าเช่าสำนักงาน (45,000.00)\n2 จ่ายค่าวัสดุ สำนักงาน ร้านเครื่อง\nเขียน (8,560.00)\f" +
		"1 จ่ายค่าเช่าสำนักงาน หลักฐาน GL แถว 8\n"}
	c = checkPrinted(am9, rows)
	if !c.OK {
		t.Fatalf("identity beside amounts: %+v", c)
	}
	if !strings.Contains(c.Detail.String(), "2 หน้า") {
		t.Fatalf("the detail says how many pages were printed: %s", c.Detail)
	}
}

func TestTheProofFailsAFileItAddsTo(t *testing.T) {
	q := withCheck(json.RawMessage(`{"passed":true,"checks":[{"name":"xlsx.sheets","ok":true}]}`),
		gateCheck{Name: "xlsx.printed", OK: false})
	var got struct {
		Passed bool              `json:"passed"`
		Checks []json.RawMessage `json:"checks"`
	}
	if err := json.Unmarshal(q, &got); err != nil || got.Passed || len(got.Checks) != 2 {
		t.Fatalf("a failed print proof fails the file and is listed: %s", q)
	}
}
