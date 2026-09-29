package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

// AM6 section B — a reminder on an install tells a person that a round is
// waiting for their files. Nothing runs without a person: the minute pass
// opens the round in awaiting_input with a request_files card and a
// notification, and the round starts only when the person sends the files.

var ict = time.FixedZone("ICT", 7*3600)

func thaiAt(y int, m time.Month, d, hh, mm int) time.Time {
	return time.Date(y, m, d, hh, mm, 0, 0, ict)
}

const monthly5 = `{"every":"month","day":5,"time":"09:00"}`

// reminder creates a reminder through the API and returns its JSON.
func (e *testEnv) reminder(in Install, label, rule string) map[string]any {
	e.t.Helper()
	out := e.mustDo(201, "POST", "/installs/"+in.ID.String()+"/reminders", map[string]any{"label": label, "rule": json.RawMessage(rule)})
	return out["reminder"].(map[string]any)
}

// dueAt puts a reminder's next occurrence at t.
func (e *testEnv) dueAt(id string, t time.Time) {
	e.t.Helper()
	if _, err := e.store.Pool.Exec(context.Background(), `UPDATE agent_market.reminders SET next_due_at = $2 WHERE id = $1`, id, t); err != nil {
		e.t.Fatal(err)
	}
}

func (e *testEnv) pass(now time.Time) ReminderPass {
	e.t.Helper()
	p, err := e.store.RemindersPass(context.Background(), now)
	if err != nil {
		e.t.Fatalf("the minute pass: %v", err)
	}
	return p
}

func (e *testEnv) installRuns(in Install) []Run {
	e.t.Helper()
	runs, err := e.store.ListRuns(context.Background(), in.ID, 50)
	if err != nil {
		e.t.Fatal(err)
	}
	return runs
}

func (e *testEnv) countRows(sql string, args ...any) int {
	e.t.Helper()
	var n int
	if err := e.store.Pool.QueryRow(context.Background(), sql, args...).Scan(&n); err != nil {
		e.t.Fatal(err)
	}
	return n
}

func (e *testEnv) notifications(surface string, hdr ...string) []map[string]any {
	e.t.Helper()
	out := e.mustDo(200, "GET", "/notifications?surface="+surface, nil, hdr...)
	list, _ := out["notifications"].([]any)
	var ns []map[string]any
	for _, n := range list {
		ns = append(ns, n.(map[string]any))
	}
	return ns
}

func (e *testEnv) getReminder(in Install, id string) map[string]any {
	e.t.Helper()
	out := e.mustDo(200, "GET", "/installs/"+in.ID.String()+"/reminders", nil)
	for _, r := range out["reminders"].([]any) {
		if m := r.(map[string]any); m["id"] == id {
			return m
		}
	}
	e.t.Fatalf("reminder %s not listed: %v", id, out)
	return nil
}

func TestAReminderRuleIsReadStrictlyAndSaidInThai(t *testing.T) {
	for _, s := range []string{monthly5, `{"every":"week","weekday":1,"time":"17:30"}`, `{"every":"month","day":28,"time":"00:00"}`,
		`{"every":"month","day":31,"time":"09:00"}`, `{"every":"week","weekday":7,"time":"23:59"}`} {
		if _, err := ParseReminderRule(json.RawMessage(s)); err != nil {
			t.Errorf("a rule the contract allows was refused: %s: %v", s, err)
		}
	}
	for _, s := range []string{`{"every":"month","day":32,"time":"09:00"}`, `{"every":"month","day":0,"time":"09:00"}`,
		`{"every":"week","weekday":8,"time":"09:00"}`, `{"every":"week","weekday":0,"time":"09:00"}`, `{"every":"day","time":"09:00"}`,
		`{"every":"month","day":5,"time":"9:00"}`, `{"every":"month","day":5,"time":"24:00"}`, `{"every":"month","day":5,"time":"09:60"}`,
		`{"every":"month","day":5}`, `{"every":"month","day":5,"weekday":1,"time":"09:00"}`, `{"every":"week","weekday":1,"day":5,"time":"09:00"}`,
		`{"every":"month","day":5,"time":"09:00","cron":"* * * * *"}`, `"monthly"`, `null`, ``} {
		if r, err := ParseReminderRule(json.RawMessage(s)); err == nil {
			t.Errorf("a rule outside the contract was taken: %s → %+v", s, r)
		}
	}
	m, _ := ParseReminderRule(json.RawMessage(monthly5))
	w, _ := ParseReminderRule(json.RawMessage(`{"every":"week","weekday":1,"time":"17:30"}`))
	last, _ := ParseReminderRule(json.RawMessage(`{"every":"month","day":31,"time":"09:00"}`))
	for got, want := range map[string]string{
		m.Text().String():    "ทุกวันที่ 5 ของเดือน เวลา 09:00 น. · สำหรับงานของเดือนก่อนหน้า",
		w.Text().String():    "ทุกวันจันทร์ เวลา 17:30 น. · สำหรับงาน 7 วันก่อนหน้า",
		last.Text().String(): "ทุกวันที่ 31 ของเดือน เวลา 09:00 น. (เดือนที่สั้นกว่าใช้วันสุดท้ายของเดือน) · สำหรับงานของเดือนก่อนหน้า",
	} {
		if got != want {
			t.Errorf("rule_text: %q, want %q", got, want)
		}
		if !ReadsForPerson(got) {
			t.Errorf("rule_text does not read as written for a person: %q", got)
		}
	}
}

func TestAReminderFallsDueAtItsNextOccurrenceForThePeriodBefore(t *testing.T) {
	m := ReminderRule{Every: "month", Day: 5, Time: "09:00"}
	w := ReminderRule{Every: "week", Weekday: 1, Time: "09:00"}
	sun := ReminderRule{Every: "week", Weekday: 7, Time: "09:00"}
	d31 := ReminderRule{Every: "month", Day: 31, Time: "09:00"}
	for _, c := range []struct {
		rule          ReminderRule
		after, due    time.Time
		period, label string
	}{
		{m, thaiAt(2026, 9, 29, 10, 0), thaiAt(2026, 10, 5, 9, 0), "2026-09", "ก.ย. 2569"},
		{m, thaiAt(2026, 10, 5, 8, 59), thaiAt(2026, 10, 5, 9, 0), "2026-09", "ก.ย. 2569"},
		{m, thaiAt(2026, 10, 5, 9, 0), thaiAt(2026, 11, 5, 9, 0), "2026-10", "ต.ค. 2569"},
		{m, thaiAt(2026, 12, 10, 0, 0), thaiAt(2027, 1, 5, 9, 0), "2026-12", "ธ.ค. 2569"},
		// a time in UTC is the same moment in Thai time: 01:30 UTC is 08:30 in Bangkok
		{m, time.Date(2026, 10, 5, 1, 30, 0, 0, time.UTC), thaiAt(2026, 10, 5, 9, 0), "2026-09", "ก.ย. 2569"},
		// 2026-09-29 is a Tuesday: the next Monday is 5 October, for the seven days before it
		{w, thaiAt(2026, 9, 29, 10, 0), thaiAt(2026, 10, 5, 9, 0), "2026-09-28", "28 ก.ย. – 4 ต.ค. 2569"},
		{w, thaiAt(2026, 10, 5, 8, 0), thaiAt(2026, 10, 5, 9, 0), "2026-09-28", "28 ก.ย. – 4 ต.ค. 2569"},
		{w, thaiAt(2026, 10, 6, 8, 0), thaiAt(2026, 10, 12, 9, 0), "2026-10-05", "5–11 ต.ค. 2569"},
		{w, thaiAt(2026, 12, 29, 8, 0), thaiAt(2027, 1, 4, 9, 0), "2026-12-28", "28 ธ.ค. 2569 – 3 ม.ค. 2570"},
		{sun, thaiAt(2026, 9, 29, 10, 0), thaiAt(2026, 10, 4, 9, 0), "2026-09-27", "27 ก.ย. – 3 ต.ค. 2569"},
		// day 31: a shorter month falls due on its last day — 30 September, 28 February, 29 in a leap year
		{d31, thaiAt(2026, 9, 1, 0, 0), thaiAt(2026, 9, 30, 9, 0), "2026-08", "ส.ค. 2569"},
		{d31, thaiAt(2026, 9, 30, 9, 0), thaiAt(2026, 10, 31, 9, 0), "2026-09", "ก.ย. 2569"},
		{d31, thaiAt(2027, 2, 1, 0, 0), thaiAt(2027, 2, 28, 9, 0), "2027-01", "ม.ค. 2570"},
		{d31, thaiAt(2027, 1, 31, 9, 30), thaiAt(2027, 2, 28, 9, 0), "2027-01", "ม.ค. 2570"},
		{d31, thaiAt(2028, 2, 10, 0, 0), thaiAt(2028, 2, 29, 9, 0), "2028-01", "ม.ค. 2571"},
		{ReminderRule{Every: "month", Day: 30, Time: "09:00"}, thaiAt(2027, 2, 28, 9, 0), thaiAt(2027, 3, 30, 9, 0), "2027-02", "ก.พ. 2570"},
	} {
		got := c.rule.NextDue(c.after)
		if !got.Equal(c.due) {
			t.Errorf("%+v after %s: due %s, want %s", c.rule, c.after, got.In(ict), c.due)
			continue
		}
		if p := c.rule.PeriodOf(got); p != c.period {
			t.Errorf("%+v due %s: period %q, want %q", c.rule, c.due, p, c.period)
		}
		if l := ReminderPeriodLabel(c.period); l != c.label {
			t.Errorf("period %s: label %q, want %q", c.period, l, c.label)
		}
	}
}

func TestAReminderIsKeptOnTheInstallAndOnlyItsOwnerTouchesIt(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	path := "/installs/" + in.ID.String() + "/reminders"
	for _, bad := range []any{map[string]any{"label": "ปิดงบ", "rule": map[string]any{"every": "month", "day": 32, "time": "09:00"}},
		map[string]any{"label": "ปิดงบ", "rule": map[string]any{"every": "hour"}}, map[string]any{"label": "ปิดงบ"}} {
		code, body := e.do("POST", path, bad)
		c, msg := errMsg(body)
		if code != 400 || c != "BAD_RULE" || !ReadsForPerson(msg) || !strings.Contains(msg, "—") {
			t.Fatalf("a rule outside the contract: %d %s %q", code, c, msg)
		}
	}
	code, body := e.do("POST", path, map[string]any{"label": "   ", "rule": json.RawMessage(monthly5)})
	if c, msg := errMsg(body); code != 400 || c != "BAD_LABEL" || !ReadsForPerson(msg) {
		t.Fatalf("a reminder without a name: %d %s %q", code, c, msg)
	}
	before := time.Now()
	r := e.reminder(in, "ปิดงบ", monthly5)
	due, err := time.Parse(time.RFC3339Nano, r["next_due_at"].(string))
	if err != nil {
		t.Fatal(err)
	}
	rule := ReminderRule{Every: "month", Day: 5, Time: "09:00"}
	if want := rule.NextDue(before); !due.Equal(want) {
		t.Fatalf("next_due_at %s, want %s", due, want)
	}
	if r["label"] != "ปิดงบ" || r["status"] != "active" || r["install_id"] != in.ID.String() ||
		r["rule_text"] != "ทุกวันที่ 5 ของเดือน เวลา 09:00 น. · สำหรับงานของเดือนก่อนหน้า" ||
		r["next_due_text"] != ThaiDateTime(due) || r["snoozed_until"] != nil || r["skipped_period"] != nil || r["waiting_run_id"] != nil ||
		r["period_label"] != ReminderPeriodLabel(rule.PeriodOf(due)) {
		t.Fatalf("the reminder: %v", r)
	}
	if rr, _ := r["rule"].(map[string]any); rr["every"] != "month" || rr["day"] != float64(5) || rr["time"] != "09:00" {
		t.Fatalf("its rule as it was set: %v", r["rule"])
	}
	list := e.mustDo(200, "GET", path, nil)
	if n := len(list["reminders"].([]any)); n != 1 {
		t.Fatalf("listed: %d", n)
	}
	reaches, _ := json.Marshal(list["reaches"])
	if string(reaches) != `{"browser":true,"desktop":true,"email":false,"in_app":true}` {
		t.Fatalf("reaches: %s", reaches)
	}
	if list["note"] != "ไม่มีอะไรทำงานเองโดยไม่มีคน: การเตือนเปิดรอบที่รอไฟล์ไว้ให้ และรอบจะเริ่มเมื่อคุณส่งไฟล์" {
		t.Fatalf("note: %v", list["note"])
	}
	id := r["id"].(string)
	// a new name and a weekly rule
	r = e.mustDo(200, "PUT", "/reminders/"+id, map[string]any{"label": "ปิดงวดรายสัปดาห์",
		"rule": map[string]any{"every": "week", "weekday": 1, "time": "08:30"}})["reminder"].(map[string]any)
	if r["label"] != "ปิดงวดรายสัปดาห์" || r["rule_text"] != "ทุกวันจันทร์ เวลา 08:30 น. · สำหรับงาน 7 วันก่อนหน้า" {
		t.Fatalf("changed: %v", r)
	}
	if code, body := e.do("PUT", "/reminders/"+id, map[string]any{"rule": map[string]any{"every": "week", "weekday": 9, "time": "08:30"}}); code != 400 {
		t.Fatalf("a bad rule on a change: %d %v", code, body)
	}
	if code, body := e.do("POST", "/reminders/"+id+"/snooze", map[string]any{"days": 2}); code != 400 {
		t.Fatalf("a snooze of 2 days: %d %v", code, body)
	} else if c, msg := errMsg(body); c != "BAD_SNOOZE" || !ReadsForPerson(msg) {
		t.Fatalf("a snooze of 2 days: %s %q", c, msg)
	}
	// somebody else: every route says there is no such thing
	other := []string{"X-Test-User", uuid.NewString()}
	for _, c := range []struct{ method, path string }{{"GET", path}, {"POST", path}, {"PUT", "/reminders/" + id},
		{"POST", "/reminders/" + id + "/snooze"}, {"POST", "/reminders/" + id + "/skip"}, {"POST", "/reminders/" + id + "/stop"},
		{"POST", "/reminders/" + id + "/resume"}, {"DELETE", "/reminders/" + id}} {
		if code, body := e.do(c.method, c.path, map[string]any{"label": "x", "rule": json.RawMessage(monthly5), "days": 1}, other...); code != 404 {
			t.Fatalf("%s %s by somebody else: %d %v", c.method, c.path, code, body)
		}
	}
	// stop, resume, delete
	r = e.mustDo(200, "POST", "/reminders/"+id+"/stop", nil)["reminder"].(map[string]any)
	if r["status"] != "stopped" {
		t.Fatalf("stopped: %v", r)
	}
	now := time.Now()
	r = e.mustDo(200, "POST", "/reminders/"+id+"/resume", nil)["reminder"].(map[string]any)
	due, _ = time.Parse(time.RFC3339Nano, r["next_due_at"].(string))
	wr := ReminderRule{Every: "week", Weekday: 1, Time: "08:30"}
	if r["status"] != "active" || !(due.Equal(wr.NextDue(now)) || due.Equal(wr.NextDue(time.Now()))) {
		t.Fatalf("resumed from now: %v", r)
	}
	if code, _ := e.do("DELETE", "/reminders/"+id, nil); code != http.StatusNoContent {
		t.Fatalf("delete: %d", code)
	}
	if n := len(e.mustDo(200, "GET", path, nil)["reminders"].([]any)); n != 0 {
		t.Fatalf("deleted and still listed: %d", n)
	}
}

func TestADueReminderOpensOneRoundThatWaitsForFilesAndTellsItsOwner(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	r := e.reminder(in, "ปิดงบ", monthly5)
	id := r["id"].(string)
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	now := thaiAt(2026, 10, 5, 9, 1)
	if p := e.pass(now); p.Fired != 1 {
		t.Fatalf("the pass: %+v", p)
	}
	runs := e.installRuns(in)
	if len(runs) != 1 {
		t.Fatalf("one round: %d", len(runs))
	}
	run := runs[0]
	// its period is the package's to read from the files; the reminder guesses none
	if run.Status != RunAwaitingInput || run.Kind != KindRun || run.UserID != in.UserID || run.Period != nil {
		t.Fatalf("the round waits for files: %+v", run)
	}
	// nothing ran: no step, no engine, no model, no charge
	if steps, _ := e.store.ListSteps(ctx, run.ID); len(steps) != 0 {
		t.Fatalf("a reminder's round did work: %d steps", len(steps))
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_budget_ledger WHERE install_id = $1`, in.ID); n != 0 || run.CreditsSpent != 0 || run.CreditsCost != 0 {
		t.Fatalf("a reminder charged: %d rows, %v, %v", n, run.CreditsSpent, run.CreditsCost)
	}
	cards, _ := e.store.RunCards(ctx, run.ID)
	if len(cards) != 1 || cards[0].Kind != "request_files" || !cards[0].Blocking || cards[0].Status != "open" {
		t.Fatalf("a request_files card: %+v", cards)
	}
	var qs []Question
	_ = json.Unmarshal(cards[0].Questions, &qs)
	var req []map[string]any
	if len(qs) == 1 {
		_ = json.Unmarshal(qs[0].Request, &req)
	}
	if len(qs) != 1 || qs[0].Code != "missing_file" || len(req) != 1 || req[0]["label"] != "GL" || req[0]["required"] != true || req[0]["role"] != "primary" {
		t.Fatalf("the card lists the agent's inputs: %+v %v", qs, req)
	}
	// the thread says why the round is there; everything in it reads as written for a person
	kinds := map[string]int{}
	for _, it := range e.threadItems(in, "") {
		kinds[it["kind"].(string)]++
		p, _ := it["payload"].(map[string]any)
		switch it["kind"] {
		case "agent_text":
			s, _ := p["text"].(string)
			t.Logf("the thread says: %s", s)
			if !strings.Contains(s, "ปิดงบ") || !strings.Contains(s, "ก.ย. 2569") || !ReadsForPerson(s) {
				t.Fatalf("why the round is there: %q", s)
			}
		case "progress":
			if p["status"] != RunAwaitingInput || p["run_id"] != run.ID.String() {
				t.Fatalf("the progress card: %v", p)
			}
		case "card":
			q := p["questions"].([]any)[0].(map[string]any)
			if s, _ := q["question"].(string); !ReadsForPerson(s) || !strings.Contains(s, "ก.ย. 2569") {
				t.Fatalf("the card's question: %q", s)
			}
		}
	}
	if kinds["agent_text"] != 1 || kinds["progress"] != 1 || kinds["card"] != 1 {
		t.Fatalf("thread: %v", kinds)
	}
	// one notification, with the contract's link
	ns := e.notifications("web")
	if len(ns) != 1 {
		t.Fatalf("one notification: %v", ns)
	}
	n := ns[0]
	link := "/chat/agents/i/" + in.ID.String() + "?thread=main&run=" + run.ID.String()
	if n["kind"] != "reminder_due" || n["title"] != "ถึงเวลาปิดงบ ก.ย. 2569 แล้ว" || n["body"] != "ต้องใช้ 1 ไฟล์นี้: GL" ||
		n["link"] != link || n["install_id"] != in.ID.String() || n["run_id"] != run.ID.String() || n["reminder_id"] != id ||
		n["agent_name"] != in.Title || n["created_at"] == nil {
		t.Fatalf("the notification: %v", n)
	}
	t.Logf("the notification: %s · %s · %s", n["title"], n["body"], n["link"])
	for _, s := range []string{n["title"].(string), n["body"].(string)} {
		if !ReadsForPerson(s) {
			t.Fatalf("a notification that does not read as written for a person: %q", s)
		}
	}
	got := e.getReminder(in, id)
	if got["waiting_run_id"] != run.ID.String() || got["period_label"] != "ก.ย. 2569" || !sameInstant(got["next_due_at"], thaiAt(2026, 11, 5, 9, 0)) {
		t.Fatalf("the reminder after it fired: %v", got)
	}
	// a second pass does nothing, and neither does a restart that finds the same occurrence due again
	if p := e.pass(now.Add(time.Minute)); p != (ReminderPass{}) {
		t.Fatalf("a second pass: %+v", p)
	}
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	if p := e.pass(now.Add(2 * time.Minute)); p.Fired != 0 || p.Cancelled != 0 {
		t.Fatalf("the same occurrence again: %+v", p)
	}
	if n := len(e.installRuns(in)); n != 1 {
		t.Fatalf("rounds after the second pass: %d", n)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.notifications WHERE install_id = $1`, in.ID); n != 1 {
		t.Fatalf("notifications after the second pass: %d", n)
	}
	if cur, _ := e.store.RunByID(ctx, run.ID); cur.Status != RunAwaitingInput {
		t.Fatalf("the waiting round was touched: %s", cur.Status)
	}
}

// firedPeriod is the period a reminder last opened a round for.
func (e *testEnv) firedPeriod(id string) string {
	e.t.Helper()
	var p *string
	if err := e.store.Pool.QueryRow(context.Background(), `SELECT fired_period FROM agent_market.reminders WHERE id = $1`, id).Scan(&p); err != nil {
		e.t.Fatal(err)
	}
	return strOr(p)
}

func sameInstant(v any, t time.Time) bool {
	s, _ := v.(string)
	got, err := time.Parse(time.RFC3339Nano, s)
	return err == nil && got.Equal(t)
}

func TestTwoRunnersFireAReminderOnce(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	id := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	var wg sync.WaitGroup
	var mu sync.Mutex
	total := ReminderPass{}
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			p, err := e.store.RemindersPass(context.Background(), thaiAt(2026, 10, 5, 9, 1))
			if err != nil {
				t.Error(err)
			}
			mu.Lock()
			total.Fired += p.Fired
			mu.Unlock()
		}()
	}
	wg.Wait()
	if total.Fired != 1 || len(e.installRuns(in)) != 1 ||
		e.countRows(`SELECT count(*) FROM agent_market.notifications WHERE install_id = $1`, in.ID) != 1 {
		t.Fatalf("four passes at once: fired %d, rounds %d", total.Fired, len(e.installRuns(in)))
	}
}

func TestFilesSentOnTheCardStartTheRoundAndNothingElseDoes(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	id := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	e.pass(thaiAt(2026, 10, 5, 9, 1))
	run := e.installRuns(in)[0]
	// nothing but a person's files moves it
	e.pass(thaiAt(2026, 10, 6, 9, 1))
	if cur, _ := e.store.RunByID(ctx, run.ID); cur.Status != RunAwaitingInput {
		t.Fatalf("the round moved without a person: %s", cur.Status)
	}
	// the web's request_files card sends the files as a message (no thread named: the first thread)
	f := e.stagedFile(in, "GL.xlsx")
	out := e.mustDo(200, "POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "", "file_ids": []string{f.ID.String()},
		"client_id": "rem-files-1"})
	if out["route"] != "resumed" || out["run_id"] != run.ID.String() {
		t.Fatalf("the files resume the reminder's round: %v", out)
	}
	if cur, _ := e.store.RunByID(ctx, run.ID); cur.Status != RunQueued {
		t.Fatalf("queued for the runner: %s", cur.Status)
	}
	// the round has its files: no follow-up, and its notification is no longer news
	if p := e.pass(thaiAt(2026, 10, 9, 9, 2)); p.FollowUps != 0 {
		t.Fatalf("a follow-up for a round that got its files: %+v", p)
	}
	if ns := e.notifications("desktop"); len(ns) != 0 {
		t.Fatalf("a notification about a round that is no longer waiting: %v", ns)
	}
}

func TestTheNextFiringClosesARoundThatGotNoFileAndChargesNothing(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	id := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	e.pass(thaiAt(2026, 10, 5, 9, 1))
	first := e.installRuns(in)[0]
	// whatever cost a pass of it might have had is not charged when it closes
	if err := e.store.Charge(ctx, in.ID, first.ID, "k-probe", 5, "", nil); err != nil {
		t.Fatal(err)
	}
	p := e.pass(thaiAt(2026, 11, 5, 9, 1))
	if p.Fired != 1 || p.Cancelled != 1 {
		t.Fatalf("the next firing: %+v", p)
	}
	old, _ := e.store.RunByID(ctx, first.ID)
	if old.Status != RunCancelled || old.StatusReason != "reminder_next_period" || old.CreditsSpent != 0 || old.Delivered() {
		t.Fatalf("the round that got no file: %+v", old)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.agent_budget_ledger WHERE run_id = $1 AND voided_at IS NULL`, first.ID); n != 0 {
		t.Fatalf("a charge left on a round that delivered nothing: %d", n)
	}
	if spent, _ := e.store.SpentThisMonth(ctx, in.ID); spent != 0 {
		t.Fatalf("the install's month: %v", spent)
	}
	cards, _ := e.store.RunCards(ctx, first.ID)
	if len(cards) != 1 || cards[0].Status != "cancelled" {
		t.Fatalf("its card: %+v", cards)
	}
	said := false
	for _, it := range e.threadItems(in, "") {
		p, _ := it["payload"].(map[string]any)
		switch {
		case it["kind"] == "notice" && p["run_id"] == first.ID.String():
			s, _ := p["text"].(string)
			said = strings.Contains(s, "มีรอบใหม่ของงวดถัดไปแล้ว") && ReadsForPerson(s)
		case it["kind"] == "progress" && p["run_id"] == first.ID.String():
			if p["status"] != RunCancelled {
				t.Fatalf("its progress card: %v", p)
			}
		}
	}
	if !said {
		t.Fatal("the thread does not say why the round was closed")
	}
	runs := e.installRuns(in)
	if len(runs) != 2 || runs[0].Status != RunAwaitingInput || e.firedPeriod(id) != "2026-10" {
		t.Fatalf("the new round: %+v", runs)
	}
	ns := e.notifications("web")
	if len(ns) != 1 || ns[0]["run_id"] != runs[0].ID.String() || ns[0]["title"] != "ถึงเวลาปิดงบ ต.ค. 2569 แล้ว" {
		t.Fatalf("only the new round's notification is news: %v", ns)
	}
}

func TestOneFollowUpThenSilence(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	id := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	fired := thaiAt(2026, 10, 5, 9, 1)
	e.pass(fired)
	if p := e.pass(fired.Add(72*time.Hour - time.Minute)); p.FollowUps != 0 {
		t.Fatalf("a follow-up before three days: %+v", p)
	}
	if p := e.pass(fired.Add(72*time.Hour + time.Minute)); p.FollowUps != 1 {
		t.Fatalf("the follow-up: %+v", p)
	}
	for _, d := range []time.Duration{73 * time.Hour, 5 * 24 * time.Hour, 20 * 24 * time.Hour} {
		if p := e.pass(fired.Add(d)); p.FollowUps != 0 {
			t.Fatalf("a second follow-up after %s: %+v", d, p)
		}
	}
	var follow map[string]any
	for _, n := range e.notifications("web") {
		if n["kind"] == "reminder_followup" {
			follow = n
		}
	}
	if follow == nil || follow["title"] != "ยังรอไฟล์สำหรับปิดงบ ก.ย. 2569" || follow["body"] != "ต้องใช้ 1 ไฟล์นี้: GL" ||
		!ReadsForPerson(follow["title"].(string)) {
		t.Fatalf("the follow-up: %v", follow)
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.notifications WHERE reminder_id = $1 AND kind = 'reminder_followup'`, id); n != 1 {
		t.Fatalf("follow-ups: %d", n)
	}
}

func TestSnoozeMovesTheFiringOrTheOneFollowUp(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	rule := ReminderRule{Every: "month", Day: 5, Time: "09:00"}
	// not fired yet: the firing moves
	id := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	now := time.Now()
	scheduled := now.Add(-time.Minute)
	e.dueAt(id, scheduled)
	r := e.mustDo(200, "POST", "/reminders/"+id+"/snooze", map[string]any{"days": 1})["reminder"].(map[string]any)
	until, _ := time.Parse(time.RFC3339Nano, fmtOf(r["snoozed_until"]))
	if until.Before(now.Add(24*time.Hour-time.Minute)) || until.After(time.Now().Add(24*time.Hour+time.Minute)) || !sameInstant(r["next_due_at"], until) {
		t.Fatalf("snoozed a day: %v", r)
	}
	if p := e.pass(now); p.Fired != 0 {
		t.Fatalf("fired while snoozed: %+v", p)
	}
	if p := e.pass(until.Add(time.Minute)); p.Fired != 1 {
		t.Fatalf("fired after the snooze: %+v", p)
	}
	run := e.installRuns(in)[0]
	if got := e.firedPeriod(id); got != rule.PeriodOf(scheduled) {
		t.Fatalf("a snoozed firing is for the period it was scheduled for: %v", got)
	}
	// fired and waiting: the one follow-up moves to the end of the snooze (the
	// round fired now, and the rule's next occurrence is far away)
	if _, err := e.store.Pool.Exec(context.Background(), `UPDATE agent_market.reminders SET fired_at = $2, next_due_at = $3 WHERE id = $1`,
		id, time.Now(), time.Now().Add(60*24*time.Hour)); err != nil {
		t.Fatal(err)
	}
	now = time.Now()
	r = e.mustDo(200, "POST", "/reminders/"+id+"/snooze", map[string]any{"days": 7})["reminder"].(map[string]any)
	until, _ = time.Parse(time.RFC3339Nano, fmtOf(r["snoozed_until"]))
	if until.Before(now.Add(7*24*time.Hour-time.Minute)) || r["waiting_run_id"] != run.ID.String() {
		t.Fatalf("snoozed a week while waiting: %v", r)
	}
	if p := e.pass(now.Add(72*time.Hour + time.Minute)); p.FollowUps != 0 {
		t.Fatalf("the follow-up came at three days despite the snooze: %+v", p)
	}
	if p := e.pass(until.Add(time.Minute)); p.FollowUps != 1 {
		t.Fatalf("the follow-up after the snooze: %+v", p)
	}
}

func fmtOf(v any) string { s, _ := v.(string); return s }

func TestSkipLeavesAPeriodOutOrClosesItsWaitingRound(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	id := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	// not fired: the next firing's period is left out
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	r := e.mustDo(200, "POST", "/reminders/"+id+"/skip", nil)["reminder"].(map[string]any)
	if r["skipped_period"] != "2026-09" {
		t.Fatalf("skipped: %v", r)
	}
	if p := e.pass(thaiAt(2026, 10, 5, 9, 1)); p.Skipped != 1 || p.Fired != 0 {
		t.Fatalf("a skipped period: %+v", p)
	}
	if len(e.installRuns(in)) != 0 || len(e.notifications("web")) != 0 {
		t.Fatal("a skipped period opened a round or said something")
	}
	if got := e.getReminder(in, id); !sameInstant(got["next_due_at"], thaiAt(2026, 11, 5, 9, 0)) {
		t.Fatalf("advanced past the skipped period: %v", got)
	}
	if p := e.pass(thaiAt(2026, 11, 5, 9, 1)); p.Fired != 1 {
		t.Fatalf("the period after: %+v", p)
	}
	// fired and waiting without files: skipping closes that round, charged nothing, and its notification goes
	run := e.installRuns(in)[0]
	r = e.mustDo(200, "POST", "/reminders/"+id+"/skip", nil)["reminder"].(map[string]any)
	if r["skipped_period"] != "2026-10" || r["waiting_run_id"] != nil {
		t.Fatalf("skipped the waiting period: %v", r)
	}
	cur, _ := e.store.RunByID(ctx, run.ID)
	if cur.Status != RunCancelled || cur.StatusReason != "reminder_skipped" || cur.CreditsSpent != 0 {
		t.Fatalf("the waiting round: %+v", cur)
	}
	if ns := e.notifications("web"); len(ns) != 0 {
		t.Fatalf("its notifications: %v", ns)
	}
}

func TestAStoppedReminderNeitherFiresNorFollowsUpAndDeletingKeepsItsRounds(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	id := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	e.pass(thaiAt(2026, 10, 5, 9, 1))
	run := e.installRuns(in)[0]
	e.mustDo(200, "POST", "/reminders/"+id+"/stop", nil)
	if p := e.pass(thaiAt(2026, 10, 9, 9, 1)); p.FollowUps != 0 {
		t.Fatalf("a follow-up after stop: %+v", p)
	}
	e.dueAt(id, thaiAt(2026, 11, 5, 9, 0))
	if p := e.pass(thaiAt(2026, 11, 5, 9, 1)); p.Fired != 0 {
		t.Fatalf("a firing after stop: %+v", p)
	}
	if cur, _ := e.store.RunByID(ctx, run.ID); cur.Status != RunAwaitingInput {
		t.Fatalf("stop leaves the waiting round as it is: %s", cur.Status)
	}
	if code, _ := e.do("DELETE", "/reminders/"+id, nil); code != http.StatusNoContent {
		t.Fatalf("delete: %d", code)
	}
	if runs := e.installRuns(in); len(runs) != 1 || runs[0].ID != run.ID {
		t.Fatalf("delete keeps the rounds: %+v", runs)
	}
	// a removed install's reminder does nothing
	id2 := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	e.mustDo(200, "DELETE", "/installs/"+in.ID.String(), nil)
	e.dueAt(id2, thaiAt(2026, 10, 5, 9, 0))
	if p := e.pass(thaiAt(2026, 10, 5, 9, 1)); p.Fired != 0 {
		t.Fatalf("a removed install's reminder fired: %+v", p)
	}
}

func TestMigration005DownDropsTheReminderAndMailTables(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	tables := func() int {
		return e.countRows(`SELECT count(*) FROM information_schema.tables WHERE table_schema = 'agent_market'
			AND table_name IN ('reminders', 'notifications', 'email_drafts', 'email_sends')`)
	}
	if tables() != 4 {
		t.Fatalf("after up: %d tables", tables())
	}
	in := e.install()
	run, _ := e.succeededRun(in)
	e.mustDo(200, "POST", "/runs/"+run.ID.String()+"/email-draft", nil)
	down, err := migrationFS.ReadFile("migrations/005_am6.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatalf("down: %v", err)
	}
	if tables() != 0 || e.countRows(`SELECT count(*) FROM agent_market.agent_thread_items WHERE kind = 'email_draft'`) != 0 {
		t.Fatalf("down leaves %d tables", tables())
	}
	if did, err := Migrate(ctx, e.store.Pool); err != nil || len(did) != 1 {
		t.Fatalf("up again: %v %v", did, err)
	}
	if tables() != 4 {
		t.Fatalf("after up again: %d tables", tables())
	}
}

// A reminder of an agent the organisation disabled opens nothing and tells
// nobody: the agent is off, and a round would only wait for an answer it
// may not have. The reminder moves on; once the agent is back, it fires again.
func TestAReminderOfADisabledAgentOpensNothing(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	id := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	if _, err := e.store.SetCatalogState(ctx, in.OrgID, in.Slug, StatusDisabled, "ปิดปรับปรุง", "admin@company.example"); err != nil {
		t.Fatal(err)
	}
	e.dueAt(id, thaiAt(2026, 10, 5, 9, 0))
	if p := e.pass(thaiAt(2026, 10, 5, 9, 1)); p.Fired != 0 {
		t.Fatalf("a disabled agent's reminder fired: %+v", p)
	}
	if runs := e.installRuns(in); len(runs) != 0 {
		t.Fatalf("a round of a disabled agent: %d", len(runs))
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.notifications WHERE install_id = $1`, in.ID); n != 0 {
		t.Fatalf("a notification about a disabled agent: %d", n)
	}
	if _, err := e.store.SetCatalogState(ctx, in.OrgID, in.Slug, StatusPublished, "", "admin@company.example"); err != nil {
		t.Fatal(err)
	}
	e.dueAt(id, thaiAt(2026, 11, 5, 9, 0))
	if p := e.pass(thaiAt(2026, 11, 5, 9, 1)); p.Fired != 1 {
		t.Fatalf("back on, it fires: %+v", p)
	}
}
