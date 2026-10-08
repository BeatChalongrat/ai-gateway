package runner

import (
	"context"
	"encoding/json"
	"sync"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// Masking follows where the data goes (AM13b).
//
// Masking exists to stop data leaving the organisation. An agent whose egress switch is off (the
// organisation's "documents may leave" and the agent's own, egress.go) is only ever given in-house
// models and no web (choice.go mayUse, web.go) — nothing of its round can leave — so masking there
// protects nothing and destroys the work: an agent that reads tax invoices has to read the tax id and
// the address. Such a round's model calls say so to the gateway (X-Sran-Egress: closed) and the gateway,
// which holds the rule (backend app/services/dlp/in_house.py), sends them to the in-house model as typed.
// An agent whose egress is on, or a model outside the organisation, is masked exactly as before: the
// header is not sent.
//
// What a round is told about itself is what the gateway answered that it DID with each call
// (X-Gateway-Masking), never what the runner asked for: a gateway that does not know this runner's
// project, or an older one, masks — and the round's card then says "masked".
const (
	EgressHeader  = "X-Sran-Egress"
	EgressClosed  = "closed"
	MaskingHeader = "X-Gateway-Masking"
	maskingOff    = "off"
)

type modelCallsKey struct{}

// modelCalls is what became of the model calls made under one context.
type modelCalls struct {
	// closed: the data of these calls may not leave the organisation (the agent's egress is off).
	closed bool

	mu       sync.Mutex
	calls    int // calls the gateway answered
	unmasked int // of those, the ones it sent to the model as typed
}

// withModelCalls marks ctx for the model client: whether the data may leave, and where to note what
// the gateway did with each call.
func withModelCalls(ctx context.Context, closed bool) (context.Context, *modelCalls) {
	c := &modelCalls{closed: closed}
	return context.WithValue(ctx, modelCallsKey{}, c), c
}

func modelCallsOf(ctx context.Context) *modelCalls {
	c, _ := ctx.Value(modelCallsKey{}).(*modelCalls)
	return c
}

func (c *modelCalls) note(unmasked bool) {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.calls++
	if unmasked {
		c.unmasked++
	}
}

// masking is "" when no call was answered, else am.MaskingOff / am.MaskingOn / am.MaskingMixed.
func (c *modelCalls) masking() string {
	if c == nil {
		return ""
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	switch {
	case c.calls == 0:
		return ""
	case c.unmasked == c.calls:
		return am.MaskingOff
	case c.unmasked == 0:
		return am.MaskingOn
	}
	return am.MaskingMixed
}

// stampMasking writes what the gateway did with a step's model calls beside the step's own output, so
// a later pass over the same run — which reads the step back and calls no model — still knows.
func stampMasking(output json.RawMessage, masking string) json.RawMessage {
	if masking == "" {
		return output
	}
	var obj map[string]json.RawMessage
	if json.Unmarshal(output, &obj) != nil || obj == nil {
		return output // not an object: nothing to write beside
	}
	obj["masking"], _ = json.Marshal(masking)
	b, err := json.Marshal(obj)
	if err != nil {
		return output
	}
	return b
}

// roundMasking is what the round's card says: every model step of the round (and of this job, when it
// is another run — a render job maps a form with a model) put together. "" when no model was called.
func (rc *runCtx) roundMasking(ctx context.Context, run am.Run) string {
	ids := []am.Run{run}
	if rc.run.ID != run.ID {
		ids = append(ids, rc.run)
	}
	total, off := 0, 0
	for _, r := range ids {
		steps, err := rc.r.Store.ListSteps(ctx, r.ID)
		if err != nil {
			return ""
		}
		for _, s := range steps {
			var v struct {
				Masking string `json:"masking"`
			}
			if s.Status != "succeeded" || json.Unmarshal(s.Output, &v) != nil || v.Masking == "" {
				continue
			}
			switch v.Masking {
			case am.MaskingOff:
				total, off = total+1, off+1
			case am.MaskingOn:
				total++
			default: // mixed inside one step
				return am.MaskingMixed
			}
		}
	}
	switch {
	case total == 0:
		return ""
	case off == total:
		return am.MaskingOff
	case off == 0:
		return am.MaskingOn
	}
	return am.MaskingMixed
}
