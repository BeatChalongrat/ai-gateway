package runner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── A package's completion gate (AM5) ────────────────────────────────────
//
// Some work is either right or not finished: a bank reconciliation whose
// adjusted bank balance and adjusted book balance differ is not a
// reconciliation yet. A package whose manifest says "completion_gate" puts
// its verdict in its computed table:
//
//	"gate": {"passed": false, "code": "not_tied",
//	         "what": "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท", "next": "…"}
//
// The runner reads it after the files are made. Passed: the round goes on
// as any other. Not passed: the files are still delivered — marked as a
// draft, so that the person can see why — and the round ends with the
// gate's code; what the person reads is the package's sentence. The
// verdict is arithmetic done by the package's script: no model is asked,
// and nothing here can turn a gate that did not pass into one that did.
//
// AM6: such a round delivered its work. The database keeps the status it
// always gave it (failed, with the gate's code: no sign-off, nothing into the
// duplicate index); what the round came to is "completed, open"
// (am.Run.Outcome), it is charged like any other, and what it says in the
// thread is "เสร็จแล้ว · ยังไม่ลงตัว" — not an error (endOpen, pipeline.go).

// gateView is a package's verdict on its own round.
type gateView struct {
	Passed bool   `json:"passed"`
	Code   string `json:"code"`
	What   string `json:"what"`
	Next   string `json:"next"`
}

var gateCode = regexp.MustCompile(`^[a-z][a-z0-9_]{2,39}$`)

// Reason is the run's status reason for a gate that did not pass: the
// package's code when it is one, never a sentence.
func (g gateView) Reason() string {
	if gateCode.MatchString(g.Code) {
		return g.Code
	}
	return "gate_not_passed"
}

// words are the gate's own sentences as a person is shown them.
func (g gateView) words() (what, next am.PersonText) {
	return am.GateWords(g.Passed, g.Reason(), g.What, g.Next)
}

// gateOf reads the gate of a computed table: nil when the table has none.
func gateOf(computed []byte) (*gateView, error) {
	var v struct {
		Gate json.RawMessage `json:"gate"`
	}
	if err := json.Unmarshal(computed, &v); err != nil {
		return nil, err
	}
	if len(v.Gate) == 0 || string(v.Gate) == "null" {
		return nil, nil
	}
	var raw struct {
		Passed *bool  `json:"passed"`
		Code   string `json:"code"`
		What   string `json:"what"`
		Next   string `json:"next"`
	}
	if err := json.Unmarshal(v.Gate, &raw); err != nil {
		return nil, fmt.Errorf("gate: %w", err)
	}
	if raw.Passed == nil {
		return nil, errors.New("gate: the package did not say whether it passed")
	}
	return &gateView{Passed: *raw.Passed, Code: raw.Code, What: raw.What, Next: raw.Next}, nil
}

// gateErr is a gate that did not pass, as the round's ending carries it: what
// a person reads is built from the gate's own sentences.
type gateErr struct {
	gate  gateView
	draft bool // the round's files were delivered as a draft
}

func (e *gateErr) Error() string {
	return fmt.Sprintf("gate: %s — %s", e.gate.Reason(), e.gate.What)
}

// completionGate is the package's verdict on this round, kept as a step of
// the run (the run page shows it). nil: the package has no gate.
func (rc *runCtx) completionGate(ctx context.Context, computed blobRef, computeKey string) (*gateView, error) {
	if !rc.pkg.Manifest.CompletionGate {
		return nil, nil
	}
	_, data, err := rc.readComputed(ctx, computed)
	if err != nil {
		return nil, err
	}
	g, err := gateOf(data)
	if err != nil {
		return nil, err
	}
	if g == nil {
		// a package that says it has a gate and gives none has not passed it
		return nil, errors.New("gate: the package's computed table has no gate")
	}
	st := rc.r.Store
	key := am.StepKey(rc.run.ID, "gate", computeKey)
	step, done, err := st.BeginStep(ctx, rc.run.ID, "gate", "check", key, json.RawMessage(`{}`))
	if err != nil {
		return nil, err
	}
	if !done {
		out, _ := json.Marshal(g)
		status, why := "succeeded", ""
		if !g.Passed {
			status, why = "failed", (&gateErr{gate: *g}).Error()
		}
		if err := st.FinishStep(ctx, step.ID, status, out, am.TrimText(why, 1500), "gate", "", 0); err != nil {
			return nil, err
		}
		rc.r.syncProgress(ctx, rc.run.ID)
	}
	return g, nil
}
