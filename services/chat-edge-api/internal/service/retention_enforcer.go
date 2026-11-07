// RetentionEnforcer — S9 background expiry job.
//
// Walks chat.tenant_memory_settings for every tenant with a
// positive retention_days, then SoftDeletes (status='deleted' +
// content scrubbed) any active memory older than the cutoff.
// Pinned memories are exempt — see MemoryRepo.ExpireOlderThan.
//
// Runs every retentionInterval inside StartBackground, plus once
// on startup so a freshly-restarted process catches up
// immediately. Shutdown signal comes from the parent ctx so the
// server's graceful-shutdown flow stops the goroutine cleanly.
package service

import (
	"context"
	"log/slog"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// retentionInterval — daily sweeps. Tighter wouldn't help (the
// cutoff math is day-granularity); slower would let expired
// memories linger past their stated lifetime.
const retentionInterval = 24 * time.Hour

type RetentionEnforcer struct {
	memories *repository.MemoryRepo
	settings *repository.TenantMemorySettingsRepo
}

func NewRetentionEnforcer(
	memories *repository.MemoryRepo,
	settings *repository.TenantMemorySettingsRepo,
) *RetentionEnforcer {
	return &RetentionEnforcer{memories: memories, settings: settings}
}

// EnforceOnce runs one pass across every tenant with a configured
// retention_days. Returns the total number of rows expired so the
// caller (test, manual trigger) can assert. Best-effort per-
// tenant — a single failed tenant logs + the loop continues.
func (e *RetentionEnforcer) EnforceOnce(ctx context.Context) int {
	tenants, err := e.settings.ListWithRetention(ctx)
	if err != nil {
		slog.Warn("retention: list tenants failed", "err", err)
		return 0
	}
	total := 0
	for i := range tenants {
		t := &tenants[i]
		if t.RetentionDays == nil || *t.RetentionDays <= 0 {
			continue
		}
		cutoff := time.Now().AddDate(0, 0, -(*t.RetentionDays))
		n, err := e.memories.ExpireOlderThan(ctx, t.TenantID, cutoff)
		if err != nil {
			slog.Warn("retention: expire failed",
				"tenant", t.TenantID.String(), "err", err)
			continue
		}
		if n > 0 {
			slog.Info("retention: expired memories",
				"tenant", t.TenantID.String(),
				"days", *t.RetentionDays,
				"expired", n)
			total += n
		}
	}
	return total
}

// StartBackground runs EnforceOnce immediately and then every
// retentionInterval until ctx is cancelled. Designed to live in
// main.go behind the same lifetime as the HTTP server's parent
// ctx so the SIGINT/SIGTERM shutdown stops both cleanly.
//
// Returns when ctx fires; the caller can wait via a sync.WaitGroup
// if it wants confirmation the goroutine drained.
func (e *RetentionEnforcer) StartBackground(ctx context.Context) {
	// First pass on startup — catches up after downtime.
	e.EnforceOnce(ctx)

	t := time.NewTicker(retentionInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			e.EnforceOnce(ctx)
		}
	}
}
