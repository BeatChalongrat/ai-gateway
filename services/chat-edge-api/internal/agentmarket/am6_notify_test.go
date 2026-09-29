package agentmarket

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// AM6 section B — notifications: shown once per surface, only to their owner,
// and a round parked on a question tells its owner once, then once more.

func TestNotificationsAreShownOncePerSurfaceAndOnlyToTheirOwner(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	first := e.reminder(in, "ปิดงบ", monthly5)["id"].(string)
	second := e.reminder(in, "ส่งภาษี", `{"every":"week","weekday":1,"time":"09:00"}`)["id"].(string)
	e.dueAt(first, thaiAt(2026, 10, 5, 9, 0))
	e.pass(thaiAt(2026, 10, 5, 9, 1))
	e.mustDo(200, "POST", "/reminders/"+first+"/stop", nil) // no follow-up in between
	// the second reminder fires later, in a thread of its own (the first thread has a round waiting)
	e.dueAt(second, thaiAt(2026, 10, 12, 9, 0))
	e.pass(thaiAt(2026, 10, 12, 9, 1))
	web := e.notifications("web")
	if len(web) != 2 || web[0]["reminder_id"] != second || web[1]["reminder_id"] != first {
		t.Fatalf("newest first: %v", web)
	}
	if !strings.Contains(web[0]["link"].(string), "?thread=") || strings.Contains(web[0]["link"].(string), "thread=main") {
		t.Fatalf("a round in a thread of its own is linked to that thread: %v", web[0]["link"])
	}
	id := web[1]["id"].(string)
	if code, body := e.do("GET", "/notifications?surface=phone", nil); code != 400 {
		t.Fatalf("an unknown surface: %d %v", code, body)
	}
	other := []string{"X-Test-User", uuid.NewString()}
	if ns := e.notifications("web", other...); len(ns) != 0 {
		t.Fatalf("somebody else's list: %v", ns)
	}
	for _, p := range []string{"/notifications/" + id + "/shown", "/notifications/" + id + "/dismiss"} {
		if code, _ := e.do("POST", p, map[string]any{"surface": "web"}, other...); code != 404 {
			t.Fatalf("%s by somebody else: %d", p, code)
		}
	}
	if code, _ := e.do("POST", "/notifications/"+id+"/shown", map[string]any{"surface": "tv"}); code != 400 {
		t.Fatalf("shown on an unknown surface: %d", code)
	}
	if code, _ := e.do("POST", "/notifications/"+id+"/shown", map[string]any{"surface": "web"}); code != 204 {
		t.Fatalf("shown: %d", code)
	}
	if ns := e.notifications("web"); len(ns) != 1 || ns[0]["id"] == id {
		t.Fatalf("shown on the web, not again on the web: %v", ns)
	}
	if ns := e.notifications("desktop"); len(ns) != 2 {
		t.Fatalf("still news on the desktop: %v", ns)
	}
	if code, _ := e.do("POST", "/notifications/"+id+"/dismiss", nil); code != 204 {
		t.Fatalf("dismiss: %d", code)
	}
	if ns := e.notifications("desktop"); len(ns) != 1 || ns[0]["id"] == id {
		t.Fatalf("dismissed everywhere: %v", ns)
	}
	// at most 20, newest first
	ctx := context.Background()
	for i := 0; i < 25; i++ {
		if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.notifications (tenant_id, user_id, kind, title, body, link, install_id,
			dedupe_key, created_at) VALUES ($1, $2, 'waiting', 'รายงานการเงิน รอคำตอบจากคุณ', '', '/chat/agents/i/x', $3, $4, now() - make_interval(mins => $5))`,
			in.TenantID, in.UserID, in.ID, uuid.NewString(), i+1); err != nil {
			t.Fatal(err)
		}
	}
	if ns := e.notifications("desktop"); len(ns) != 20 {
		t.Fatalf("at most 20: %d", len(ns))
	}
}

// parkedOnAQuestion is a round waiting on a question card posted ago.
func (e *testEnv) parkedOnAQuestion(in Install, ago time.Duration) (Run, Card) {
	e.t.Helper()
	ctx := context.Background()
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	card := e.openCard(run, true)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input', awaiting_since = now() - make_interval(secs => $2)
		WHERE id = $1`, run.ID, int(ago.Seconds())); err != nil {
		e.t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_run_clarifications SET created_at = now() - make_interval(secs => $2) WHERE id = $1`,
		card.ID, int(ago.Seconds())); err != nil {
		e.t.Fatal(err)
	}
	run, _ = e.store.RunByID(ctx, run.ID)
	return run, card
}

func TestARoundParkedOnAQuestionTellsItsOwnerOnceAndFollowsUpOnce(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run, _ := e.parkedOnAQuestion(in, 5*time.Hour)
	now := time.Now()
	if p := e.pass(now); p.Waiting != 1 {
		t.Fatalf("the pass: %+v", p)
	}
	if p := e.pass(now.Add(time.Minute)); p.Waiting != 0 || p.WaitingFollowUps != 0 {
		t.Fatalf("a second pass: %+v", p)
	}
	ns := e.notifications("web")
	if len(ns) != 1 {
		t.Fatalf("one notification: %v", ns)
	}
	n := ns[0]
	if n["kind"] != "waiting" || n["title"] != in.Title+" รอคำตอบจากคุณ" || n["run_id"] != run.ID.String() || n["reminder_id"] != nil ||
		n["link"] != "/chat/agents/i/"+in.ID.String()+"?thread=main&run="+run.ID.String() {
		t.Fatalf("the notification: %v", n)
	}
	for _, s := range []string{n["title"].(string), n["body"].(string)} {
		if s == "" || !ReadsForPerson(s) {
			t.Fatalf("does not read as written for a person: %q", s)
		}
	}
	if p := e.pass(now.Add(72*time.Hour - time.Minute)); p.WaitingFollowUps != 0 {
		t.Fatalf("a follow-up before three days: %+v", p)
	}
	if p := e.pass(now.Add(72*time.Hour + time.Minute)); p.WaitingFollowUps != 1 {
		t.Fatalf("the follow-up: %+v", p)
	}
	for _, d := range []time.Duration{74 * time.Hour, 10 * 24 * time.Hour} {
		if p := e.pass(now.Add(d)); p.Waiting != 0 || p.WaitingFollowUps != 0 {
			t.Fatalf("after %s: %+v", d, p)
		}
	}
	if k := e.countRows(`SELECT count(*) FROM agent_market.notifications WHERE run_id = $1 AND kind = 'waiting_followup'`, run.ID); k != 1 {
		t.Fatalf("follow-ups: %d", k)
	}

	// not yet four hours: nothing
	e2 := newEnv(t)
	in2 := e2.install()
	e2.parkedOnAQuestion(in2, 3*time.Hour)
	if p := e2.pass(time.Now()); p.Waiting != 0 {
		t.Fatalf("a question three hours old: %+v", p)
	}
	// the person opened the agent after the question was posted: nothing
	e3 := newEnv(t)
	in3 := e3.install()
	e3.parkedOnAQuestion(in3, 5*time.Hour)
	e3.mustDo(200, "POST", "/installs/"+in3.ID.String()+"/opened", nil)
	if p := e3.pass(time.Now()); p.Waiting != 0 {
		t.Fatalf("a question the person has seen: %+v", p)
	}
	_ = ctx
}
