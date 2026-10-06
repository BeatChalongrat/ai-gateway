package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// AM11 — the control plane in English, end to end. Each test walks one thing
// twice: once as every client before this card does it (no language named —
// Thai, and exactly what it was), once with the screen's language named.
//
// The rule these hold the code to (lang.go): what answers a request is in the
// request's language; what is written for a run is in the run's language,
// whichever screen asked for it.

const inEnglish = "en"

func thaiLetters(s string) bool { return strings.ContainsFunc(s, isThai) }

// without is s with the person's own data taken out of it (a file's name, an agent's): what is left is ours.
func without(s string, theirs ...string) string {
	for _, x := range theirs {
		if x != "" {
			s = strings.ReplaceAll(s, x, "")
		}
	}
	return s
}

func messageOfErr(t *testing.T, body map[string]any) (code, message string) {
	t.Helper()
	e, _ := body["error"].(map[string]any)
	code, _ = e["code"].(string)
	message, _ = e["message"].(string)
	if code == "" || message == "" {
		t.Fatalf("not an error a person reads: %v", body)
	}
	return code, message
}

// (1) The same refusal, in the language the request names.
func TestTheSameRefusalIsSaidInTheRequestsLanguage(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	e.seedDefinition()
	// before any runner is there: RUNNER_DOWN names whom to ask ({contact} — here the organisation's
	// admin, by address; with nobody named, the stand-in for them is a sentence of ours, in the
	// message's language)
	for _, lang := range []string{"", inEnglish} {
		status, body := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"}, "Accept-Language", lang)
		code, msg := messageOfErr(t, body)
		l := ParseLang(lang)
		if want := MessageIn(l, "RUNNER_DOWN", "contact", "am1-test@example.test"); status != 503 || code != "RUNNER_DOWN" || msg != want {
			t.Fatalf("lang %q: %d %s\n got  %s\n want %s", lang, status, code, msg, want)
		}
		if standIn := MessageIn(l, "RUNNER_DOWN"); !strings.Contains(standIn, (Contact{}).LabelIn(l)) || thaiLetters(standIn) != (l == Thai) ||
			thaiLetters(msg) != (l == Thai) {
			t.Fatalf("lang %q: whom to ask is not said in it: %s / %s", lang, msg, standIn)
		}
	}
	var nobody Contact
	if nobody.LabelIn(Thai) != "ผู้ดูแล Agent Market ขององค์กร" || nobody.LabelIn(English) != "your organisation's Agent Market admin" {
		t.Fatalf("the stand-in for nobody named: %q / %q", nobody.LabelIn(Thai), nobody.LabelIn(English))
	}
	in := e.install()
	ctx := context.Background()
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	if err != nil {
		t.Fatal(err)
	}
	badPolicy := json.RawMessage(`{"schema":"x"}`)
	long := strings.Repeat("a", 4001)
	for _, c := range []struct {
		name, method, path string
		body               any
		status             int
		code               string
		vars               func(l Lang) []string
	}{
		{"{what}: a run that is not there", "GET", "/runs/" + uuid.NewString(), nil, 404, "NOT_FOUND",
			func(l Lang) []string { return []string{"what", whatIn(l, "run")} }},
		{"{detail}: built by the policy table's validator", "PUT", "/installs/" + in.ID.String() + "/defaults/policy",
			map[string]any{"value": badPolicy}, 400, "BAD_VALUE",
			func(l Lang) []string { return []string{"detail", settingProblem(l, "policy", badPolicy)} }},
		{"{why} and {next}: a receipt on a run still in progress", "POST", "/runs/" + run.ID.String() + "/receipts",
			map[string]any{"key": "po:1", "quantity": "all"}, 409, "RECEIPT_NOT_OPEN",
			func(l Lang) []string {
				return []string{"why", T(l, "รอบนี้ยังทำอยู่"), "next", T(l, "รอให้รอบนี้เสร็จก่อน แล้วค่อยยืนยันการรับของ")}
			}},
		{"no variable: a message that is too long", "POST", "/installs/" + in.ID.String() + "/messages",
			map[string]any{"text": long}, 400, "TOO_LONG", func(Lang) []string { return nil }},
		{"{code}: a request the web never sends", "GET", "/runs/not-an-id", nil, 400, "BAD_ID",
			func(Lang) []string { return nil }},
	} {
		said := map[Lang]string{}
		for _, lang := range []string{"", inEnglish} {
			l := ParseLang(lang)
			status, body := e.do(c.method, c.path, c.body, "Accept-Language", lang)
			code, msg := messageOfErr(t, body)
			want := MessageIn(l, c.code, c.vars(l)...)
			if c.code == "BAD_ID" {
				want = MessageIn(l, "BAD_REQUEST", "code", "BAD_ID")
			}
			if status != c.status || code != c.code || msg != want {
				t.Errorf("%s, lang %q: %d %s\n got  %s\n want %s", c.name, lang, status, code, msg, want)
			}
			if l == English && thaiLetters(msg) {
				t.Errorf("%s: Thai in the English message: %s", c.name, msg)
			}
			if l == Thai && !thaiLetters(msg) {
				t.Errorf("%s: the Thai message is not Thai: %s", c.name, msg)
			}
			said[l] = msg
		}
		if said[Thai] == said[English] {
			t.Errorf("%s: the two languages say the same thing: %s", c.name, said[Thai])
		}
	}
	// the Thai of one of them, word for word, as it was before there were two languages
	_, body := e.do("GET", "/runs/"+uuid.NewString(), nil)
	if _, msg := messageOfErr(t, body); msg != "ไม่พบรอบงานนี้ อาจถูกลบไปแล้วหรือเป็นของบัญชีอื่น — กลับไปหน้า Agent Market แล้วเปิดใหม่อีกครั้ง "+
		"ข้อมูลอื่นของคุณไม่ได้รับผลกระทบ" {
		t.Fatalf("the Thai message changed: %s", msg)
	}
}

func (e *testEnv) paramsOf(run uuid.UUID) string {
	e.t.Helper()
	var p json.RawMessage
	if err := e.store.Pool.QueryRow(context.Background(), `SELECT params FROM agent_market.agent_runs WHERE id = $1`, run).Scan(&p); err != nil {
		e.t.Fatal(err)
	}
	var v any
	_ = json.Unmarshal(p, &v)
	b, _ := json.Marshal(v) // as JSON text, whatever spacing the database gives it back with
	return string(b)
}

// startRun sends a message with one file in the named language and returns the run it starts.
func (e *testEnv) startRun(in Install, file, lang string) Run {
	e.t.Helper()
	f := e.stagedFile(in, file)
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "close September",
		"file_ids": []string{f.ID.String()}, "client_id": "c-" + file}, "Accept-Language", lang)
	if code != 200 || body["route"] != "new_run" {
		e.t.Fatalf("message: %d %v", code, body)
	}
	run, err := e.store.RunByID(context.Background(), uuid.MustParse(body["run_id"].(string)))
	if err != nil {
		e.t.Fatal(err)
	}
	return run
}

// (2) A run has the language of the screen that started it; a job done for it has the run's.
func TestARunHasTheLanguageItWasStartedIn(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	th := e.startRun(in, "GL_th.xlsx", "")
	if got := e.paramsOf(th.ID); got != "{}" || th.Lang() != Thai {
		t.Fatalf("a run started in Thai has params %s (want exactly {}, as before AM11)", got)
	}
	// its row, cancelled, makes room for the next
	_, _ = e.do("POST", "/runs/"+th.ID.String()+"/cancel", nil)
	en := e.startRun(in, "GL_en.xlsx", inEnglish)
	if got := e.paramsOf(en.ID); got != `{"lang":"en"}` || en.Lang() != English {
		t.Fatalf("a run started in English has params %s", got)
	}
	// a job for the English run, made under a context that says Thai (another screen, the runner's own): English
	for _, jobCtx := range []context.Context{ctx, WithLang(ctx, Thai)} {
		parent := en.ID
		job, _, err := e.store.CreateRun(jobCtx, e.store.Pool, Run{InstallID: in.ID, ParentRunID: &parent, TenantID: in.TenantID,
			UserID: in.UserID, Kind: KindRender, Params: json.RawMessage(`{"formats":["xlsx"],"why":"api"}`),
			DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
		if err != nil {
			t.Fatal(err)
		}
		if job.Lang() != English || !strings.Contains(e.paramsOf(job.ID), `"lang":"en"`) || !strings.Contains(e.paramsOf(job.ID), `"why":"api"`) {
			t.Fatalf("a job of an English run: params %s", e.paramsOf(job.ID))
		}
	}
	// … and a job for a Thai run asked for from an English screen stays Thai, with its params as they were given
	parent := th.ID
	job, _, err := e.store.CreateRun(WithLang(ctx, English), e.store.Pool, Run{InstallID: in.ID, ParentRunID: &parent, TenantID: in.TenantID,
		UserID: in.UserID, Kind: KindRender, Params: json.RawMessage(`{"formats":["xlsx"]}`), DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	if err != nil || job.Lang() != Thai || e.paramsOf(job.ID) != `{"formats":["xlsx"]}` {
		t.Fatalf("a job of a Thai run: %v params %s", err, e.paramsOf(job.ID))
	}
}

func (e *testEnv) storedPayload(itemID string) string {
	e.t.Helper()
	var p string
	if err := e.store.Pool.QueryRow(context.Background(), `SELECT payload::text FROM agent_market.agent_thread_items WHERE id = $1`, itemID).Scan(&p); err != nil {
		e.t.Fatal(err)
	}
	return p
}

func (e *testEnv) itemsOfRun(in Install, run Run, kind string) []map[string]any {
	e.t.Helper()
	var out []map[string]any
	for _, it := range e.threadItems(in, "") {
		if it["kind"] == kind && it["run_id"] == run.ID.String() {
			out = append(out, it)
		}
	}
	return out
}

// (3) (4) What the control plane writes into a run's thread is in the run's language — a card it builds
// itself, a notice — and an English item says so ("lang":"en"); the same in Thai is what it always was.
func TestWhatIsWrittenIntoARunsThreadIsInTheRunsLanguage(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	const thaiQuestion = "ได้รับ late.xlsx ระหว่างที่กำลังทำรอบนี้ จะให้รวมในรอบนี้ หรือเก็บไว้ทำรอบใหม่ครับ"
	// the run's language, and the language of the screen that sends the late file — which is the other one
	for _, c := range []struct{ runLang, requestLang string }{{"", inEnglish}, {inEnglish, ""}} {
		run := e.startRun(in, "GL"+c.runLang+".xlsx", c.runLang)
		late := e.stagedFile(in, "late.xlsx")
		code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{late.ID.String()},
			"client_id": "late-" + c.runLang}, "Accept-Language", c.requestLang)
		if code != 200 || body["route"] != "late_file" {
			t.Fatalf("a file sent while a run works asks first: %d %v", code, body)
		}
		cards := e.itemsOfRun(in, run, "card")
		if len(cards) != 1 {
			t.Fatalf("run in %q: %d cards", c.runLang, len(cards))
		}
		p := cards[0]["payload"].(map[string]any)
		q := p["questions"].([]any)[0].(map[string]any)
		stored := e.storedPayload(cards[0]["id"].(string))
		var labels []string
		for _, o := range q["options"].([]any) {
			labels = append(labels, o.(map[string]any)["label"].(string))
		}
		if c.runLang == "" {
			// exactly the card of before AM11, although an English screen sent the file
			if q["header"] != "ไฟล์เพิ่ม" || q["question"] != thaiQuestion || strings.Join(labels, "|") != "รวมในรอบนี้|เก็บไว้ทำรอบใหม่" ||
				q["default_label"] != "รวมในรอบนี้" || strings.Contains(stored, `"lang"`) || p["lang"] != nil {
				t.Fatalf("a Thai run's card changed: %v\nstored %s", q, stored)
			}
		} else {
			if !strings.Contains(stored, `"lang": "en"`) && !strings.Contains(stored, `"lang":"en"`) {
				t.Fatalf("an English run's card is not marked: %s", stored)
			}
			if p["lang"] != "en" || q["header"] != "More files" || DisplayLen(q["header"].(string)) > 12 ||
				q["question"] != "I received late.xlsx while working on this run. Include in this run, or keep for a new run?" ||
				strings.Join(labels, "|") != "Include in this run|Keep for a new run" || q["default_label"] != "Include in this run" {
				t.Fatalf("an English run's card, sent the file from a Thai screen: %v", p)
			}
			b, _ := json.Marshal(p)
			if thaiLetters(string(b)) {
				t.Fatalf("Thai on an English run's card: %s", b)
			}
		}
		// the label a person clicks is the label they were shown — the card's own language — whatever the
		// language of the screen that sends the click: it is taken as that option, not read as free text
		code, body = e.do("POST", "/cards/"+p["card_id"].(string)+"/answer",
			map[string]any{"answers": map[string]any{"late_file": map[string]any{"labels": []string{labels[0]}}}}, "Accept-Language", c.requestLang)
		if code != 200 || body["changed"] != true || body["status"] == "interpreting" || body["card"].(map[string]any)["status"] != "answered" {
			t.Fatalf("run in %q: clicking %q on its card: %d %v", c.runLang, labels[0], code, body)
		}
		if files, _ := e.store.RunFiles(context.Background(), run.ID, "active"); len(files) != 2 {
			t.Fatalf("run in %q: the late file was to be included: %d active files", c.runLang, len(files))
		}
		// a notice about the run, written for a request in the other language: the run's language still
		code, body = e.do("POST", "/runs/"+run.ID.String()+"/problem", map[string]any{"text": "the total looks wrong", "page": "run"},
			"Accept-Language", c.requestLang)
		if code != 200 {
			t.Fatalf("problem: %d %v", code, body)
		}
		notices := e.itemsOfRun(in, run, "notice")
		said, _ := notices[len(notices)-1]["payload"].(map[string]any)["text"].(string)
		answered, _ := body["message"].(string)
		if c.runLang == "" {
			if !strings.HasPrefix(said, "ส่งเรื่องถึงผู้ดูแล Agent Market แล้ว (เลขอ้างอิง #") || thaiLetters(answered) {
				t.Fatalf("a Thai run's notice: %q; the English request's own answer: %q", said, answered)
			}
		} else {
			if !strings.HasPrefix(said, "Your report has been sent to the Agent Market admin (reference #") || thaiLetters(said) ||
				!strings.HasPrefix(answered, "ส่งเรื่องถึงผู้ดูแล Agent Market แล้ว") {
				t.Fatalf("an English run's notice: %q; the Thai request's own answer: %q", said, answered)
			}
		}
		_, _ = e.do("POST", "/runs/"+run.ID.String()+"/cancel", nil)
	}
	// a failed run tried again from an English screen: the new run is English, and so is what the thread says of it
	failed := e.failedRun(in, "compute_failed", "GL_retry.xlsx")
	code, body := e.do("POST", "/runs/"+failed.ID.String()+"/retry", nil, "Accept-Language", inEnglish)
	if code != 200 && code != 202 {
		t.Fatalf("retry: %d %v", code, body)
	}
	again, err := e.store.RunByID(context.Background(), uuid.MustParse(body["run"].(map[string]any)["id"].(string)))
	if err != nil || again.Lang() != English {
		t.Fatalf("the run a retry starts: %v %s", err, again.Params)
	}
	notices := e.itemsOfRun(in, again, "notice")
	if len(notices) != 1 || notices[0]["payload"].(map[string]any)["text"] != "Trying this run again with the same files (1): GL_retry.xlsx" ||
		notices[0]["payload"].(map[string]any)["lang"] != "en" {
		t.Fatalf("an English retry's notice: %v", notices)
	}
	_, _ = e.do("POST", "/runs/"+again.ID.String()+"/cancel", nil)
	failed = e.failedRun(in, "compute_failed", "GL_retry2.xlsx")
	_, body = e.do("POST", "/runs/"+failed.ID.String()+"/retry", nil)
	again, _ = e.store.RunByID(context.Background(), uuid.MustParse(body["run"].(map[string]any)["id"].(string)))
	notices = e.itemsOfRun(in, again, "notice")
	if len(notices) != 1 || notices[0]["payload"].(map[string]any)["text"] != "ลองรอบนี้ใหม่ด้วยไฟล์เดิม 1 ไฟล์: GL_retry2.xlsx" ||
		notices[0]["payload"].(map[string]any)["lang"] != nil || e.paramsOf(again.ID) != `{"retry_of":"`+failed.ID.String()+`"}` {
		t.Fatalf("a Thai retry changed: %v params %s", notices, e.paramsOf(again.ID))
	}
}

// (4) Every header of a card the platform builds fits twelve character cells in both languages
// (GLOSSARY.md §6 rule 5), and a card's own words have their English.
func TestAPlatformCardsHeaderFitsInBothLanguages(t *testing.T) {
	both := bothPlanesEnglish(t)
	for _, header := range []string{"ไฟล์เพิ่ม", "ขอไฟล์", "ชุดไฟล์", "ชนิดไฟล์", "ขอไฟล์ใหม่", "คำถาม"} {
		en, ok := both[header]
		if !ok {
			t.Errorf("the header %q has no English", header)
			continue
		}
		if DisplayLen(header) > 12 || DisplayLen(en) > 12 {
			t.Errorf("a card's header must fit 12 cells: %q (%d) / %q (%d)", header, DisplayLen(header), en, DisplayLen(en))
		}
	}
	// the "which files" card, as a run of each language is asked it
	var m Manifest
	if err := json.Unmarshal([]byte(testManifest), &m); err != nil {
		t.Fatal(err)
	}
	th := OutputSetFinding(Thai, m, nil, []string{"docx", "pptx"})
	en := OutputSetFinding(English, m, nil, []string{"docx", "pptx"})
	if th == nil || en == nil {
		t.Fatal("the manifest makes three kinds of file: the question is asked")
	}
	if th.Header != "ชุดไฟล์" || th.Options[0].Label != "รายงาน Word + สไลด์ (ชุดมาตรฐาน)" ||
		th.Question != "รอบนี้ต้องการไฟล์แบบไหนครับ ถ้าไม่ตอบจะทำชุดมาตรฐานให้ก่อน และจำคำตอบไว้ใช้รอบหน้า" {
		t.Fatalf("the Thai question changed: %+v", th)
	}
	if en.Header != "Which files" || en.Options[0].Label != "Word report + PowerPoint slides (standard set)" ||
		en.Options[2].Label != "Word report only" || thaiLetters(en.Question) || en.Validate() != nil {
		t.Fatalf("the English question: %+v (%v)", en, en.Validate())
	}
	for i := range th.Options {
		if string(th.Options[i].Value) != string(en.Options[i].Value) {
			t.Fatalf("an option means the same in both languages: %s / %s", th.Options[i].Value, en.Options[i].Value)
		}
	}
}

// (5) A reminder speaks the language of the screen that set it: the run it opens, that run's card and
// thread, and the notification. A notification about a run's waiting question is in the run's language.
func TestAReminderAndItsNotificationAreInItsLanguage(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	due := thaiAt(2026, 10, 1, 9, 0)
	for _, lang := range []string{"", inEnglish} {
		label := "ปิดงบ"
		if lang == inEnglish {
			label = "Close"
		}
		out := e.mustDo(201, "POST", "/installs/"+in.ID.String()+"/reminders",
			map[string]any{"label": label, "rule": json.RawMessage(`{"every":"month","day":1,"time":"09:00"}`)}, "Accept-Language", lang)
		rem := out["reminder"].(map[string]any)
		id := rem["id"].(string)
		var rule string
		_ = e.store.Pool.QueryRow(ctx, `SELECT rule::text FROM agent_market.reminders WHERE id = $1`, id).Scan(&rule)
		if strings.Contains(rule, `"lang"`) != (lang == inEnglish) {
			t.Fatalf("lang %q: the rule kept is %s", lang, rule)
		}
		e.dueAt(id, due)
		if p := e.pass(due.Add(time.Minute)); p.Fired != 1 {
			t.Fatalf("lang %q: the reminder did not fire: %+v", lang, p)
		}
		var runID uuid.UUID
		var title, body string
		if err := e.store.Pool.QueryRow(ctx, `SELECT run_id, title, body FROM agent_market.notifications WHERE reminder_id = $1 AND kind = 'reminder_due'`,
			id).Scan(&runID, &title, &body); err != nil {
			t.Fatal(err)
		}
		run, _ := e.store.RunByID(ctx, runID)
		card := e.itemsOfRun(in, run, "card")
		said := e.itemsOfRun(in, run, "agent_text")
		if len(card) != 1 || len(said) != 1 {
			t.Fatalf("lang %q: the waiting run's thread: %d cards, %d texts", lang, len(card), len(said))
		}
		q := card[0]["payload"].(map[string]any)["questions"].([]any)[0].(map[string]any)
		text := said[0]["payload"].(map[string]any)["text"].(string)
		if lang == "" {
			if e.paramsOf(runID) != "{}" || title != "ถึงเวลาปิดงบ ก.ย. 2569 แล้ว" || body != "ต้องใช้ 1 ไฟล์นี้: GL" || q["header"] != "ขอไฟล์" ||
				q["question"] != "ถึงเวลาปิดงบ ก.ย. 2569 แล้ว ส่งไฟล์ของงวดนี้มาได้เลย รอบนี้จะเริ่มเมื่อได้รับไฟล์" ||
				!strings.HasPrefix(text, "ถึงเวลาปิดงบ ก.ย. 2569 แล้ว — การเตือนที่ตั้งไว้ (ทุกวันที่ 1 ของเดือน เวลา 09:00 น. · สำหรับงานของเดือนก่อนหน้า) เปิดรอบนี้รอไฟล์ไว้ให้") {
				t.Fatalf("a Thai reminder changed:\n params %s\n title %s\n body %s\n card %v\n text %s", e.paramsOf(runID), title, body, q, text)
			}
		} else {
			if run.Lang() != English || title != "It is time for Close (Sep 2026)" || body != "Files needed (1): GL" ||
				q["header"] != "Files needed" || DisplayLen(q["header"].(string)) > 12 ||
				q["question"] != "It is time for Close (Sep 2026). Send this period's files — this run starts when the files arrive." ||
				q["default_label"] != "Wait for my upload" ||
				!strings.HasPrefix(text, "It is time for Close (Sep 2026) — the reminder you set (On day 1 of every month at 09:00 · for the previous month's work) opened this run") {
				t.Fatalf("an English reminder:\n params %s\n title %s\n body %s\n card %v\n text %s", e.paramsOf(runID), title, body, q, text)
			}
			b, _ := json.Marshal([]any{card[0]["payload"], said[0]["payload"], title, body})
			if thaiLetters(string(b)) {
				t.Fatalf("Thai in what an English reminder wrote: %s", b)
			}
			// the list of reminders says the period in the reader's language, not in the reminder's
			if got := e.getReminder(in, id)["period_label"]; got != "ก.ย. 2569" {
				t.Fatalf("a Thai screen reads the period as %v", got)
			}
		}
		// three days on and still no file: the one follow-up, in the same language — and the mail's own lines too
		if p := e.pass(due.Add(3*24*time.Hour + 2*time.Minute)); p.FollowUps != 1 {
			t.Fatalf("lang %q: no follow-up: %+v", lang, p)
		}
		var again string
		_ = e.store.Pool.QueryRow(ctx, `SELECT title FROM agent_market.notifications WHERE reminder_id = $1 AND kind = 'reminder_followup'`, id).Scan(&again)
		mail := reminderMessage(run.Lang(), again, body, "https://chat.example.test/chat/agents/i/x")
		if lang == "" {
			if again != "ยังรอไฟล์สำหรับปิดงบ ก.ย. 2569" || mail.Subject != again ||
				mail.Body != again+"\n\n"+body+"\n\nเปิดรอบนี้และส่งไฟล์ได้ที่: https://chat.example.test/chat/agents/i/x"+
					"\n\n— Agent Market · อีเมลนี้มาจากการเตือนที่คุณตั้งไว้ในหน้า agent หยุดหรือเลื่อนการเตือนได้ที่หน้าเดียวกัน" {
				t.Fatalf("a Thai reminder's follow-up changed: %q\n%s", again, mail.Body)
			}
		} else if again != "Still waiting for the files for Close (Sep 2026)" || thaiLetters(mail.Subject+mail.Body) ||
			!strings.Contains(mail.Body, "\n\nOpen this run and send the files at: https://chat.example.test/chat/agents/i/x\n\n— Agent Market · This email comes from a reminder you set") {
			t.Fatalf("an English reminder's follow-up: %q\n%s", again, mail.Body)
		}
		_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'cancelled', finished_at = now() WHERE id = $1`, runID)
		_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.reminders SET status = 'stopped' WHERE id = $1`, id)
	}
	// a run parked on a question nobody looked at: the notification is in the run's language
	for _, lang := range []string{"", inEnglish} {
		run, card := e.parkedOnAQuestion(in, 5*time.Hour)
		if lang == inEnglish {
			if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET params = '{"lang":"en"}' WHERE id = $1`, run.ID); err != nil {
				t.Fatal(err)
			}
		}
		e.pass(time.Now())
		var title, body string
		if err := e.store.Pool.QueryRow(ctx, `SELECT title, body FROM agent_market.notifications WHERE card_id = $1 AND kind = 'waiting'`,
			card.ID).Scan(&title, &body); err != nil {
			t.Fatal(err)
		}
		if lang == "" && (title != in.Title+" รอคำตอบจากคุณ" || body != "มีคำถาม 1 ข้อที่ต้องตอบก่อน agent จะทำต่อ เปิดดูแล้วตอบในการ์ดคำถามได้เลย") {
			t.Fatalf("a Thai run's waiting notification changed: %s / %s", title, body)
		}
		if lang == inEnglish && (thaiLetters(without(title+body, in.Title)) || !strings.Contains(title, in.Title) || !strings.Contains(body, "1")) {
			t.Fatalf("an English run's waiting notification: %s / %s", title, body)
		}
		_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'cancelled', finished_at = now() WHERE id = $1`, run.ID)
	}
}

// (5) An e-mail draft is the run's: built, kept and sent in the run's language, whichever screen asks for it.
func TestAnEmailDraftIsInTheRunsLanguage(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	ctx := context.Background()
	summary := map[string]any{"period": "2026-09", "period_label": "กันยายน 2569", "revenue": "1250000.00"}
	for _, lang := range []string{"", inEnglish} {
		run, arts := e.deliveredRound(in, summary)
		params := `{}`
		if lang == inEnglish {
			params = `{"lang":"en"}`
		}
		if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET params = $2, period = '2026-09' WHERE id = $1`, run.ID, params); err != nil {
			t.Fatal(err)
		}
		// asked for from a screen in the OTHER language
		other := inEnglish
		if lang == inEnglish {
			other = ""
		}
		d := draftOf(t, e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/email-draft", nil, "Accept-Language", other))
		subject, body := d["subject"].(string), d["body"].(string)
		theirs := []string{in.Title, "GL_2569-09.xlsx"}
		for _, a := range arts {
			theirs = append(theirs, a.Name)
		}
		var kept, keptBody string
		_ = e.store.Pool.QueryRow(ctx, `SELECT subject, body FROM agent_market.email_drafts WHERE run_id = $1`, run.ID).Scan(&kept, &keptBody)
		if kept != subject || keptBody != body {
			t.Fatalf("lang %q: what is kept is not what is shown:\n %s\n %s", lang, kept, subject)
		}
		if lang == "" {
			if subject != in.Title+" · กันยายน 2569 · เสร็จแล้ว" || !strings.HasPrefix(body, "สรุปผลของ "+in.Title+" งวด กันยายน 2569\nสถานะ: เสร็จแล้ว") ||
				!strings.Contains(body, "· รายได้รวม 1,250,000.00 บาท") || !strings.Contains(body, "ไฟล์แนบ 2 ไฟล์") {
				t.Fatalf("a Thai run's draft changed:\n%s\n%s", subject, body)
			}
			continue
		}
		if subject != in.Title+" · September 2026 · Finished" || !strings.HasPrefix(body, "Summary of results: "+in.Title+", period September 2026\nStatus: Finished") ||
			!strings.Contains(body, "· Total revenue: THB 1,250,000.00") || !strings.Contains(body, "Attachments: 2") ||
			thaiLetters(without(subject+body, theirs...)) {
			t.Fatalf("an English run's draft:\n%s\n%s", subject, body)
		}
		// read again from a Thai screen: the same draft, and its hash still that of what is kept
		again := draftOf(t, e.mustDo(200, "GET", "/email-drafts/"+d["id"].(string), nil))
		if again["subject"] != subject || again["body"] != body || again["body_sha256"] != bodyHash(body) {
			t.Fatalf("an English draft read back: %v", again)
		}
		// … and its item in the thread is marked English, so that it is read as English
		item := lastOfKind(e.threadItems(in, ""), "email_draft")
		if p := item["payload"].(map[string]any); p["lang"] != "en" || p["subject"] != subject || p["body"] != body {
			t.Fatalf("the draft's thread item: %v", item["payload"])
		}
	}
}

// (6) What is made when it is read — the data sheet, the model menu, the storage notes, a chat's name —
// is in the language of the request that reads it.
func TestWhatIsMadeWhenItIsReadFollowsTheRequest(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	texts := func(v any) string { b, _ := json.Marshal(v); return string(b) }
	for _, lang := range []string{"", inEnglish} {
		l := ParseLang(lang)
		// the data sheet
		ds := e.mustDo(200, "GET", "/catalog/sran.financial-report/datasheet", nil, "Accept-Language", lang)
		iso := ds["isolation"].([]any)
		if len(iso) != 3 || iso[0] != T(l, "ทุกขั้นตอนทำงานในกล่องแยก (sandbox) ที่ไม่มีเครือข่าย ออกอินเทอร์เน็ตไม่ได้") ||
			ds["notes"].(map[string]any)["not_archive"] != T(l, "ที่นี่ไม่ใช่ที่เก็บเอกสารของบริษัท — ต้นฉบับยังต้องเก็บตามที่บริษัทเก็บอยู่ ระบบเก็บสำเนาและหลักฐานว่าตัวเลขคำนวณจากไฟล์ใด") {
			t.Fatalf("lang %q: the data sheet: %v %v", lang, iso, ds["notes"])
		}
		ours := texts([]any{ds["isolation"], ds["no_model"], ds["notes"], ds["retention"]})
		if thaiLetters(ours) != (l == Thai) {
			t.Fatalf("lang %q: the data sheet's own lines: %s", lang, ours)
		}
		// the model menu
		menu := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/models", nil, "Accept-Language", lang)
		if menu["reads"] != T(l, "agent นี้ไม่ส่งเอกสารให้โมเดลใดเลย ทุกค่าอ่านจากไฟล์และคำนวณด้วยสคริปต์") ||
			thaiLetters(texts([]any{menu["reads"], menu["note"]})) != (l == Thai) {
			t.Fatalf("lang %q: the model menu: %v / %v", lang, menu["reads"], menu["note"])
		}
		// the storage notes and the retention line
		st := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/storage", nil, "Accept-Language", lang)
		if st["retention"] != RetentionText(5).In(l) || thaiLetters(texts([]any{st["retention"], st["not_archive"], st["personal_delete"]})) != (l == Thai) {
			t.Fatalf("lang %q: storage: %v", lang, st)
		}
		// a chat nobody named
		chats := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/threads", nil, "Accept-Language", lang)["threads"].([]any)
		if got := chats[0].(map[string]any)["title"]; got != T(l, "แชทแรก") {
			t.Fatalf("lang %q: the first chat is called %v", lang, got)
		}
		// the theme's presets
		theme := e.mustDo(200, "GET", "/theme", nil, "Accept-Language", lang)
		if got := theme["presets"].([]any)[0].(map[string]any)["name"]; got != T(l, "กรมท่า") {
			t.Fatalf("lang %q: a preset is called %v", lang, got)
		}
	}
	// the Thai of two of them, word for word
	st := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/storage", nil)
	if st["retention"] != "ไฟล์ที่ส่งเข้ามาและไฟล์ผลลัพธ์ของรอบที่สำเร็จเก็บไว้ 5 ปีตามที่องค์กรกำหนด · ข้อมูลของรอบที่ล้มเหลวหรือยกเลิกเก็บ 7 วัน · ไฟล์ที่ไม่ได้ใช้ในรอบใดเก็บ 30 วัน" {
		t.Fatalf("the retention line changed: %v", st["retention"])
	}
	menu := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/models", nil)
	if menu["note"] != "โมเดลที่เลือกไม่ได้ข้ามการตรวจใด ๆ และไม่ได้เป็นที่มาของตัวเลข — ช่วยให้อ่านสแกนที่ไม่ชัดและเขียนคำอธิบายได้ดีขึ้นเท่านั้น" {
		t.Fatalf("the model menu's note changed: %v", menu["note"])
	}
}

// What a person types in English is read as the same thing typed in Thai (words.go). An English word is
// found only as a whole word — which is why "password" does not ask for a Word file, "show" does not ask
// how, and "book" is not an "ok" — while a Thai word is found anywhere, as Thai is written.
func TestWhatAPersonTypesInEnglishIsReadAsInThai(t *testing.T) {
	secret := func(q string) bool {
		f := Finding{Key: "k", Code: "k", Severity: "blocking", Header: "h", Question: q, Default: json.RawMessage(`"a"`),
			Options: []Option{{Label: "a", Value: json.RawMessage(`"a"`)}, {Label: "b", Value: json.RawMessage(`"b"`)}}}
		return f.Validate() != nil
	}
	notOption := func(text string) bool { // the answer names the first word of an option only to refuse it
		q := Question{Options: []Option{{Label: "exclude the stray rows", Value: json.RawMessage(`"x"`)}, {Label: "นับรวมด้วย", Value: json.RawMessage(`"i"`)}}}
		_, ok := InterpretText(q, text)
		return !ok
	}
	for _, c := range []struct {
		list  string
		reads func(string) bool
		yes   []string // at least one Thai phrase and two English ones
		no    []string
	}{
		{"newRoundWords", wantsNewRound,
			[]string{"ขอเริ่มใหม่", "Start over with these files", "month-end close for September 2026", "please REDO it"},
			[]string{"what is the closing balance", "the rerun", "restarted"}},
		{"aboutRoundWords", ReadsAsAboutLastRound,
			[]string{"ทำไมกำไรลดลง", "ขอpdfหน่อย", "Why is revenue lower", "how was this calculated", "send me the slides", "report.docx please"},
			[]string{"show the next step", "whatever you think", "passwords", "use the October files"}},
		{"socialWords", SocialOnly,
			[]string{"ขอบคุณครับ", "Thank you!", "ok thanks", "okay", "Got it."},
			[]string{"book it", "thanks but redo it", "okra"}},
		{"freshStartWords", wantsFreshStart,
			[]string{"ลบแชทนี้ทิ้ง", "Delete this chat", "let us start from scratch"},
			[]string{"delete the row", "clearly"}},
		{"formWords", ReadsAsFormRequest,
			[]string{"กรอกลงแบบฟอร์มนี้", "Fill in this form", "put the figures into this template", "fill out the attached"},
			[]string{"inform me", "the format is fine", "performance"}},
		{"negations (a typed answer)", notOption,
			[]string{"ไม่exclude", "ไม่ต้อง exclude", "do not exclude them", "don't exclude", "never exclude", "no need to exclude", "without exclude"},
			[]string{"exclude them", "yes exclude", "please exclude the rows"}},
		{"what a card may never ask (Validate)", secret,
			[]string{"กรุณาใส่รหัสผ่าน", "Enter your password", "What is your PIN?", "your national ID number"},
			[]string{"Is the shipping cost included?", "Which opinion applies?"}},
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
	// the rule itself
	for text, word := range map[string]string{"ขอpdfหน่อย": "pdf", "report.PDF": "pdf", "is it OK?": "ok", "รายงาน word": "word"} {
		if !HasWord(text, word) {
			t.Errorf("%q has the word %q", text, word)
		}
	}
	for text, word := range map[string]string{"password": "word", "in other words": "word", "excellent": "excel", "show": "how", "pdf2": "pdf"} {
		if HasWord(text, word) {
			t.Errorf("%q does not have the word %q", text, word)
		}
	}
	if got := WithoutWords("OK ขอบคุณครับ, book", "ok", "ขอบคุณ", "ครับ"); got != " , book" {
		t.Errorf("words taken out: %q", got)
	}
}

// A value a sentence carries is quoted by the rule of the sentence's language: a name in Latin letters is
// in quotation marks in a Thai sentence exactly as before — the Thai reader shows it only so — and needs
// none in an English one when the English reader knows its words.
func TestANameIsQuotedByTheRuleOfTheSentencesLanguage(t *testing.T) {
	said := Text("ผู้ดูแล %s ลบไฟล์ของรอบนี้ (ไฟล์ที่ส่งเข้าและไฟล์ผลลัพธ์) ก่อนครบระยะเก็บ เหตุผล: “%s” — บันทึกว่ารอบนี้เคยทำยังอยู่",
		Name("Admin Finance"), Typed("duplicate", 500))
	if th := said.In(Thai); !strings.HasPrefix(th, "ผู้ดูแล “Admin Finance” ลบไฟล์ของรอบนี้") {
		t.Fatalf("a Thai sentence carries a Latin name in quotation marks, as before: %s", th)
	}
	if en := said.In(English); !strings.HasPrefix(en, "Admin ") || thaiLetters(en) {
		t.Fatalf("the English sentence: %s", en)
	}
	// … and each is shown whole by the reader of its own language
	for _, l := range []Lang{Thai, English} {
		item, _ := MarshalIn(l, Notice{Text: said})
		var got struct {
			Text string `json:"text"`
		}
		_ = json.Unmarshal(ForClient("notice", stampLang(l, item)), &got)
		if got.Text != said.In(l) || strings.Contains(got.Text, "…") {
			t.Fatalf("%s: written %q, shown %q", l, said.In(l), got.Text)
		}
	}
	// a package's sentence is read by the rule of the language it is written in: the Thai side is what
	// the Thai rule shows of it, as before; the English side what the English rule shows
	pkg := FromPackage("The files are below.", Text("อ่านไฟล์ที่ส่งมาไม่สำเร็จ"))
	if pkg.In(Thai) != "อ่านไฟล์ที่ส่งมาไม่สำเร็จ" || pkg.In(English) != "The files are below." {
		t.Fatalf("a package's English sentence: Thai %q, English %q", pkg.In(Thai), pkg.In(English))
	}
	if got := ShownToPerson("อ่านไฟล์ไม่สำเร็จ: the file could not be opened"); got != "อ่านไฟล์ไม่สำเร็จ: …" {
		t.Fatalf("the Thai rule shows %q", got)
	}
}
