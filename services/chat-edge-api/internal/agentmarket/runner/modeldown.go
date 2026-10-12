package runner

import (
	"context"
	"errors"
	"fmt"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── AM15 on the execution plane: a run that cannot reach its model waits ──
//
// A step that asks a model and finds none it may use reachable (ErrModelUnreachable, model.go) fails as
// a step and the run is parked — awaiting_input, "model_unreachable" — with a notice in its thread, in
// the run's language. Nothing after that step has run: no file is rendered, nothing is published. The
// failed step is the first unfinished one, so the run that comes back asks again and goes on from there
// (pipeline.go: a step whose key is done is read back, never re-run).
//
// It comes back by itself: while any run waits, modelWatch asks the model one word every ModelRetry
// and, on an answer, puts every waiting run back in the queue (am.Store.ResumeModelParked).

// parkForModel stops a run to wait for its model — or, when it has waited am.MaxModelWaits times and
// found the model away again each time it went on, ends it failed with that said (the model answers
// the watch's word and not this run's question: waiting again would go round for ever).
func (r *Runner) parkForModel(rc *runCtx) error {
	bg := context.Background()
	if n, err := r.Store.ModelWaits(bg, rc.run); err == nil && n >= am.MaxModelWaits {
		return r.fail(rc.ctx, rc.run, am.ReasonModelUnreachable, ErrModelUnreachable)
	}
	if err := r.Store.Park(bg, rc.run.ID, r.ID, am.ReasonModelUnreachable); err != nil {
		return err
	}
	// it has delivered nothing: what its finished steps were charged is held while it waits (am.Store.HoldCharges)
	if err := r.Store.HoldCharges(bg, r.Store.Pool, rc.run.ID); err != nil {
		rc.log.Warn("the charges of a run that waits for its model were not held", "err", err)
	}
	_ = r.Store.AddNotice(bg, r.Store.Pool, rc.run, am.Notice{Text: am.ModelAwayNotice(), Tone: "warning", Reason: am.ReasonModelUnreachable})
	r.syncProgress(bg, rc.run.ID)
	rc.log.Info("run waits for its model")
	return nil
}

// ModelWord is what came of asking the primary model one word: who was asked, whether anything came
// back from it, and — when another model answered in its place — which.
type ModelWord struct {
	Asked    string    `json:"asked"`
	Answered bool      `json:"answered"`        // something came back from the model asked (an empty answer too)
	Served   string    `json:"served"`          // the model that answered: the one asked, another, or "" for none
	MS       int64     `json:"ms"`              // how long it took
	At       time.Time `json:"at"`              // when it was asked
	Error    string    `json:"error,omitempty"` // why there is no usable answer, as the gateway said it
}

// word asks the primary model one word through the same client, key and gateway every step uses.
func (m *ModelClient) word(ctx context.Context) ModelWord {
	w := ModelWord{Asked: m.Primary, At: time.Now().UTC()}
	if !m.Available() {
		w.Error = "no model is configured"
		return w
	}
	start := time.Now()
	// (a few characters no earlier call sent: the gateway keeps an answer for an hour under the same
	// words, and an answer out of that cache says nothing about who answers now)
	c, err := m.send(ctx, m.Primary, []chatMsg{{Role: "user", Content: fmt.Sprintf("Reply with the single word: ok [%x]", start.UnixNano())}}, 0, 16)
	w.MS = time.Since(start).Milliseconds()
	var away *unreachable
	switch {
	case err == nil && c.Cached:
		w.Error = "answered out of the gateway's cache: nobody answered now"
	case err == nil:
		w.Answered, w.Served = true, c.Model
	case errors.As(err, &away):
		w.Served, w.Error = away.other, trim(err.Error(), 240)
	case ctx.Err() != nil:
		w.Error = "stopped before an answer"
	default:
		// the model asked answered, and what it said cannot be used (send checks who answered first)
		w.Answered, w.Served, w.Error = true, m.Primary, trim(err.Error(), 240)
	}
	return w
}

// Reachable asks the primary model one word and says whether anything came back from it: an answer
// of any kind — an empty one too — is the model being there. Like every model call of the runner it
// waits its turn behind chat and behind the runner's other calls.
func (m *ModelClient) Reachable(ctx context.Context) bool {
	if !m.Available() {
		return false
	}
	release, err := m.acquire(ctx)
	if err != nil {
		return false
	}
	defer release()
	m.yield(ctx)
	return m.word(ctx).Answered
}

// WordWithin is the word for whoever asks from outside (the runner's own /model page, read by the
// smoke suite): the last one when it is younger than maxAge, else a new one. It does not wait behind
// chat — one word is not a turn — and it is what makes "the runner has a model" something that can be
// false: /healthz says only that a model is configured.
func (m *ModelClient) WordWithin(ctx context.Context, maxAge time.Duration) ModelWord {
	m.wordMu.Lock()
	defer m.wordMu.Unlock()
	if !m.lastWord.At.IsZero() && time.Since(m.lastWord.At) < maxAge {
		return m.lastWord
	}
	m.lastWord = m.word(ctx)
	return m.lastWord
}

// resumeModelParked: if a run waits for its model and the model answers, every such run goes on.
func (r *Runner) resumeModelParked(ctx context.Context) int {
	waiting, err := r.Store.ModelParked(ctx)
	if err != nil || len(waiting) == 0 {
		return 0 // nothing waits: the model is not asked
	}
	if !r.Model.Reachable(ctx) {
		r.logger().Info("runs still wait for their model", "runs", len(waiting))
		return 0
	}
	n, err := r.Store.ResumeModelParked(ctx)
	if err != nil {
		r.logger().Warn("resuming the runs that waited for their model failed", "err", err)
		return 0
	}
	if n > 0 {
		r.logger().Info("runs resumed after waiting for their model", "runs", n)
	}
	return n
}

// modelWatch runs resumeModelParked every ModelRetry (two minutes unless set) until ctx ends.
func (r *Runner) modelWatch(ctx context.Context) {
	t := time.NewTicker(orDefault(r.ModelRetry, 2*time.Minute))
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			r.resumeModelParked(ctx)
		}
	}
}
