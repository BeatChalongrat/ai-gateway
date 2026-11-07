package handler

// The one line of stream_p95_test.go that cannot exist on the pre-FIX41
// tree, split out so the measurement file itself stays portable. When
// running the p95 probe in a worktree at the old commit, this file is
// simply not copied across.

import (
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

func configureLedgerIfPresent(t *testing.T, svc *service.MessageService) {
	t.Helper()
	url := os.Getenv("FIX41_BENCH_REDIS_URL")
	if url == "" {
		url = "redis://localhost:6380/1"
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		svc.ConfigureRunLedger(10*time.Minute, 8000, nil)
		return
	}
	c := redis.NewClient(opts)
	if err := c.Ping(t.Context()).Err(); err != nil {
		_ = c.Close()
		t.Logf("p95 probe: redis unreachable — measuring the in-memory ledger only")
		svc.ConfigureRunLedger(10*time.Minute, 8000, nil)
		return
	}
	t.Cleanup(func() { _ = c.Close() })
	svc.ConfigureRunLedger(10*time.Minute, 8000, c)
}
