package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// roundWithWords runs one bank reconciliation whose typed words a model has to read (its only model call:
// the documents themselves are computed by script and written by rules), and returns what the round's
// model calls said about egress, what the "instructions" step noted, and what the round's card says.
func (e *itEnv) roundWithWords(r *Runner, words string) (said []string, step string, card string) {
	e.t.Helper()
	ctx := context.Background()
	e.rounds++
	mark := fmt.Sprintf("REF-R%d", e.rounds)
	words += " (" + mark + ")"
	e.egressMu.Lock()
	e.egressSaid, e.egressMark = nil, mark
	e.egressMu.Unlock()
	run := e.bankRun("kbank_sep.xlsx", "ledger_sep.xlsx")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET message = $2 WHERE id = $1`, run.ID, words); err != nil {
		e.t.Fatal(err)
	}
	if got := e.drive(r, run.ID, 6*time.Minute, terminal); got.Status != am.RunSucceeded {
		e.t.Fatalf("the round: %s %s", got.Status, got.StatusReason)
	}
	for _, s := range e.steps(run.ID) {
		if s.Name == "instructions" {
			var v struct {
				Masking string   `json:"masking"`
				Apply   []string `json:"apply"`
			}
			_ = json.Unmarshal(s.Output, &v)
			if strings.Join(v.Apply, " ") != "sort_by_date" {
				e.t.Fatalf("the words were read as %v — the note beside a step's output must not disturb the output", v.Apply)
			}
			step = v.Masking
		}
	}
	cards := e.outputsOf(run.ID)
	if len(cards) == 0 {
		e.t.Fatal("the round posted no files card")
	}
	if m, ok := cards[len(cards)-1]["model"].(map[string]any); ok {
		card, _ = m["masking"].(string)
	}
	e.egressMu.Lock()
	said = append([]string(nil), e.egressSaid...)
	e.egressMu.Unlock()
	return said, step, card
}

// AM13b — masking follows where the data goes, held both ways on one agent.
//
// Egress off (the default for an agent that touches accounting): the round's model call tells the gateway
// the data may not leave, the gateway sends it as typed and says so, and the round's card says "off".
// An admin lets the agent's documents leave: the same round says nothing to the gateway, is masked as
// before, and its card says "on" — although the model that answered is the same in-house one.
// And the card is never the runner's own claim: against a gateway that does not comply (one that does not
// know this runner, or an older one) the runner still asks, and the card still says "on".
func TestARoundWhoseDataCannotLeaveIsNotMaskedAndOneThatCanIsMasked(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	in := e.setupBankInstall()
	ctx := context.Background()
	r := e.runner("r1")
	words := "กระทบยอดเดือนกันยายน เรียงตามวันที่ ส่งเอกสารที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ"
	allClosed := func(said []string) bool {
		for _, s := range said {
			if s != EgressClosed {
				return false
			}
		}
		return len(said) > 0
	}
	noneClosed := func(said []string) bool {
		for _, s := range said {
			if s != "" {
				return false
			}
		}
		return len(said) > 0
	}

	// 1. nothing can leave, and the gateway knows this runner
	e.gatewayHonours.Store(true)
	said, step, card := e.roundWithWords(r, words)
	if !allClosed(said) || step != am.MaskingOff || card != am.MaskingOff {
		t.Fatalf("egress off, in-house model: calls said %q, the step noted %q, the card says %q — want closed / off / off", said, step, card)
	}

	// 2. an admin lets this agent's documents leave: masked as before, on the same in-house model
	if _, err := e.store.EnsureOrg(ctx, am.Config{}); err != nil {
		t.Fatal(err)
	}
	if tag, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.org_settings SET documents_may_leave = true WHERE org_id = $1`, am.DefaultOrg); err != nil || tag.RowsAffected() != 1 {
		t.Fatalf("the organisation's switch: %v, %d rows", err, tag.RowsAffected())
	}
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_egress (org_id, slug, documents_may_leave) VALUES ($1, $2, true)
		ON CONFLICT (org_id, slug) DO UPDATE SET documents_may_leave = true`, am.DefaultOrg, in.Slug); err != nil {
		t.Fatal(err)
	}
	said, step, card = e.roundWithWords(r, words)
	if !noneClosed(said) || step != am.MaskingOn || card != am.MaskingOn {
		t.Fatalf("egress on: calls said %q, the step noted %q, the card says %q — want nothing said / on / on", said, step, card)
	}

	// 3. egress off again, but a gateway that does not comply: the card says what was DONE
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_egress SET documents_may_leave = false WHERE org_id = $1 AND slug = $2`,
		am.DefaultOrg, in.Slug); err != nil {
		t.Fatal(err)
	}
	e.gatewayHonours.Store(false)
	said, step, card = e.roundWithWords(r, words)
	if !allClosed(said) || step != am.MaskingOn || card != am.MaskingOn {
		t.Fatalf("a gateway that says nothing: calls said %q, the step noted %q, the card says %q — want closed / on / on", said, step, card)
	}
}
