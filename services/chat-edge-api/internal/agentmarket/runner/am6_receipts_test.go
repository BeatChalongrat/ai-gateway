package runner

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM6 — a receipt confirmed in this system is an answer no card carries: the
// person gave it with an action on the round's files. The working paper names
// who confirmed it, as it names whoever answered a card.
func TestWhoConfirmedAReceiptIsHandedToThePackage(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	run := e.round(am.KindRun, true)
	who := uuid.New()
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_settings (org_id) VALUES ('default') ON CONFLICT DO NOTHING`); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_members (org_id, email, user_id, display_name)
		VALUES ('default', 'somchai@example.test', $1, 'สมชาย คลังสินค้า')`, who); err != nil {
		t.Fatal(err)
	}
	const key = "pomatch_receipt:PO6909001#l:10"
	var ans map[string]am.Answer
	_ = json.Unmarshal([]byte(`{"`+key+`": {"value": "all", "label": "รับครบตามใบสั่งซื้อ", "moment": "run", "via": "action",
		"code": "pomatch_receipt", "by": "`+who.String()+`"},
		"pomatch_receipt:PO6909002#l:20": {"value": "all", "moment": "run", "via": "action", "code": "pomatch_receipt"}}`), &ans)
	if err := e.store.MergeAnswers(ctx, e.store.Pool, run.ID, ans); err != nil {
		t.Fatal(err)
	}
	in := e.install
	in.OrgID = "default"
	rc := &runCtx{ctx: ctx, r: e.r, run: run, install: in, pkg: &Package{Manifest: am.Manifest{RecordWhoAnswered: true}}}
	if err := rc.reloadAnswers(ctx); err != nil {
		t.Fatal(err)
	}
	got := rc.answeredBy(ctx)
	if got[key]["name"] != "สมชาย คลังสินค้า" || got[key]["via"] != "action" {
		t.Fatalf("who confirmed the receipt: %v", got)
	}
	if _, ok := got["pomatch_receipt:PO6909002#l:20"]; ok {
		t.Fatalf("an answer that names nobody has no name on it: %v", got)
	}
}
