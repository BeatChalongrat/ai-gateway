package service

// FIX130 A1e — per-conversation bookkeeping for the memory extractor:
//
//	watermark — how many messages the conversation held when its last
//	            extraction run read it. The next run reads only what came
//	            after, so no message is sent for extraction twice.
//	claim     — at most one run in flight per conversation (two turns
//	            finishing close together, or two chat-edge replicas).
//
// No migration: the marks live in Redis (chat-edge already holds a client
// for the run ledger) with a TTL, or in process when Redis is not wired
// (tests, a single dev instance). Losing a watermark (TTL, flush) costs at
// most one re-read of a conversation's newest window, which the dedup gate
// keeps from turning into duplicate suggestions.

import (
	"context"
	"log/slog"
	"strconv"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// extractionClaimTTL outlives the longest a claimed run can take: the wait
// for the turn to end (backgroundWaitMax) plus one call per model tried.
const extractionClaimTTL = backgroundWaitMax + 3*preferenceExtractionTimeout

// extractionWatermarkTTL — a conversation idle this long starts afresh.
const extractionWatermarkTTL = 30 * 24 * time.Hour

type extractionMarks interface {
	Watermark(ctx context.Context, convID uuid.UUID) int
	SetWatermark(ctx context.Context, convID uuid.UUID, n int)
	TryClaim(ctx context.Context, convID uuid.UUID) bool
	Release(ctx context.Context, convID uuid.UUID)
}

// memoryExtractionMarks is the in-process implementation.
type memoryExtractionMarks struct {
	mu     sync.Mutex
	wm     map[uuid.UUID]int
	claims map[uuid.UUID]time.Time // claim → expiry
}

func newMemoryExtractionMarks() *memoryExtractionMarks {
	return &memoryExtractionMarks{wm: map[uuid.UUID]int{}, claims: map[uuid.UUID]time.Time{}}
}

func (m *memoryExtractionMarks) Watermark(_ context.Context, id uuid.UUID) int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.wm[id]
}

func (m *memoryExtractionMarks) SetWatermark(_ context.Context, id uuid.UUID, n int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.wm[id] = n
}

func (m *memoryExtractionMarks) TryClaim(_ context.Context, id uuid.UUID) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	if exp, ok := m.claims[id]; ok && time.Now().Before(exp) {
		return false
	}
	m.claims[id] = time.Now().Add(extractionClaimTTL)
	return true
}

func (m *memoryExtractionMarks) Release(_ context.Context, id uuid.UUID) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.claims, id)
}

// redisExtractionMarks keeps both marks in Redis. A Redis error falls back
// to the in-process marks for that call (logged), so an outage degrades to
// single-instance behaviour instead of stopping extraction or running it
// on every turn.
type redisExtractionMarks struct {
	rdb      *redis.Client
	fallback *memoryExtractionMarks
}

func newRedisExtractionMarks(rdb *redis.Client) *redisExtractionMarks {
	return &redisExtractionMarks{rdb: rdb, fallback: newMemoryExtractionMarks()}
}

func extractionWatermarkKey(id uuid.UUID) string { return "chat-edge:memextract:wm:" + id.String() }
func extractionClaimKey(id uuid.UUID) string     { return "chat-edge:memextract:run:" + id.String() }

func (r *redisExtractionMarks) Watermark(ctx context.Context, id uuid.UUID) int {
	v, err := r.rdb.Get(ctx, extractionWatermarkKey(id)).Result()
	if err == redis.Nil {
		return 0
	}
	if err != nil {
		slog.Warn("memory_extraction: redis watermark read failed; using in-process", "err", err)
		return r.fallback.Watermark(ctx, id)
	}
	n, _ := strconv.Atoi(v)
	return n
}

func (r *redisExtractionMarks) SetWatermark(ctx context.Context, id uuid.UUID, n int) {
	r.fallback.SetWatermark(ctx, id, n)
	if err := r.rdb.Set(ctx, extractionWatermarkKey(id), n, extractionWatermarkTTL).Err(); err != nil {
		slog.Warn("memory_extraction: redis watermark write failed", "err", err)
	}
}

func (r *redisExtractionMarks) TryClaim(ctx context.Context, id uuid.UUID) bool {
	ok, err := r.rdb.SetNX(ctx, extractionClaimKey(id), "1", extractionClaimTTL).Result()
	if err != nil {
		slog.Warn("memory_extraction: redis claim failed; using in-process", "err", err)
		return r.fallback.TryClaim(ctx, id)
	}
	return ok
}

func (r *redisExtractionMarks) Release(ctx context.Context, id uuid.UUID) {
	r.fallback.Release(ctx, id)
	if err := r.rdb.Del(ctx, extractionClaimKey(id)).Err(); err != nil {
		slog.Warn("memory_extraction: redis claim release failed", "err", err)
	}
}
