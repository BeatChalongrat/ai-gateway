package runner

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM11 — the runner in English. Everything the runner writes for a run is
// written under the run's context, which says the run's language (runner.go
// execute). These tests build what the runner writes under a Thai context and
// under an English one: the Thai is, word for word, what it was before there
// were two languages; the English has no Thai letter that is not the
// person's own data, and says dates, money and counts the English way.

func hasThai(s string) bool {
	for _, r := range s {
		if r >= 0x0E00 && r <= 0x0E7F {
			return true
		}
	}
	return false
}

// written is v as the store writes it under a context of l (am.MarshalFor).
func written(t *testing.T, l am.Lang, v any) string {
	t.Helper()
	b, err := am.MarshalFor(am.WithLang(context.Background(), l), v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestTheProgressCardIsWrittenInTheRunsLanguage(t *testing.T) {
	started := time.Date(2026, 9, 28, 3, 0, 0, 0, time.UTC)
	waited := int64(5 * 60 * 1000)
	run := am.Run{ID: uuid.New(), Kind: am.KindRun, Status: am.RunRunning, StartedAt: &started}
	steps := []am.Step{
		{Name: "parse:GL.xlsx", Status: "succeeded"},
		{Name: "preflight", Status: "succeeded", Output: json.RawMessage(`{"stats":{"rows":212,"debit_total":"10.00","credit_total":"10.00"}}`)},
		{Name: "clarify:preflight#1", Status: "succeeded", DurationMS: &waited},
		{Name: "compute", Status: "running"},
	}
	p := BuildProgress(run, steps, started.Add(time.Minute))
	var th, en struct {
		Steps []struct{ Key, Label, Detail string } `json:"steps"`
	}
	if err := json.Unmarshal([]byte(written(t, am.Thai, p)), &th); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(written(t, am.English, p)), &en); err != nil {
		t.Fatal(err)
	}
	wantTh := [][2]string{{"อ่านไฟล์ 1 ไฟล์", "GL.xlsx"}, {"ตรวจความครบถ้วน", "212 แถว · เดบิตเครดิตสมดุล"}, {"ถามคุณก่อนคำนวณ", "ได้คำตอบแล้ว · รอ 5 นาที"},
		{"คำนวณตัวเลขด้วยสคริปต์ (ไม่ใช้โมเดล)", ""}, {"ตรวจกฎหารายการผิดปกติ", ""}, {"เขียนคำอธิบาย", ""}, {"สร้างไฟล์ผลลัพธ์", ""}}
	wantEn := [][2]string{{"Reading the files (1)", "GL.xlsx"}, {"Checking completeness", "212 rows · debits equal credits"},
		{"Asking you before calculating", "Answered · waited 5 min"}, {"Calculating the figures with scripts (no model)", ""},
		{"Checking the rules for unusual items", ""}, {"Writing the commentary", ""}, {"Making the files", ""}}
	if len(th.Steps) != len(wantTh) || len(en.Steps) != len(wantEn) {
		t.Fatalf("steps: %d Thai, %d English", len(th.Steps), len(en.Steps))
	}
	for i := range wantTh {
		if got := [2]string{th.Steps[i].Label, th.Steps[i].Detail}; got != wantTh[i] {
			t.Errorf("Thai step %d changed: %q, want %q", i, got, wantTh[i])
		}
		if got := [2]string{en.Steps[i].Label, en.Steps[i].Detail}; got != wantEn[i] {
			t.Errorf("English step %d: %q, want %q", i, got, wantEn[i])
		}
	}
	// a plain marshal — a place nobody told the language — is Thai, as before
	if plain, _ := json.Marshal(p); string(plain) != written(t, am.Thai, p) {
		t.Fatal("a text written with no language named is not the Thai one")
	}
	// the units of a wait, each as its language says it
	for ms, want := range map[int64][2]string{30 * 1000: {"30 วินาที", "30 sec"}, 3 * 3600 * 1000: {"3 ชั่วโมง", "3 hr"}, 72 * 3600 * 1000: {"3 วัน", "3 days"}} {
		if got := [2]string{humanMS(ms).In(am.Thai), humanMS(ms).In(am.English)}; got != want {
			t.Errorf("%d ms: %q, want %q", ms, got, want)
		}
	}
}

func TestAFailureAndACompletionAreWrittenInTheRunsLanguage(t *testing.T) {
	// a failure: the reason's own sentences and the files' fate (AM11b: nothing of the charge)
	facts := am.FailureFacts{Kind: am.KindRun, HasFiles: true, KeepDays: 7}
	f := am.FailureFor("compute_failed", facts)
	const thai = "คำนวณจากไฟล์ที่ส่งมาไม่สำเร็จ — กด “ลองใหม่ด้วยไฟล์เดิม” ได้เลย ถ้ายังไม่สำเร็จอีกให้กด “แจ้งปัญหา” ผู้ดูแลจะเห็นรอบนี้พร้อมขั้นตอนที่หยุด " +
		"ไฟล์ที่ส่งมาในรอบนี้ยังอยู่ครบและเก็บไว้ให้อีก 7 วัน ผลงานของรอบก่อน ๆ ไม่ถูกแตะต้อง"
	item := am.AgentText{Text: f.Text(), Tone: "error", Failure: &f}
	var th, en struct {
		Text    string
		Failure struct{ What, Next, Files string }
	}
	_ = json.Unmarshal([]byte(written(t, am.Thai, item)), &th)
	_ = json.Unmarshal([]byte(written(t, am.English, item)), &en)
	if th.Text != thai || th.Failure.What != "คำนวณจากไฟล์ที่ส่งมาไม่สำเร็จ" {
		t.Fatalf("a Thai run's failure changed:\n%s", th.Text)
	}
	if hasThai(en.Text) || !strings.HasPrefix(en.Text, "The calculation from the files you sent could not be completed — Press “Try again with the same files”.") ||
		!strings.Contains(en.Text, "“Try again with the same files”") || !strings.Contains(en.Text, "7 days") ||
		en.Failure.What == "" || en.Failure.Next == "" || en.Failure.Files == "" || strings.Contains(strings.ToLower(en.Text), "credit") || strings.Contains(th.Text, "เครดิต") {
		t.Fatalf("an English run's failure:\n%s\n%+v", en.Text, en.Failure)
	}
	// the checks that did not pass are said in the run's language, the file's kind and all
	for l, want := range map[am.Lang]string{
		am.Thai:    "สร้างไฟล์ได้ แต่ไม่ผ่านด่านตรวจคุณภาพ จึงยังไม่ส่งให้ (ข้อที่ไม่ผ่าน: รายงาน: ทุกหน้าแสดงผลได้ (3 หน้า แสดงผลได้ 2 หน้า))",
		am.English: "The files were produced but did not pass the quality checks, so they have not been delivered (checks that did not pass: Word report: Every page can be displayed (Pages: 3, displayed: 2))",
	} {
		pages := am.Text("%s หน้า แสดงผลได้ %s หน้า", am.Count(3), am.Count(2))
		line := checkLine(l, "docx", am.Text("ทุกหน้าแสดงผลได้").In(l), pages.In(l))
		q := am.FailureFor("quality_gate", am.FailureFacts{Kind: am.KindRun, HasFiles: true, FailedChecks: []string{line}, Lang: l})
		if got := q.What.In(l); got != want {
			t.Errorf("%s: the failed check reads\n %s\nwant\n %s", l, got, want)
		}
	}
	// a run that finished with its gate open
	c := am.CompletionFor(am.GateNotTied, am.CompletionFacts{KeepDays: 7})
	if got := c.Text().In(am.Thai); !strings.HasPrefix(got, "เสร็จแล้ว · ยังไม่ลงตัว — งบกระทบยอดยังไม่ลงตัว ยอดหลังปรับปรุงของธนาคารและของบัญชียังไม่เท่ากัน ") ||
		strings.Contains(got, "เครดิต") {
		t.Fatalf("a Thai run's completion changed: %s", got)
	}
	if got := c.Text().In(am.English); hasThai(got) || !strings.HasPrefix(got, "Finished · not reconciled — ") || strings.Contains(got, "credit") ||
		!strings.Contains(got, "Draft") {
		t.Fatalf("an English run's completion: %s", got)
	}
	// a date and a period, each as its language says it
	at := time.Date(2026, 9, 28, 3, 5, 0, 0, time.UTC) // 10:05 in Bangkok
	when := am.Text("%s · %s · %s", am.When(at), am.Day(at), am.Period("2026-09"))
	if th, en := when.In(am.Thai), when.In(am.English); th != "28 ก.ย. 2569 เวลา 10:05 น. · 28 ก.ย. 2569 · กันยายน 2569" ||
		en != "28 Sep 2026, 10:05 · 28 Sep 2026 · September 2026" {
		t.Fatalf("dates: %q / %q", th, en)
	}
}

const englishComputed = `{"citations":[
 {"id":"c1","label":"Revenue this period","value":"12480300.00","unit":"thb","formula":"the footing of nominals 4xxx",
  "sources":[{"file":"gl.xlsx","sheet":"GL","rows":1200,"cells":"A2:F1201"},{"file":"inv-001.pdf","row":12,"value":"1000.00","note":"credit note"}]},
 {"id":"c2","label":"Net profit margin","value":"22.0","unit":"pct","derived_from":["c1"]},
 {"id":"c3","label":"Documents","value":"12","unit":"count"}]}`

func TestAnAnswerFromTheRecordSaysMoneyAsItsLanguageDoes(t *testing.T) {
	// asked in English of an English run: THB before the amount, the record's headings in English
	ans, theirs := explainAnswer(am.English, "How is revenue calculated?", []byte(englishComputed))
	want := "**Revenue this period = THB 12,480,300.00**\n\nHow it is calculated: the footing of nominals 4xxx\n\nSource:\n" +
		"- File “gl.xlsx”, sheet GL, 1200 rows (A2:F1201)\n- “inv-001.pdf”, row 12: THB 1,000.00 — credit note\n\n" +
		"This answer is built from this run's record of the source of figures, not by a model — see every row on the “Source of figures” page."
	if ans != want || hasThai(ans) || len(theirs) == 0 {
		t.Fatalf("the English answer:\n%s\nwant\n%s", ans, want)
	}
	// what a person is shown of it in an English item is the whole of it — once the words of the package's
	// own English (here, a formula's) are told to the reader, as a package's catalogue tells them
	// (am.AddEnglishWords): until then a word no sentence of the product uses is withheld, in English too
	if shown := am.FromPackage(ans, am.NoText, theirs...).In(am.English); !strings.Contains(shown, "How it is calculated: the … of …") {
		t.Fatalf("a package's words nobody told the reader were shown: %s", shown)
	}
	am.AddEnglishWords("the footing of nominals")
	if shown := am.FromPackage(ans, am.NoText, theirs...).In(am.English); shown != ans {
		t.Fatalf("an English item shows:\n%s", shown)
	}
	// the same record, for a Thai run: exactly the lines of before AM11
	th, _ := explainAnswer(am.Thai, "ทำไม Revenue this period ถึงเป็นแบบนี้", []byte(englishComputed))
	wantTh := "**Revenue this period = 12,480,300.00 บาท**\n\nวิธีคิด: the footing of nominals 4xxx\n\nมาจาก:\n" +
		"- ไฟล์ “gl.xlsx” ชีต GL 1200 แถว (A2:F1201)\n- “inv-001.pdf” แถว 12: 1,000.00 บาท — credit note\n\n" +
		"คำตอบนี้สร้างจากบันทึกที่มาของตัวเลขในรอบนี้ ไม่ได้มาจากโมเดล — ดูทุกแถวได้ในหน้า “ที่มาของตัวเลข”"
	if th != wantTh {
		t.Fatalf("the Thai answer changed:\n%s\nwant\n%s", th, wantTh)
	}
	// a percentage is the same in both; a count says what it counts; a why nobody recorded is not guessed
	if en, _ := explainAnswer(am.English, "what is the net profit margin made up of?", []byte(englishComputed)); !strings.Contains(en, "= 22.00%**") ||
		!strings.Contains(en, "Made up of:\n- Revenue this period = THB 12,480,300.00") {
		t.Fatalf("a ratio: %s", en)
	}
	c := citation{Value: ptr("12"), Unit: "count"}
	if c.valueText(am.Thai) != "12 ฉบับ" || c.valueText(am.English) != "12 documents" || (citation{}).valueText(am.English) != "no value" {
		t.Fatalf("a count: %q / %q", c.valueText(am.Thai), c.valueText(am.English))
	}
	if en, _ := explainAnswer(am.English, "why is the weather like this", []byte(`{"citations":[]}`)); en != am.T(am.English, dontKnow) || hasThai(en) {
		t.Fatalf("a why with no record: %s", en)
	}
	// "this period" at the end of a label is cut off in either language, so the figure is found without it
	if keys := (citation{Label: "Revenue This Period"}).labelKeys(); keys[1] != "revenue" {
		t.Fatalf("label keys: %v", keys)
	}
	if keys := (citation{Label: "รายได้ งวดนี้"}).labelKeys(); keys[1] != "รายได้" {
		t.Fatalf("label keys: %v", keys)
	}
}

func ptr(s string) *string { return &s }

// The number guard holds an English commentary to exactly the rule of a Thai one: every figure is one
// of the computed table's, with its unit — however the English writes the unit.
func TestTheNumberGuardReadsAnEnglishCommentary(t *testing.T) {
	q := map[string]string{"revenue": "12,480,300.00", "revenue_m": "THB 12.48 M", "margin": "22.0%", "margin_change": "-0.95 pp", "growth": "+8.2%"}
	ids := []string{"510000", "5300-01"}
	for _, ok := range []string{
		"Revenue was THB 12,480,300.00 (THB 12.48 M), up 8.2% on the prior period.",
		"Revenue reached 12.48 million and the net profit margin was 22.0%, down 0.95 pp.",
		"The margin fell by 0.95 percentage points to 22.0%; revenue was 12.48M across 3 branches in 2026.",
		"Account 510000 and code 5300-01 moved most; a/c 510000 is new.",
	} {
		if err := GuardNumbersIn(am.English, ok, q, ids); err != nil {
			t.Errorf("a commentary that quotes the table was refused: %q: %v", ok, err)
		}
	}
	for _, bad := range []string{
		"Revenue was THB 13.10 M.",              // an invented figure
		"Revenue was THB 12,480,301.00.",        // a digit off
		"Revenue was 22.0 million.",             // a figure of the table with a unit it does not have
		"The margin was 21.5%, down 0.95 pp.",   // an invented percentage
		"Other income of 510000 was recorded.",  // an account code passed off as an amount
		"Revenue grew by 124 M over the period", // a number nobody computed
	} {
		if err := GuardNumbersIn(am.English, bad, q, ids); err == nil {
			t.Errorf("an invented figure passed the guard: %q", bad)
		}
	}
	// the Thai guard is what it was: its own unit, and an English unit is no part of a Thai figure
	th := map[string]string{"revenue_m": "12.48 ล้าน", "margin_change": "-0.95 จุด", "margin": "22.0%"}
	if err := GuardNumbers("รายได้ 12.48 ล้าน อัตรากำไร 22.0% ลดลง 0.95 จุด", th, nil); err != nil {
		t.Fatalf("the Thai guard changed: %v", err)
	}
	if err := GuardNumbers("รายได้ 13.10 ล้าน", th, nil); err == nil {
		t.Fatal("the Thai guard let an invented figure through")
	}
	// an English commentary on a table whose figures are still printed with Thai units is read against
	// the same figures: "12.48 M" is the table's "12.48 ล้าน", and nothing else is
	if err := GuardNumbersIn(am.English, "Revenue was THB 12.48 M, the margin down 0.95 pp to 22.0%.", th, nil); err != nil {
		t.Fatalf("an English commentary on Thai-printed figures: %v", err)
	}
	if err := GuardNumbersIn(am.English, "Revenue was THB 12.84 M.", th, nil); err == nil || !strings.Contains(err.Error(), "ตัวเลขที่ไม่มีในตารางที่คำนวณ") {
		t.Fatalf("an invented figure, and the reason sent back to the model: %v", err)
	}
	// a model's answer may give no cause, in either language
	for _, s := range []string{"Revenue fell because of returns.", "Lower revenue, driven by fewer orders.", "This was due to a one-off.", "รายได้ลดลงเพราะคืนสินค้า"} {
		if !givesACause(s) {
			t.Errorf("a cause was let through: %q", s)
		}
	}
	for _, s := range []string{"Revenue was THB 12.48 M.", "The reasonable figure is 22.0%.", "รายได้ 12,480,300 บาท"} {
		if givesACause(s) {
			t.Errorf("no cause is given, and one was read: %q", s)
		}
	}
}

// A model is told to write in the run's language; a Thai run's prompt is exactly what it was.
func TestAPromptNamesTheRunsLanguage(t *testing.T) {
	cv := computedView{PeriodLabel: "September 2026", Quotable: map[string]string{"revenue": "12.48 M"}}
	th, en := narrativePrompt(am.Thai, cv), narrativePrompt(am.English, cv)
	if !strings.HasSuffix(th, "\nเขียนคำอธิบายตามรูปแบบ JSON ใน instructions ภาษาไทย ใช้ตัวเลขจากรายการด้านบนเท่านั้น") {
		t.Fatalf("a Thai run's prompt changed:\n%s", th)
	}
	if !strings.Contains(en, "ใน instructions ภาษาอังกฤษ ใช้ตัวเลขจากรายการด้านบนเท่านั้น") || !strings.Contains(en, "Write the whole answer in English") ||
		strings.Contains(en, "ภาษาไทย ใช้ตัวเลข") {
		t.Fatalf("an English run's prompt does not ask for English:\n%s", en)
	}
	cv.Figures = map[string]figureView{"revenue": {Text: "12.48 M", Cite: "c1", Label: "Revenue"}}
	th, en = narrativePrompt(am.Thai, cv), narrativePrompt(am.English, cv)
	if !strings.HasSuffix(th, "\nเขียนคำอธิบายตามรูปแบบ JSON ใน instructions ภาษาไทย ทุกตัวเลขเขียนเป็น {ชื่อ} จากรายการด้านบน "+
		"(ถ้าบอกทิศทางเป็นคำแล้ว เช่น ลดลง ใช้ {ชื่อ:abs} เพื่อพิมพ์โดยไม่มีเครื่องหมาย)") {
		t.Fatalf("a Thai run's prompt (figures by key) changed:\n%s", th)
	}
	if !strings.Contains(en, "ใน instructions ภาษาอังกฤษ ทุกตัวเลข") || !strings.Contains(en, "เช่น decreased") || !strings.Contains(en, "Write the whole answer in English") {
		t.Fatalf("an English run's prompt (figures by key):\n%s", en)
	}
	if answerIn(am.Thai) != "" || languageName(am.Thai) != "ภาษาไทย" || languageName(am.English) != "ภาษาอังกฤษ" {
		t.Fatal("what a prompt says of languages")
	}
}

// The PDF's text check goes by the report's language. A Thai report: pdf.thai, exactly as before. An
// English report: pdf.text — every paragraph of the report, in the report's order.
func TestThePdfGateOfAnEnglishReport(t *testing.T) {
	para := func(s string) string { return `<w:p><w:r><w:t>` + s + `</w:t></w:r></w:p>` }
	docx := testDocx(t, para("Income statement for September 2026")+para("12,480,300.00")+
		para("Revenue rose in the office segment.")+para("บริษัท ตัวอย่าง จำกัด")+para("Net profit is final."))
	good := CheckResult{Pages: 1, RenderedPages: 1, PageInk: []float64{0.03},
		RawText: "Income statement for September 2026\n12,480,300.00\nRevenue rose in the ofﬁce\nsegment.\nบริษัท ตัวอย่าง จํากัด\nNet proﬁt is ﬁnal.\n"}
	checks := pdfGate(am.English, docx, good)
	if len(checks) != 2 || checks[1].Name != "pdf.text" || !checks[0].OK || !checks[1].OK {
		t.Fatalf("an English report whose PDF holds its text: %+v", checks)
	}
	if got := checks[1].Label.In(am.English) + " · " + checks[1].Detail.In(am.English); got != "The text in the PDF is the same as in the report · Paragraphs that are the same: 4 of 4" {
		t.Fatalf("the check reads: %s", got)
	}
	// a paragraph that is not in the PDF
	missing := good
	missing.RawText = strings.Replace(good.RawText, "Net proﬁt is ﬁnal.", "", 1)
	c := pdfGate(am.English, docx, missing)[1]
	if c.OK || c.Detail.In(am.English) != "Paragraphs that are the same: 3 of 4 · not the same, for example “Net profit is final.”" {
		t.Fatalf("a missing paragraph: %+v / %s", c, c.Detail.In(am.English))
	}
	// the same paragraphs in another order are not the report
	moved := good
	moved.RawText = "Net profit is final.\nIncome statement for September 2026\nRevenue rose in the office segment.\nบริษัท ตัวอย่าง จำกัด\n"
	if c := pdfGate(am.English, docx, moved)[1]; c.OK {
		t.Fatalf("paragraphs out of the report's order passed: %s", c.Detail.In(am.English))
	}
	// a report with no text at all
	empty := testDocx(t, para("12,480,300.00"))
	if c := pdfGate(am.English, empty, good)[1]; c.OK || c.Detail.In(am.English) != "The report has no text" {
		t.Fatalf("a report with no text: %+v", c)
	}
	// the Thai rule is as it was: the same English report, as a Thai run's, has one Thai paragraph to find …
	th := pdfGate(am.Thai, docx, good)
	if len(th) != 2 || th[1].Name != "pdf.thai" || !th[1].OK || th[1].Detail.String() != "ตรง 1 จาก 1 ย่อหน้า" || th[1].Label.String() != "ภาษาไทยใน PDF ตรงกับรายงาน" {
		t.Fatalf("the Thai rule changed: %+v", th)
	}
	// … and a report with no Thai in it does not pass a Thai run's gate
	if c := pdfGate(am.Thai, testDocx(t, para("Net profit is final.")), good)[1]; c.OK || c.Detail.String() != "ไม่พบข้อความภาษาไทยในรายงาน" {
		t.Fatalf("the Thai rule changed: %+v", c)
	}
	// a check kept on an artifact is kept in the run's language
	q := withCheck(am.English, json.RawMessage(`{"passed":true,"checks":[]}`), checks[1])
	if hasThai(string(q)) || !strings.Contains(string(q), "The text in the PDF is the same as in the report") {
		t.Fatalf("a check kept for an English run: %s", q)
	}
	if q := withCheck(am.Thai, json.RawMessage(`{"passed":true,"checks":[]}`), th[1]); !strings.Contains(string(q), "ภาษาไทยใน PDF ตรงกับรายงาน") {
		t.Fatalf("a check kept for a Thai run: %s", q)
	}
}

// What a person types to the agent in English is read as the same thing typed in Thai. An English word
// is found only as a whole word (am.HasWord): "excellent" does not ask for an Excel file, nor "in other
// words" or "password" for a Word one.
func TestWhatAPersonTypesInEnglishIsReadAsInThai(t *testing.T) {
	asks := func(format string) func(string) bool {
		return func(text string) bool {
			for _, f := range detectFormats(text) {
				if f == format {
					return true
				}
			}
			return false
		}
	}
	for _, c := range []struct {
		list  string
		reads func(string) bool
		yes   []string // at least one Thai phrase and two English ones
		no    []string
	}{
		{"formatWords (xlsx)", asks("xlsx"), []string{"ขอเอ็กเซลด้วย", "ขอexcelหน่อย", "Can I have it in Excel", "a spreadsheet please", "report.XLSX"},
			[]string{"excellent work", "That excels"}},
		{"formatWords (docx)", asks("docx"), []string{"ขอเป็นเวิร์ด", "As a Word file please", "the word report", "a .docx"},
			[]string{"in other words, yes", "what is the password", "keywords"}},
		{"formatWords (pptx)", asks("pptx"), []string{"ขอสไลด์", "Make the slides too", "a PowerPoint deck"}, []string{"sliding scale", "decking"}},
		{"pdfWords", asksForPDF, []string{"ขอพีดีเอฟ", "ขอpdfด้วย", "Send it as PDF", "two pdfs"}, []string{"Excel"}},
		{"questionWords", isQuestion, []string{"ทำไมกำไรลดลง", "How much is revenue", "what is the net profit", "Explain the margin", "which accounts moved"},
			[]string{"show me the chart", "whatever", "send the files"}},
		{"moreFilesWords", wantsMoreFiles, []string{"ขอไฟล์เพิ่ม", "I need more files", "in another format please"}, []string{"more file space", "the other day"}},
		// (englishWords left with AM18: the language of the documents is read by the model with every other
		// request, and is no longer a list of words)
		{"whyWords", asksWhy, []string{"ทำไมยอดเป็นศูนย์", "Why is it zero", "what is the reason", "where does it come from", "how was it calculated"},
			[]string{"reasonable", "resource", "becauseway"}},
	} {
		for _, s := range c.yes {
			if !c.reads(s) {
				t.Errorf("%s: %q is not read", c.list, s)
			}
		}
		for _, s := range c.no {
			if c.reads(s) {
				t.Errorf("%s: %q is read, and should not be (an English word is found only whole)", c.list, s)
			}
		}
	}
}

// A card the runner builds is asked in the run's language, and its header fits twelve cells in both.
func TestACardTheRunnerBuildsIsInTheRunsLanguage(t *testing.T) {
	all := []string{"docx", "pptx", "xlsx", "pdf"}
	th, en := formatQuestion(am.Thai, all, true), formatQuestion(am.English, all, true)
	if th.Header != "ชนิดไฟล์" || th.Question != "ต้องการไฟล์แบบไหนเพิ่มครับ (เลือกได้หลายแบบ)" || th.Options[0].Label != "รายงาน .docx" ||
		th.Options[3].Label != "รายงาน PDF" || th.DefaultLabel != "ตารางตัวเลข .xlsx" {
		t.Fatalf("the Thai card changed: %+v", th)
	}
	if en.Header != "File type" || am.DisplayLen(en.Header) > 12 || hasThai(en.Question) || en.Options[0].Label != "Word report (.docx)" ||
		en.Options[1].Label != "PowerPoint slides (.pptx)" || en.Options[3].Label != "PDF report" || en.DefaultLabel != "Excel workbook (.xlsx)" {
		t.Fatalf("the English card: %+v", en)
	}
	for i := range th.Options {
		if string(th.Options[i].Value) != string(en.Options[i].Value) {
			t.Fatal("an option means the same in both languages")
		}
	}
	if got := am.T(am.English, "ขอไฟล์ใหม่"); got != "New file" || am.DisplayLen(got) > 12 {
		t.Fatalf("the replacement-file card's header: %q", got)
	}
	// the names of the files a reply promises
	if got := formatNames([]string{"docx", "pdf"}); got.In(am.Thai) != "รายงาน .docx และ รายงาน PDF" || got.In(am.English) != "Word report (.docx) and PDF report" {
		t.Fatalf("format names: %q / %q", got.In(am.Thai), got.In(am.English))
	}
	// a self-test's working paper says who prepared it — nobody — in its run's language
	if am.T(am.English, SamplePreparer) != "Agent's sample file (not prepared by anyone)" || am.T(am.Thai, SamplePreparer) != SamplePreparer {
		t.Fatalf("the sample's preparer: %q", am.T(am.English, SamplePreparer))
	}
}

// What goes into a document follows the language the package writes in, not the run's: a run from an
// English screen of a package that speaks only Thai gets a Thai report, Thai commentary inside it, and
// the Thai check of its PDF — never a document half one language and half another.
func TestADocumentIsInThePackagesLanguageNotTheRuns(t *testing.T) {
	thaiOnly := &runCtx{pkg: &Package{Manifest: am.Manifest{}}}
	both := &runCtx{pkg: &Package{Manifest: am.Manifest{Languages: []string{"th", "en"}}}}
	english := am.WithLang(context.Background(), am.English)
	thai := am.WithLang(context.Background(), am.Thai)
	for _, c := range []struct {
		what string
		rc   *runCtx
		ctx  context.Context
		want am.Lang
	}{
		{"a Thai-only package, a run in English", thaiOnly, english, am.Thai},
		{"a Thai-only package, a run in Thai", thaiOnly, thai, am.Thai},
		{"a package that speaks both, a run in English", both, english, am.English},
		{"a package that speaks both, a run in Thai", both, thai, am.Thai},
		{"no language on the context at all", both, context.Background(), am.Thai},
	} {
		if got := c.rc.docLang(c.ctx); got != c.want {
			t.Errorf("%s: documents in %q, want %q", c.what, got, c.want)
		}
	}
	if (am.Manifest{}).Speaks(am.English) || !(am.Manifest{}).Speaks(am.Thai) {
		t.Error("a package that names no language speaks Thai, and only Thai")
	}
	if !(am.Manifest{Languages: []string{" EN "}}).Speaks(am.English) || (am.Manifest{Languages: []string{"en"}}).Speaks(am.Thai) {
		t.Error("a package speaks exactly the languages it names")
	}
	// the gate of a Thai report is the Thai check whatever screen started the run
	para := func(s string) string { return `<w:p><w:r><w:t>` + s + `</w:t></w:r></w:p>` }
	docx := testDocx(t, para("รายงานผลประกอบการ กันยายน 2569")+para("GL_2569-09.xlsx")+para("รายได้เพิ่มขึ้นจากงวดก่อน"))
	chk := CheckResult{Pages: 1, RenderedPages: 1, PageInk: []float64{0.03}, RawText: "รายได้เพิ่มขึ้นจากงวดก่อน\nGL_2569-09.xlsx\nรายงานผลประกอบการ กันยายน 2569\n"}
	got := pdfGate(thaiOnly.docLang(english), docx, chk)
	if len(got) != 2 || got[1].Name != "pdf.thai" || !got[1].OK {
		t.Fatalf("a Thai report in a run from an English screen: %+v", got)
	}
}
