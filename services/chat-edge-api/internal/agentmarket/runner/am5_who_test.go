package runner

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM5 phase E — "record the pass and who confirmed". A pair a person
// confirmed said only that a person had; the working paper now names them.
// The runner hands a package that asks for it (manifest
// "record_who_answered") the name of whoever answered each card — read from
// the card itself, never from what the answer says.

func TestWhoAnsweredACardIsHandedToThePackageThatAsksForIt(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	run := e.round(am.KindRun, true)
	reviewer := uuid.New()
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_settings (org_id) VALUES ('default') ON CONFLICT DO NOTHING`); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_members (org_id, email, user_id, display_name)
		VALUES ('default', 'somying@example.test', $1, 'สมหญิง ผู้ตรวจ')`, reviewer); err != nil {
		t.Fatal(err)
	}
	const key = "e3_candidate:k1~b1"
	qs, _ := json.Marshal([]am.Question{{Key: key, Code: "e3_candidate", Question: "เป็นรายการเดียวกันหรือไม่"}})
	card, err := e.store.CreateCard(ctx, e.store.Pool, am.Card{RunID: run.ID, InstallID: run.InstallID, TenantID: run.TenantID,
		UserID: run.UserID, Moment: "preflight", Kind: "questions", Blocking: true, Questions: qs})
	if err != nil {
		t.Fatal(err)
	}
	ans := map[string]am.Answer{key: {Value: json.RawMessage(`"match"`), Label: "เป็นรายการเดียวกัน", Moment: "preflight",
		CardID: card.ID.String(), Via: "card", Code: "e3_candidate"},
		"bankrec_side:dabc": {Value: json.RawMessage(`"unused"`), Moment: "preflight", Via: "default", Code: "bankrec_side"}}
	ab, _ := json.Marshal(ans)
	if _, _, err := e.store.CloseCard(ctx, e.store.Pool, card.ID, "answered", ab, "card", &reviewer); err != nil {
		t.Fatal(err)
	}
	if err := e.store.MergeAnswers(ctx, e.store.Pool, run.ID, ans); err != nil {
		t.Fatal(err)
	}
	in := e.install
	in.OrgID = "default"
	rc := &runCtx{ctx: ctx, r: e.r, run: run, install: in, pkg: &Package{Manifest: am.Manifest{RecordWhoAnswered: true}}}
	if err := rc.reloadAnswers(ctx); err != nil {
		t.Fatal(err)
	}
	who := rc.answeredBy(ctx)
	if who[key]["name"] != "สมหญิง ผู้ตรวจ" || who[key]["via"] != "card" {
		t.Fatalf("who answered the card: %v", who)
	}
	if _, ok := who["bankrec_side:dabc"]; ok {
		t.Fatalf("an answer nobody gave (a default) has no name on it: %v", who)
	}
	// a package that does not ask is handed nothing, and its step keys are what they were
	rc.pkg = &Package{Manifest: am.Manifest{}}
	if got := rc.answeredBy(ctx); got != nil {
		t.Fatalf("not asked for: %v", got)
	}
}
