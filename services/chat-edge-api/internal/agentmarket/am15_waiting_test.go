package agentmarket

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM15 — a run that waits for its model is not a run that waits for the person.
//
// The sidebar's count beside an agent is titled "รอคำตอบของคุณ" and a chat's row says "รอคำตอบ" for a
// run that is awaiting_input. Walking the built client with the model away showed both on a run that
// asked nothing: its card said "รอโมเดล" and the sidebar said the person owed an answer. A run that
// waits for its model is in neither count — and it is what the runner's watch finds, puts back in the
// queue when the model answers, and counts the waits of.
func TestARunThatWaitsForItsModelIsNotARunThatWaitsForThePerson(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	tid := e.newChat(in)
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum, ThreadID: ptrUUID(uuid.MustParse(tid))}, "w1")
	if err != nil {
		t.Fatal(err)
	}
	park := func(reason string) {
		t.Helper()
		tag, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input', status_reason = $2,
			awaiting_since = now() WHERE id = $1`, run.ID, reason)
		if err != nil || tag.RowsAffected() != 1 {
			t.Fatalf("park: %v (%d rows)", err, tag.RowsAffected())
		}
	}
	asksThePerson := func() (rail float64, chat bool) {
		t.Helper()
		r := e.mustDo(200, "GET", "/rail", nil)
		for _, c := range e.chats(in) {
			if c["id"] == tid {
				chat = c["waiting"] == true
			}
		}
		return r["waiting_total"].(float64), chat
	}

	// a run that asked a question: it waits for the person, as always
	park("waiting_preflight")
	if rail, chat := asksThePerson(); rail != 1 || !chat {
		t.Fatalf("a run that asked waits for the person: rail %v, chat %v", rail, chat)
	}
	if waiting, _ := e.store.ModelParked(ctx); len(waiting) != 0 {
		t.Fatalf("and is not one that waits for its model: %d", len(waiting))
	}

	// a run that waits for its model: nothing is asked of the person
	park(ReasonModelUnreachable)
	if rail, chat := asksThePerson(); rail != 0 || chat {
		t.Fatalf("a run that waits for its model asks the person nothing: rail %v, chat %v", rail, chat)
	}
	waiting, err := e.store.ModelParked(ctx)
	if err != nil || len(waiting) != 1 || waiting[0].ID != run.ID {
		t.Fatalf("it is what the watch finds: %v %v", waiting, err)
	}

	// each wait leaves its notice, and that is what the bound counts
	if n, _ := e.store.ModelWaits(ctx, run); n != 0 {
		t.Fatalf("no wait said yet: %d", n)
	}
	if err := e.store.AddNotice(ctx, e.store.Pool, run, Notice{Text: ModelAwayNotice(), Tone: "warning", Reason: ReasonModelUnreachable}); err != nil {
		t.Fatal(err)
	}
	if n, _ := e.store.ModelWaits(ctx, run); n != 1 {
		t.Fatalf("one wait: %d", n)
	}

	// the model answers: the run is put back in the queue, and its thread says so — once
	if n, err := e.store.ResumeModelParked(ctx); err != nil || n != 1 {
		t.Fatalf("resumed: %d %v", n, err)
	}
	got, _ := e.store.RunByID(ctx, run.ID)
	if got.Status != RunQueued || got.AwaitingSince != nil {
		t.Fatalf("back in the queue: %s", got.Status)
	}
	if n, _ := e.store.ResumeModelParked(ctx); n != 0 {
		t.Fatalf("nothing waits any more: %d", n)
	}
	if n, _ := e.store.ModelWaits(ctx, run); n != 1 {
		t.Fatalf("the line that says the run goes on is not a wait: %d", n)
	}
	var back int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_thread_items WHERE run_id = $1 AND kind = 'notice'
		AND payload::text LIKE '%' || $2 || '%'`, run.ID, "ติดต่อโมเดลได้แล้ว").Scan(&back)
	if back != 1 {
		t.Fatalf("the thread says the run goes on, once: %d", back)
	}
}

// The words of a wait are in both languages, say nothing of cost (AM11b), and name no code.
func TestTheWordsOfAWaitForTheModelAreInBothLanguages(t *testing.T) {
	for name, text := range map[string]PersonText{"away": ModelAwayNotice(), "back": ModelBackNotice(), "answer": ModelAwayAnswer()} {
		th, en := text.In(Thai), text.In(English)
		if th == "" || en == "" || th == en {
			t.Fatalf("%s: both languages: %q / %q", name, th, en)
		}
		for _, bad := range []string{"credit", "เครดิต", "บาท", "model_unreachable", "504", "timeout", "gateway"} {
			if strings.Contains(strings.ToLower(en), bad) || strings.Contains(th, bad) {
				t.Fatalf("%s says %q: %q / %q", name, bad, th, en)
			}
		}
		if strings.Contains(strings.ToLower(en), "round") {
			t.Fatalf("%s: English says run, never round: %q", name, en)
		}
	}
	f := FailureFor(ReasonModelUnreachable, FailureFacts{Kind: KindRun, HasFiles: true, KeepDays: 7})
	if !strings.Contains(f.What.In(Thai), "ติดต่อโมเดล") || !strings.Contains(f.What.In(English), "could not be reached") {
		t.Fatalf("a run that ended after its waits says why in both languages: %q / %q", f.What.In(Thai), f.What.In(English))
	}
}
