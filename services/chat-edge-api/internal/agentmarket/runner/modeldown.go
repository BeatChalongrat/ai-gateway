package runner

import (
	"context"
	"errors"
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
	_ = r.Store.AddNotice(bg, r.Store.Pool, rc.run, am.Notice{Text: am.ModelAwayNotice(), Tone: "warning", Reason: am.ReasonModelUnreachable})
	r.syncProgress(bg, rc.run.ID)
	rc.log.Info("run waits for its model")
	return nil
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
	_, err = m.send(ctx, m.Primary, []chatMsg{{Role: "user", Content: "Reply with the single word: ok"}}, 0, 16)
	return ctx.Err() == nil && !errors.Is(err, ErrModelUnreachable)
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
