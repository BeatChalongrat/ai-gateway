package runner

import (
	"context"
	"encoding/json"
	"sync"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// Where a round's data may go, and whether it is masked on the way: two switches of the admin's, per
// agent (AM16; egress.go).
//
// Until AM16 masking followed the first switch (AM13b): an agent whose documents could not leave was
// sent to its in-house model as typed, and one whose documents could leave was masked. The owner
// separated them: whether documents may leave is one choice, whether personal data is masked before a
// model reads it is another, and it is off unless an admin turns it on. So a round whose documents may
// leave, and whose agent is not masked, sends them to a model outside the organisation as typed.
//
// Each model call tells the gateway, which holds the rule (backend app/services/dlp/in_house.py), what
// the two switches say: X-Sran-Masking: off|on, and X-Sran-Egress: closed when the data may not leave.
//
// What a round is told about itself is what HAPPENED, never what the runner asked for:
//
//   - masking: what the gateway answered that it did with each call's text (X-Gateway-Masking) — a
//     gateway that does not know this runner's project, or an older one, masks, and the card says so;
//   - sent out unmasked: a call that went to a model outside the organisation with something in it as
//     typed — text the gateway did not mask, or the image of a page. A page image is never masked: the
//     gateway masks text. (Before AM16 a scanned page read by an outside model left as it was while the
//     round's card said "masked".) This is what the card says on its face.
const (
	EgressHeader     = "X-Sran-Egress"
	EgressClosed     = "closed"
	AskMaskingHeader = "X-Sran-Masking"
	MaskingHeader    = "X-Gateway-Masking"
	maskingOff       = "off"
	maskingOn        = "on"
)

type modelCallsKey struct{}

// modelCalls is what became of the model calls made under one context.
type modelCalls struct {
	// closed: the data of these calls may not leave the organisation (the agent's egress is off).
	closed bool
	// masked: the admin's masking switch for this agent is on.
	masked bool

	mu       sync.Mutex
	calls    int // calls the gateway answered
	unmasked int // of those, the ones whose text it sent to the model as typed
	sentOut  int // calls that took something out of the organisation as it was typed
}

// withModelCalls marks ctx for the model client: what the agent's two switches say, and where to note
// what happened to each call.
func withModelCalls(ctx context.Context, closed, masked bool) (context.Context, *modelCalls) {
	c := &modelCalls{closed: closed, masked: masked}
	return context.WithValue(ctx, modelCallsKey{}, c), c
}

type recipientsKey struct{}

// withRecipients (AM16) marks ctx with the recipients named for the run's agent: every model call made
// under it tells the gateway that list, and the gateway refuses to send the call to anyone else. The
// list is never nil here — an agent nobody named a recipient for has the empty list, which is nobody.
func withRecipients(ctx context.Context, recipients []string) context.Context {
	if recipients == nil {
		recipients = []string{}
	}
	return context.WithValue(ctx, recipientsKey{}, recipients)
}

// recipientsOf is the list a call must carry, and whether it carries one at all (a call of the runner's
// own, outside any run, does not — the health check of its model, which is in-house).
func recipientsOf(ctx context.Context) ([]string, bool) {
	l, ok := ctx.Value(recipientsKey{}).([]string)
	return l, ok
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

// noteSentOut: a call went to a model outside the organisation with text or a page in it as typed.
func (c *modelCalls) noteSentOut() {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.sentOut++
}

func (c *modelCalls) wentOut() bool {
	if c == nil {
		return false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.sentOut > 0
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

type pagesKey struct{}

// withPages marks a call that carries the image of a page: an image is sent as it is, whatever the
// gateway does with the text beside it.
func withPages(ctx context.Context) context.Context { return context.WithValue(ctx, pagesKey{}, true) }

func carriesPages(ctx context.Context) bool {
	v, _ := ctx.Value(pagesKey{}).(bool)
	return v
}

// refusedBeforeSending: a gateway answer that says the request never reached a provider — it was not
// let in (401, 403), not paid for (402), named a model or route that is not there (404), or was not a
// request at all (422). Any other failure may have come after the provider was sent the request.
func refusedBeforeSending(status int) bool {
	switch status {
	case 401, 402, 403, 404, 422:
		return true
	}
	return false
}

// stampMasking writes what the gateway did with a step's model calls beside the step's own output, so
// a later pass over the same run — which reads the step back and calls no model — still knows.
func stampMasking(output json.RawMessage, masking string) json.RawMessage {
	if masking == "" {
		return output
	}
	return stamp(output, "masking", masking)
}

// sentOutKey is written beside a step's output — a failed step's too — when one of its calls took
// something out of the organisation as typed.
const sentOutKey = "sent_out_unmasked"

func stampSentOut(output json.RawMessage, sent bool) json.RawMessage {
	if !sent {
		return output
	}
	if len(output) == 0 || string(output) == "null" {
		output = json.RawMessage(`{}`)
	}
	return stamp(output, sentOutKey, true)
}

func stamp(output json.RawMessage, key string, value any) json.RawMessage {
	var obj map[string]json.RawMessage
	if json.Unmarshal(output, &obj) != nil || obj == nil {
		return output // not an object: nothing to write beside
	}
	obj[key], _ = json.Marshal(value)
	b, err := json.Marshal(obj)
	if err != nil {
		return output
	}
	return b
}

// sentOutStamped reads the stamp back from a step's output.
func sentOutStamped(output json.RawMessage) bool {
	var v struct {
		Sent bool `json:"sent_out_unmasked"`
	}
	return len(output) > 0 && json.Unmarshal(output, &v) == nil && v.Sent
}

// sentOutOf: one of these steps — whatever became of it — took something out of the organisation as
// typed. A step that failed after the provider was sent its page counts: the page left.
func sentOutOf(steps []am.Step) bool {
	for _, s := range steps {
		if sentOutStamped(s.Output) {
			return true
		}
	}
	return false
}

// roundMasking is what the round's card says: every model step of the round (and of this job, when it
// is another run — a render job maps a form with a model) put together. "" when no model was called.
func (rc *runCtx) roundMasking(ctx context.Context, run am.Run) string {
	total, off := 0, 0
	for _, r := range rc.runsOf(run) {
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

// roundSentOut: something of this round (or of this job, when it is another run) left the
// organisation as typed. Read from every step, failed ones included.
func (rc *runCtx) roundSentOut(ctx context.Context, run am.Run) bool {
	for _, r := range rc.runsOf(run) {
		steps, err := rc.r.Store.ListSteps(ctx, r.ID)
		if err == nil && sentOutOf(steps) {
			return true
		}
	}
	return false
}

func (rc *runCtx) runsOf(run am.Run) []am.Run {
	ids := []am.Run{run}
	if rc.run.ID != run.ID {
		ids = append(ids, rc.run)
	}
	return ids
}
