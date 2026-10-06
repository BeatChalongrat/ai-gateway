package agentmarket

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM6 — a rule that refuses the product's own sentences is wrong. Everything
// the four packages hand to a thread (am6_packages_test.go: what their eval
// sets produce), every message of the platform and every sentence written in
// the source goes through the rule here and must come out as it went in.

type packageDoc struct {
	Findings []struct {
		Package, Case string
		Finding       json.RawMessage
	}
	Summaries []struct {
		Package, Case string
		Summary       map[string]any
		Gate          map[string]any
	}
	Checks []struct {
		Package, Case, File string
		Check               map[string]any
	}
	Assumptions []struct {
		Package, Case string
		Assumption    struct{ Text string }
	}
}

func packageOutputsDoc(t *testing.T) packageDoc {
	t.Helper()
	var doc packageDoc
	if err := json.Unmarshal([]byte(packageOutputs), &doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

// textsOf lists the texts a person reads in a sent payload, by path.
func textsOf(path string, v any, f field, out map[string]string) {
	if _, isList := v.([]any); isList && f.alt != nil {
		f = *f.alt // a list where another package writes counts (AM6: tiers)
	}
	switch x := v.(type) {
	case string:
		if f.class == asText {
			out[path] = x
		}
	case []any:
		if f.class == asList {
			for i, e := range x {
				textsOf(fmt.Sprintf("%s[%d]", path, i), e, *f.each, out)
			}
		}
	case map[string]any:
		if f.class == asObject {
			for k, e := range x {
				if sub, ok := f.fields[k]; ok {
					textsOf(path+"."+k, e, sub, out)
				}
			}
		}
	}
}

func sentTexts(t *testing.T, kind string, payload any) (before, after map[string]string, sent map[string]any) {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	var in map[string]any
	_ = json.Unmarshal(raw, &in)
	_ = json.Unmarshal(ForClient(kind, raw), &sent)
	before, after = map[string]string{}, map[string]string{}
	textsOf(kind, in, itemSchema[kind], before)
	textsOf(kind, sent, itemSchema[kind], after)
	return before, after, sent
}

func TestEverySentenceOfThePackagesReadsAsWrittenForAPerson(t *testing.T) {
	doc := packageOutputsDoc(t)
	texts, refused, engine := 0, 0, 0
	thai := regexp.MustCompile(`[ก-๛]`)
	same := func(where string, before, after map[string]string) {
		for path, s := range before {
			if strings.TrimSpace(s) == "" {
				continue
			}
			if strings.Contains(path, ".evidence.") {
				// what a question shows beside its sentence: the person's own data is sent as it is
				// (checked below); a sentence of the package's is sent; the engine's own English
				// ("13 digits with a valid check digit") is not a sentence for a person, and is not sent
				if after[path] != s {
					engine++
					if thai.MatchString(s) || after[path] != "" {
						t.Errorf("%s %s\n   written: %q\n   sent:    %q", where, path, s, after[path])
					}
				}
				continue
			}
			texts++
			if after[path] != s {
				refused++
				t.Errorf("%s %s\n   written: %q\n   sent:    %q", where, path, s, after[path])
			}
		}
	}
	// every finding, as the card the runner makes of it
	for _, f := range doc.Findings {
		var fd Finding
		if err := json.Unmarshal(f.Finding, &fd); err != nil {
			t.Fatalf("%s/%s: %v", f.Package, f.Case, err)
		}
		if fd.Moment == "" {
			fd.Moment = "preflight"
		}
		if err := fd.Validate(); err != nil {
			t.Fatalf("%s/%s: %v", f.Package, f.Case, err)
		}
		qs, _ := json.Marshal([]Question{questionOf(fd)})
		card := CardItemPayload(Card{ID: uuid.New(), RunID: uuid.New(), Kind: "questions", Moment: fd.Moment, Status: "open", Questions: qs},
			NoText, NoText)
		before, after, sent := sentTexts(t, "card", card)
		same(f.Package+"/"+f.Case, before, after)
		// the evidence: the person's own data is sent; what only the engine reads is not
		q := sent["questions"].([]any)[0].(map[string]any)
		ev, _ := q["evidence"].(map[string]any)
		var raw struct {
			Evidence map[string]any
		}
		_ = json.Unmarshal(f.Finding, &raw)
		for _, k := range []string{"rows", "sample_rows", "columns", "values", "headers", "file", "document", "months", "claim", "sheet_name"} {
			if w, ok := raw.Evidence[k]; ok && w != nil && !reflect.DeepEqual(jsonOf(ev[k]), jsonOf(w)) {
				t.Errorf("%s/%s: the person's own data (evidence.%s) is sent as it is: %s, sent %s", f.Package, f.Case, k, jsonOf(w), jsonOf(ev[k]))
			}
		}
		for _, k := range []string{"question", "error", "check", "answered_by"} {
			if _, ok := ev[k]; ok {
				t.Errorf("%s/%s: evidence.%s is the engine's, not a person's: %v", f.Package, f.Case, k, ev[k])
			}
		}
		if fact, ok := ev["fact"].(map[string]any); ok {
			for k := range fact {
				if k != "detail" {
					t.Errorf("%s/%s: evidence.fact.%s is the engine's: %v", f.Package, f.Case, k, fact[k])
				}
			}
		}
	}
	// every compute summary and every gate, as the files card carries them
	for _, s := range doc.Summaries {
		payload := map[string]any{"run_id": uuid.NewString(), "summary": s.Summary}
		if s.Gate != nil {
			payload = map[string]any{"run_id": uuid.NewString(), "gate": s.Gate, "summary": map[string]any{"gate": s.Gate}}
		}
		before, after, _ := sentTexts(t, "outputs", payload)
		same(s.Package+"/"+s.Case, before, after)
	}
	// every quality check of a rendered file
	for _, c := range doc.Checks {
		name := c.File
		if name == "" {
			name = "report.xlsx"
		}
		payload := map[string]any{"run_id": uuid.NewString(), "artifacts": []any{map[string]any{"id": uuid.NewString(), "name": name,
			"format": "xlsx", "size": 1, "version": 1, "quality": map[string]any{"passed": true, "checks": []any{c.Check}}}}}
		before, after, _ := sentTexts(t, "outputs", payload)
		same(c.Package+"/"+c.Case, before, after)
	}
	// every assumption (the run page and the files print them)
	for _, a := range doc.Assumptions {
		texts++
		if !ReadsForPerson(a.Assumption.Text) {
			refused++
			t.Errorf("%s/%s assumption: %q\n   shown: %q", a.Package, a.Case, a.Assumption.Text, ShownToPerson(a.Assumption.Text))
		}
	}
	t.Logf("%d sentences of the packages' eval sets, %d refused (%d findings, %d summaries and gates, %d checks, %d assumptions); "+
		"%d values of evidence in the engine's own English were not sent",
		texts, refused, len(doc.Findings), len(doc.Summaries), len(doc.Checks), len(doc.Assumptions), engine)
	if texts < 400 {
		t.Fatalf("the packages' sentences were not read: %d", texts)
	}
}

func jsonOf(v any) string {
	b, _ := json.Marshal(v)
	var x any
	_ = json.Unmarshal(b, &x)
	b, _ = json.Marshal(x)
	return string(b)
}

func TestEveryMessageOfThePlatformReadsAsWrittenForAPerson(t *testing.T) {
	n := 0
	check := func(where, s string) {
		t.Helper()
		n++
		if !ReadsForPerson(s) {
			t.Errorf("%s: %q\n   shown: %q", where, s, ShownToPerson(s))
		}
	}
	// the message table, with facts as the handlers give them
	for code := range userMessages {
		check("message "+code, Message(code, "detail", "รายงาน Word", "code", "BAD_JSON", "what", whatThai["run"],
			"contact", ContactFact(Contact{Name: "คุณสมหญิง", Email: "admin@example.test"}).s, "mb", "20", "types", ".xlsx .csv .pdf",
			"format", "docx", "reason", Aside("ปิดปรับปรุงสิ้นเดือน").s, "email", "colleague@example.test", "domains", "@example.test",
			"why", "รอบนี้ยังทำไม่เสร็จ", "next", "รอให้รอบนี้เสร็จก่อน แล้วค่อยตรวจทาน", "which", "ของคุณ", "days", "7"))
	}
	for k, v := range whatThai {
		check("what "+k, "ไม่พบ"+v+"นี้")
	}
	// every failed round, every round that finished open
	for _, reason := range append(FailureReasons(), "", "load", "some_reason_nobody_named") {
		for _, facts := range []FailureFacts{{Kind: KindRun, HasFiles: true, KeepDays: 7}, {Kind: KindSelfTest}, {Kind: KindRun, Credits: 12.5, HasFiles: true},
			{Kind: KindRun, HasFiles: true, FailedChecks: []string{"รายงาน: หัวข้อครบทุกส่วน", "PDF: แปลงเป็น PDF (ตัวแปลงเอกสารยังไม่พร้อม)"}}} {
			f := FailureFor(reason, facts)
			check("failure "+reason, f.Text().String())
			for _, part := range []PersonText{f.What, f.Next, f.Files, f.Charged} {
				check("failure "+reason, part.String())
			}
		}
	}
	for _, code := range []string{GateNotTied, "gate_not_passed", "stock_not_counted"} {
		for _, facts := range []CompletionFacts{{Credits: 1134.58, KeepDays: 7, GateWhat: "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท",
			GateNext: "ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร"}, {}} {
			c := CompletionFor(code, facts)
			check("completion "+code, c.Text().String())
			for _, part := range []PersonText{c.Title, c.What, c.Next, c.Files, c.Charged} {
				check("completion "+code, part.String())
			}
		}
	}
	// what a round costs, before it starts
	c, thb, s := 1134.58, 39.71, 40.2
	for _, e := range []Estimate{{}, {Credits: &c}, {Credits: &c, THB: &thb, Seconds: &s}} {
		check("estimate", estimateLine(e, true).String())
		check("estimate", estimateLine(e, false).String())
	}
	// notices
	limit := 1000.0
	for _, st := range []BudgetState{{UserLimit: 1000, UserSpent: 1200, Exhausted: BudgetUser, Contact: Contact{Name: "ฝ่ายไอที", Email: "it@example.test"}},
		{Department: "บัญชี", DeptLimit: &limit, DeptSpent: 1000, Exhausted: BudgetDepartment, Contact: Contact{Email: "head@example.test"}},
		{InstallLimit: 500, InstallSpent: 600, Exhausted: BudgetInstall}} {
		check("budget notice", BudgetNotice(st).String())
	}
	check("problem notice", problemNotice(ProblemReport{Ref: "3FA2B1"}).String())
	check("problem notice", problemNotice(ProblemReport{Ref: "FACADE"}).String())
	for _, k := range []string{"company", "formats", "currency", "language", "materiality", "mapping", "dc_convention", "theme", "fiscal_year_start",
		"company_tax_id", "company_branch", "policy", "holidays", "policy_kb", "match_date_window_days", "bank_name"} {
		check("setting "+k, settingLabel(Thai, k))
	}
	for _, s := range []string{reasonOutside, reasonNoVision, reasonNotListed, RetentionText(5).String()} {
		check("line", s)
	}
	// every sentence written in the source of both planes
	lit := regexp.MustCompile(`"((?:[^"\\]|\\.)*)"`)
	call := regexp.MustCompile(`\b(?:am\.)?(?:Text|or|orNth)\(\s*"`)
	for _, dir := range []string{".", "runner"} {
		files, _ := filepath.Glob(filepath.Join(dir, "*.go"))
		for _, f := range files {
			if strings.HasSuffix(f, "_test.go") {
				continue
			}
			src, err := os.ReadFile(f)
			if err != nil {
				t.Fatal(err)
			}
			text := string(src)
			for _, at := range call.FindAllStringIndex(text, -1) {
				// the literal, and what is joined to it with "+"
				rest, sentence := text[at[1]-1:], ""
				for {
					m := lit.FindStringSubmatchIndex(rest)
					if m == nil || strings.TrimSpace(rest[:m[0]]) != "" {
						break
					}
					part, err := strconvUnquote(rest[m[0]:m[1]])
					if err != nil {
						break
					}
					sentence += part
					rest = rest[m[1]:]
					trimmed := strings.TrimLeft(rest, " \t\r\n")
					if !strings.HasPrefix(trimmed, "+") {
						break
					}
					rest = strings.TrimLeft(trimmed[1:], " \t\r\n")
				}
				if sentence == "" || sentence == "%s" {
					continue
				}
				// a fact in a sentence's place: a Thai word stands for it
				check(f, strings.ReplaceAll(sentence, "%s", "ข้อมูล"))
			}
		}
	}
	t.Logf("%d sentences of the platform", n)
	if n < 300 {
		t.Fatalf("the platform's sentences were not read: %d", n)
	}
}

func strconvUnquote(s string) (string, error) {
	var out string
	err := json.Unmarshal([]byte(s), &out)
	return out, err
}

// What is, and what is not, written for a person.
func TestWhatReadsAsWrittenForAPerson(t *testing.T) {
	for _, s := range []string{
		"อ่านไฟล์ที่ส่งมาไม่สำเร็จ",
		"ยังไม่ลงตัว ต่างกัน 21,435.00 บาท",
		"ไฟล์ “GL กันยายน.xlsx” มีรายการหลายเดือน",
		"ได้รับ GL_2569-09.xlsx ระหว่างที่กำลังทำรอบนี้",
		"เอกสาร PV256909-9901 ซ้ำกับ PV256909-0034 ทุกบรรทัด (วันเดียวกัน ยอด 25,246.94 บาท)",
		"คอลัมน์ “D/C Ind.” มีค่าเป็น S กับ H ผมเดาว่า S = เดบิต และ H = เครดิต",
		"ช่อง B12 ของชีต “สรุป” (Sheet1!A1:C3) เป็นสูตร",
		"ส่งเป็น PDF จากระบบหรือสแกน 200–300 DPI ถ้ามี",
		"ภาษีมูลค่าเพิ่ม (VAT) ไม่เท่ากับ 7% ของฐาน",
		"ยืนยันโดย finance@example.test เมื่อ 2026-10-01 และวันเสาร์–อาทิตย์",
		"ค.ศ. เช่น September 2026",
		"คำอธิบายเขียนด้วย qwen3.8-27b-fp8 ที่รันในองค์กร",
		"รอบหนึ่งใช้ประมาณ 1,130 เครดิต (≈ 0.02 บาท) ราว 40 วินาที",
	} {
		if !ReadsForPerson(s) || ShownToPerson(s) != s {
			t.Errorf("written for a person, and refused: %q → %q", s, ShownToPerson(s))
		}
	}
	// the name a document gives itself, in front of its own file — when the item declares the file
	doc := "Cloudnine Software Pte. Ltd. เลขที่ CN-2026-00917 (f07_foreign_currency_usd.pdf): เอกสารเป็นเงินตราต่างประเทศ"
	if !ReadsForPerson(doc, "f07_foreign_currency_usd.pdf") {
		t.Errorf("a document's own name: %q", ShownToPerson(doc, "f07_foreign_currency_usd.pdf"))
	}
	if ReadsForPerson(doc) || ReadsForPerson(doc, "another.pdf") || strings.Contains(ShownToPerson(doc), "Cloudnine") {
		t.Errorf("a name in front of a file nobody declared is a name nobody declared: %q", ShownToPerson(doc))
	}
	// a word the item declares as the person's data
	if s := "ไม่พบผู้ขาย Acme Trading ในทะเบียน"; ReadsForPerson(s) || !ReadsForPerson(s, "Acme Trading Co.") {
		t.Errorf("declared data: %q", s)
	}
	for s, shown := range map[string]string{
		// an English sentence, an identifier, a path, a type's name, an address — whatever their shape
		"the file could not be opened":                      "",
		"File is not a zip file":                            "",
		"Internal Server Error":                             "",
		"Bad Gateway":                                       "",
		"e2_check_failed":                                   "",
		"/job/in/am_engine.zip/agent_market/engine/step.py": "",
		"C:\\Users\\runner\\work\\report.xlsx":              "",
		"Workbook":                                          "",
		"0xDEADBEEF":                                        "",
		"172.18.0.4:2375":                                   "",
		"อ่านไฟล์ไม่สำเร็จ: the file could not be opened":                       "อ่านไฟล์ไม่สำเร็จ: …",
		"อ่านไฟล์ไม่สำเร็จ (BadZipFile: File is not a zip file) ลองส่งใหม่":     "อ่านไฟล์ไม่สำเร็จ ลองส่งใหม่",
		"อ่านไฟล์ไม่สำเร็จ Internal Server Error":                               "อ่านไฟล์ไม่สำเร็จ …",
		"เชื่อมต่อไม่ได้ 172.18.0.4:2375 ลองใหม่":                               "เชื่อมต่อไม่ได้ … ลองใหม่",
		"ขั้นตอน compute_failed หยุดที่ step.py":                                "ขั้นตอน … หยุดที่ …",
		"ตรวจไม่ผ่าน <<retry budget>> แล้ว":                                     "ตรวจไม่ผ่าน … แล้ว",
		"ผิดพลาด {\"ok\": false, \"error\": \"x\"} ในรอบนี้":                    "ผิดพลาด … ในรอบนี้",
		"C07 · John Smith เบิกค่าอาหาร 600.00 บาท ที่ Starbucks วันที่ 10 ต.ค.": "C07 · … เบิกค่าอาหาร 600.00 บาท ที่ … วันที่ 10 ต.ค.",
		// no piece of what was refused is shown beside it
		"สร้างไฟล์ไม่สำเร็จ: 0xDEADBEEF @ ModuleLoader":   "สร้างไฟล์ไม่สำเร็จ: …",
		"ซ้ำกับ \"agent_run_steps_pkey\" violates unique": "ซ้ำกับ …",
	} {
		if ReadsForPerson(s) {
			t.Errorf("not written for a person, and let through: %q", s)
		}
		if got := ShownToPerson(s); got != shown {
			t.Errorf("%q\n   shown:    %q\n   expected: %q", s, got, shown)
		}
		// AM11: the rule above is the Thai one — an item written in Thai, read exactly as before. An item
		// written in English is read by the English rule, which shows a word an English sentence of the
		// product uses and is otherwise the same promise: whatever it shows reads as written for a person,
		// and nothing of it is an error's own text, in either language.
		en := ShownIn(English, s)
		if LooksLikeError(en) || (en != "" && !ReadsIn(English, en)) || strings.ContainsAny(en, "<>{}\\") {
			t.Errorf("the English rule shows %q of %q", en, s)
		}
		if shown != "" && en == "" {
			t.Errorf("the English rule shows nothing of %q, where the Thai rule shows %q", s, shown)
		}
		for _, own := range []string{"BadZipFile", "0xDEADBEEF", "ModuleLoader", "172.18.0.4", "step.py", "e2_check_failed", "compute_failed", "pkey", "violates"} {
			if strings.Contains(en, own) {
				t.Errorf("the English rule shows an error's own word %q of %q: %q", own, s, en)
			}
		}
	}
	// an answer of several lines is read line by line: a line that is not for a person is left out
	answer := "**รายได้จากการขาย = 12,480,300.00 บาท**\n\nมาจาก:\n- ไฟล์ “GL_2569-09.xlsx” ชีต GL 212 แถว (F12:G900)\n" +
		"Traceback (most recent call last):\n- “ledger.xlsx” แถว 7: 1,200.00 บาท"
	want := "**รายได้จากการขาย = 12,480,300.00 บาท**\n\nมาจาก:\n- ไฟล์ “GL_2569-09.xlsx” ชีต GL 212 แถว (F12:G900)\n- “ledger.xlsx” แถว 7: 1,200.00 บาท"
	if got := ShownToPerson(answer); got != want {
		t.Errorf("line by line:\n%q\n%q", got, want)
	}
	// the three shapes nobody listed
	for _, s := range neverListed {
		for _, in := range []string{s, "อ่านไฟล์ที่ส่งมาไม่สำเร็จ " + s, "อ่านไฟล์ที่ส่งมาไม่สำเร็จ (" + s + ") ลองใหม่อีกครั้ง"} {
			shown := ShownToPerson(in)
			if ReadsForPerson(in) || strings.ContainsAny(shown, "<>@") || regexp.MustCompile(`[A-Za-z]`).MatchString(shown) {
				t.Errorf("%q → %q", in, shown)
			}
		}
	}
}

// Handing a string to whatever writes to a thread does not compile: every
// writer takes a PersonText or one of the thread's own shapes, and in those
// shapes every field the schema sends as a text is a PersonText.
func TestThreadWritersTakeTextBuiltForAPerson(t *testing.T) {
	text := reflect.TypeOf(PersonText{})
	payload := reflect.TypeOf((*ItemPayload)(nil)).Elem()
	store := reflect.TypeOf(&Store{})
	takes := func(method string, arg int, want reflect.Type) {
		t.Helper()
		m, ok := store.MethodByName(method)
		if !ok {
			t.Fatalf("no Store.%s", method)
		}
		if got := m.Type.In(arg); got != want {
			t.Errorf("Store.%s takes %s where a person reads, want %s", method, got, want)
		}
	}
	takes("PostText", 3, text)
	takes("PostNotice", 3, text)
	takes("PostAgentText", 3, reflect.TypeOf(AgentText{}))
	takes("AddNotice", 4, reflect.TypeOf(Notice{}))
	takes("AddItem", 7, payload)
	takes("AddItemIn", 8, payload)
	takes("UpdateItem", 3, payload)
	takes("PatchItem", 4, reflect.TypeOf(ItemPatch{}))
	// a string has no place in any of them
	for _, m := range []string{"PostText", "PostNotice", "PostAgentText", "AddNotice", "AddItem", "AddItemIn", "UpdateItem", "PatchItem"} {
		mt, _ := store.MethodByName(m)
		for i := 1; i < mt.Type.NumIn(); i++ {
			if in := mt.Type.In(i); in.Kind() == reflect.Interface && in.NumMethod() == 0 {
				t.Errorf("Store.%s takes anything (%s)", m, in)
			}
		}
	}
	raw := reflect.TypeOf(json.RawMessage{})
	var walk func(where string, typ reflect.Type, f field)
	walk = func(where string, typ reflect.Type, f field) {
		for typ.Kind() == reflect.Pointer {
			typ = typ.Elem()
		}
		switch {
		case typ == raw || typ.Kind() == reflect.Map || typ.Kind() == reflect.Interface:
			// a package's own data (a card's questions, a summary): read by the schema when it is sent
		case f.class == asText:
			if typ != text {
				t.Errorf("%s is sent as a text and is a %s", where, typ)
			}
		case f.class == asList && typ.Kind() == reflect.Slice:
			walk(where+"[]", typ.Elem(), *f.each)
		case f.class == asObject && typ.Kind() == reflect.Struct && typ != text:
			for i := 0; i < typ.NumField(); i++ {
				sf := typ.Field(i)
				name := strings.Split(sf.Tag.Get("json"), ",")[0]
				if name == "" || name == "-" {
					continue
				}
				sub, ok := f.fields[name]
				if !ok {
					t.Errorf("%s.%s is written and never sent: the schema does not declare it", where, name)
					continue
				}
				walk(where+"."+name, sf.Type, sub)
			}
		}
	}
	for _, p := range []ItemPayload{Greeting{}, AgentText{}, Notice{}, CardPayload{}, Progress{}, OutputsPayload{}, IntentPayload{}} {
		walk(p.itemKind(), reflect.TypeOf(p), itemSchema[p.itemKind()])
	}
	// a patch changes what the shapes declare, and its one text is a PersonText
	pt := reflect.TypeOf(ItemPatch{})
	for i := 0; i < pt.NumField(); i++ {
		name := strings.Split(pt.Field(i).Tag.Get("json"), ",")[0]
		declared := false
		for _, f := range itemSchema {
			if sub, ok := f.fields[name]; ok {
				declared = true
				if typ := pt.Field(i).Type; sub.class == asText && typ != reflect.PointerTo(text) && typ != text {
					t.Errorf("ItemPatch.%s is sent as a text and is a %s", name, typ)
				}
			}
		}
		if !declared {
			t.Errorf("ItemPatch.%s changes a field no schema declares", name)
		}
	}
	// a PersonText is made in one file
	made := regexp.MustCompile(`PersonText\{\s*s:|\bwording\(|\bFact\{`)
	for _, dir := range []string{".", "runner"} {
		files, _ := filepath.Glob(filepath.Join(dir, "*.go"))
		for _, f := range files {
			if filepath.Base(f) == "persontext.go" || strings.HasSuffix(f, "_test.go") {
				continue
			}
			src, err := os.ReadFile(f)
			if err != nil {
				t.Fatal(err)
			}
			for i, line := range strings.Split(string(src), "\n") {
				if j := strings.Index(line, "//"); j >= 0 {
					line = line[:j]
				}
				if made.MatchString(line) {
					t.Errorf("%s:%d makes a text for a person outside persontext.go: %s", f, i+1, strings.TrimSpace(line))
				}
			}
		}
	}
	// reading JSON is no way around it. AM11: a row does not say which language it was written in, so a
	// text read from one is what the reader of both languages shows of it — never a word that no sentence
	// of the product uses, never a sign that is not ours — and where the text is then sent, the rule of
	// the item's own language is asked once more: an item written in Thai shows nothing of it, as before.
	for _, s := range neverListed {
		var smuggled PersonText
		_ = json.Unmarshal(mustJSON(s), &smuggled)
		got := smuggled.String()
		if got != ShownIn(English, s) || strings.ContainsAny(got, "<>@") || LooksLikeError(got) ||
			(got != "" && !readerFor(English).Reads(got)) {
			t.Errorf("a text read from JSON is only what a person is shown of it: %q → %q", s, got)
		}
		for _, w := range []string{"zx81", "frobnicate", "retry", "0xDEADBEEF", "ModuleLoader", "ValueProblem", "Pipeline", "halted"} {
			if strings.Contains(got, w) {
				t.Errorf("a word nobody's sentence uses came back from JSON: %q in %q", w, got)
			}
		}
		var sent struct {
			Text string `json:"text"`
		}
		_ = json.Unmarshal(ForClient("notice", mustJSON(map[string]any{"text": s})), &sent)
		if sent.Text != "" {
			t.Errorf("an item written in Thai shows %q of %q", sent.Text, s)
		}
	}
	for _, s := range neverListed {
		if got := FromPackage("ตรวจไม่ผ่าน "+s, NoText).String(); strings.Contains(got, s) {
			t.Errorf("a package's sentence is only what a person is shown of it: %q", got)
		}
		if got := Text("เปิดไฟล์ %s ไม่ได้", Name(s)).String(); !ReadsForPerson(got) {
			t.Errorf("a name that is not plainly readable is quoted as the person's own: %q", got)
		}
	}
}

func TestEveryKindOfItemIsDeclared(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	var def string
	if err := e.store.Pool.QueryRow(context.Background(), `SELECT pg_get_constraintdef(oid) FROM pg_constraint
		WHERE conname = 'agent_thread_items_kind_check'`).Scan(&def); err != nil {
		t.Fatal(err)
	}
	kinds := regexp.MustCompile(`'([a-z_]+)'`).FindAllStringSubmatch(def, -1)
	if len(kinds) < 9 {
		t.Fatalf("the kinds the table takes: %s", def)
	}
	for _, k := range kinds {
		if _, ok := itemSchema[k[1]]; !ok {
			t.Errorf("an item of kind %q has no schema: nothing of it would be sent", k[1])
		}
	}
	if len(itemSchema) != len(kinds) {
		t.Errorf("%d schemas for %d kinds", len(itemSchema), len(kinds))
	}
	// a kind nobody declared sends nothing of its own
	if got := string(ForClient("debug", json.RawMessage(`{"text":"สวัสดี","trace":"x"}`))); got != "{}" {
		t.Errorf("an undeclared kind: %s", got)
	}
	for _, line := range SchemaLines() {
		t.Log(line)
	}
}

func TestMigration005DownUndoesTheChargingColumns(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	crashed := e.endedRun(in, RunFailed, "render_failed", 40, 7, 3)
	columns := func() int {
		var n int
		_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market' AND (
			(table_name = 'agent_budget_ledger' AND column_name IN ('voided_at', 'void_reason')) OR
			(table_name = 'agent_runs' AND column_name IN ('credits_cost', 'gate_open', 'delivered_at')))`).Scan(&n)
		return n
	}
	if columns() != 5 || crashed.CreditsSpent != 0 || crashed.CreditsCost != 10 {
		t.Fatalf("after up: %d columns, charged %v, cost %v", columns(), crashed.CreditsSpent, crashed.CreditsCost)
	}
	down, err := migrationFS.ReadFile("migrations/005_am6.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatalf("down: %v", err)
	}
	var rec int
	var spent, ledger float64
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.schema_migrations WHERE version = '005_am6'`).Scan(&rec)
	_ = e.store.Pool.QueryRow(ctx, `SELECT credits_spent::float8 FROM agent_market.agent_runs WHERE id = $1`, crashed.ID).Scan(&spent)
	_ = e.store.Pool.QueryRow(ctx, `SELECT COALESCE(sum(credits), 0)::float8 FROM agent_market.agent_budget_ledger WHERE run_id = $1`, crashed.ID).Scan(&ledger)
	if columns() != 0 || rec != 0 {
		t.Fatalf("down leaves %d columns, record %d", columns(), rec)
	}
	if spent != 10 || ledger != 10 {
		t.Fatalf("the rows are kept, and a run is charged what its ledger says again: %v %v", spent, ledger)
	}
	did, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(did) != 1 || did[0] != "005_am6" {
		t.Fatalf("up again: %v %v", did, err)
	}
	if columns() != 5 {
		t.Fatalf("after up again: %d columns", columns())
	}
	if r, err := e.store.RunByID(ctx, crashed.ID); err != nil || r.CreditsCost != 10 {
		t.Fatalf("what was charged before the rule stays the run's cost: %+v %v", r, err)
	}
}
