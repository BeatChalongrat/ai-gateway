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
// model calls said about egress and about masking, what the "instructions" step noted, and what the
// round's card says (masking, and whether something of it left the organisation as typed).
func (e *itEnv) roundWithWords(r *Runner, words string) (said, masking []string, step string, card string, sentOut bool) {
	e.t.Helper()
	ctx := context.Background()
	e.rounds++
	mark := fmt.Sprintf("REF-R%d", e.rounds)
	words += " (" + mark + ")"
	e.egressMu.Lock()
	e.egressSaid, e.maskingSaid, e.egressMark = nil, nil, mark
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
		sentOut, _ = m["sent_out_unmasked"].(bool)
	}
	e.egressMu.Lock()
	said = append([]string(nil), e.egressSaid...)
	masking = append([]string(nil), e.maskingSaid...)
	e.egressMu.Unlock()
	return said, masking, step, card, sentOut
}

// AM16 — the admin's two switches for an agent, held every way on one agent (AM13b's fixture: until
// AM16 masking followed the leave switch, and this test said "one that can leave is masked").
//
// The bank reconciliation's only model call reads the typed words, and that reader is in-house whatever
// the switches say (selfserve.go textModels) — so nothing of these rounds leaves, and the card never
// says it did. What moves is what the gateway is told, and what the card says was done with the text:
//
//	stays inside, not masked (the default)   closed + masking off   → as typed       card "off"
//	may leave, masked (what "may leave" was) masking on             → masked         card "on"
//	may leave, not masked (AM16)             masking off            → as typed       card "off"
//	stays inside, masked (AM16)              masking on             → masked         card "on"
//
// And the card is never the runner's own claim: against a gateway that does not comply (one that does
// not know this runner, or an older one) the runner still asks, and the card still says "on".
func TestTheTwoSwitchesAreWhatTheGatewayIsToldAndTheCardSaysWhatWasDone(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	in := e.setupBankInstall()
	ctx := context.Background()
	r := e.runner("r1")
	words := "กระทบยอดเดือนกันยายน เรียงตามวันที่ ส่งเอกสารที่สำนักงานใหญ่ ชั้น 12 อาคารสาทรธานี ถนนสาทรเหนือ"
	all := func(said []string, want string) bool {
		for _, s := range said {
			if s != want {
				return false
			}
		}
		return len(said) > 0
	}
	if _, err := e.store.EnsureOrg(ctx, am.Config{}); err != nil {
		t.Fatal(err)
	}
	if tag, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.org_settings SET documents_may_leave = true WHERE org_id = $1`, am.DefaultOrg); err != nil || tag.RowsAffected() != 1 {
		t.Fatalf("the organisation's switch: %v, %d rows", err, tag.RowsAffected())
	}
	set := func(leave, masked bool) {
		t.Helper()
		if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_egress (org_id, slug, documents_may_leave, masked) VALUES ($1, $2, $3, $4)
			ON CONFLICT (org_id, slug) DO UPDATE SET documents_may_leave = EXCLUDED.documents_may_leave, masked = EXCLUDED.masked`,
			am.DefaultOrg, in.Slug, leave, masked); err != nil {
			t.Fatal(err)
		}
	}
	e.gatewayHonours.Store(true)
	for _, c := range []struct {
		what            string
		leave, masked   bool
		egress, masking string // what every call tells the gateway
		done            string // what the step notes and the card says was done
	}{
		{"stays inside, not masked (the default)", false, false, EgressClosed, maskingOff, am.MaskingOff},
		{"may leave, masked", true, true, "", maskingOn, am.MaskingOn},
		{"may leave, not masked", true, false, "", maskingOff, am.MaskingOff},
		{"stays inside, masked", false, true, "", maskingOn, am.MaskingOn},
	} {
		set(c.leave, c.masked)
		said, masking, step, card, sentOut := e.roundWithWords(r, words)
		if !all(said, c.egress) || !all(masking, c.masking) || step != c.done || card != c.done {
			t.Fatalf("%s: calls said egress %q masking %q, the step noted %q, the card says %q — want %q / %q / %q / %q",
				c.what, said, masking, step, card, c.egress, c.masking, c.done, c.done)
		}
		if sentOut {
			t.Fatalf("%s: the card says something left the organisation as typed — the model that read the words is in-house", c.what)
		}
	}

	// the default again, but a gateway that does not comply: the card says what was DONE
	set(false, false)
	e.gatewayHonours.Store(false)
	said, masking, step, card, _ := e.roundWithWords(r, words)
	if !all(said, EgressClosed) || !all(masking, maskingOff) || step != am.MaskingOn || card != am.MaskingOn {
		t.Fatalf("a gateway that says nothing: calls said %q %q, the step noted %q, the card says %q — want closed / off / on / on", said, masking, step, card)
	}
}
