package runner

import (
	"context"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// reminders is the runner's minute pass for AM6 section B (agentmarket's
// reminders.go and notify.go): reminders that fell due open rounds that wait
// for files, one follow-up each, and the owners of rounds parked on a
// question are told. It writes rows only — it never claims a round, never
// starts a step: nothing runs without a person, and the round it opens
// starts when that person sends the files. Several runners may run the pass
// at once; each reminder is taken under its own row lock (SKIP LOCKED).
func (r *Runner) reminders(ctx context.Context) {
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		if p, err := r.RemindersOnce(ctx); err != nil {
			if ctx.Err() == nil {
				r.logger().Warn("reminders pass failed", "err", err)
			}
		} else if p != (am.ReminderPass{}) {
			r.logger().Info("reminders pass", "fired", p.Fired, "skipped", p.Skipped, "closed_without_files", p.Cancelled,
				"follow_ups", p.FollowUps, "waiting", p.Waiting, "waiting_follow_ups", p.WaitingFollowUps)
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

// RemindersOnce is one pass of the loop, at the runner's clock.
func (r *Runner) RemindersOnce(ctx context.Context) (am.ReminderPass, error) {
	now := time.Now()
	if r.Now != nil {
		now = r.Now()
	}
	return r.Store.RemindersPass(ctx, now)
}
