// Cloud scheduler (FIX16) — the always-on timer that fires due cloud tasks.
//
// This is the piece FIX13 deliberately left unwired: a goroutine that calls
// CloudTaskRunner.Tick on an interval. Two layers keep it safe:
//
//   - In THIS process, a non-overlap guard skips a tick while the previous
//     one is still draining, so a slow batch never stacks ticks.
//   - Across processes/instances (and across a restart mid-tick), the DB
//     LEASE inside Tick→ClaimDue is the real guarantee: a task claimed by one
//     tick is invisible to every other until released or its lease goes stale.
//
// The loop owns no task state; every decision (authorize, run, audit, advance)
// lives in the runner, which FIX13 proved. StartScheduler only decides WHEN.
package service

import (
	"context"
	"log/slog"
	"sync"
	"time"
)

// StartScheduler launches the firing goroutine, bound to ctx (cancel it on
// shutdown to stop the loop). interval bounds scan latency; the per-task
// 1-hour floor is enforced by the store, not here.
func StartScheduler(ctx context.Context, runner *CloudTaskRunner, interval time.Duration, logger *slog.Logger) {
	if interval <= 0 {
		interval = time.Minute
	}
	if logger == nil {
		logger = slog.Default()
	}
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()

		var mu sync.Mutex
		var running bool

		runTick := func() {
			// Non-overlap guard: if the previous tick is still draining, skip
			// this one. The lease means a skipped tick loses nothing — the
			// still-due tasks are picked up next interval.
			mu.Lock()
			if running {
				mu.Unlock()
				logger.Debug("cloud scheduler: previous tick still running; skipping")
				return
			}
			running = true
			mu.Unlock()
			defer func() {
				mu.Lock()
				running = false
				mu.Unlock()
			}()
			if err := runner.Tick(ctx, time.Now().UTC()); err != nil {
				logger.Warn("cloud scheduler tick failed", "err", err)
			}
		}

		logger.Info("cloud scheduler started", "interval", interval.String(), "instance", runner.instanceID)
		for {
			select {
			case <-ctx.Done():
				logger.Info("cloud scheduler stopped")
				return
			case <-ticker.C:
				runTick()
			}
		}
	}()
}
