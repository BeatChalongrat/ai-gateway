package service

// FIX131 E1 — the Redis lease store keeps the same contract as the
// in-memory one the other FIX131 tests use. Runs against the run-ledger
// test Redis (FIX41_BENCH_REDIS_URL) under its own key prefix, with
// second-long TTLs, and deletes what it wrote.

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

func TestFIX131_RedisLeaseStoreContract(t *testing.T) {
	rdb := benchRedis(t)
	if rdb == nil {
		testdeps.Missing(t, "redis unreachable (FIX41_BENCH_REDIS_URL)")
		return
	}
	prefix := "fix131test:runlease:" + uuid.NewString()[:8] + ":"
	st := NewRedisLeaseStore(rdb, prefix)
	ctx := context.Background()
	a, b := uuid.New(), uuid.New()
	t.Cleanup(func() { _ = rdb.Del(context.Background(), prefix+a.String(), prefix+b.String()).Err() })

	if alive, err := st.Alive(ctx, []uuid.UUID{a, b}); err != nil || alive[a] || alive[b] {
		t.Fatalf("nothing leased yet: %v %v", alive, err)
	}
	if err := st.Put(ctx, "owner-1", []uuid.UUID{a, b}, 3*time.Second); err != nil {
		t.Fatal(err)
	}
	if alive, _ := st.Alive(ctx, []uuid.UUID{a, b}); !alive[a] || !alive[b] {
		t.Fatalf("after Put: %v", alive)
	}
	// Another owner cannot drop a lease it does not hold.
	if err := st.Drop(ctx, "owner-2", []uuid.UUID{a}); err != nil {
		t.Fatal(err)
	}
	if alive, _ := st.Alive(ctx, []uuid.UUID{a}); !alive[a] {
		t.Fatal("a foreign Drop removed the lease")
	}
	if err := st.Drop(ctx, "owner-1", []uuid.UUID{a}); err != nil {
		t.Fatal(err)
	}
	if alive, _ := st.Alive(ctx, []uuid.UUID{a, b}); alive[a] || !alive[b] {
		t.Fatalf("after the owner's Drop: %v", alive)
	}
	// A lease that is not renewed expires.
	if err := st.Put(ctx, "owner-1", []uuid.UUID{b}, time.Second); err != nil {
		t.Fatal(err)
	}
	time.Sleep(1500 * time.Millisecond)
	if alive, _ := st.Alive(ctx, []uuid.UUID{b}); alive[b] {
		t.Fatal("an unrenewed lease did not expire")
	}
	// RunLeases on top: acquire writes, release deletes.
	l := NewRunLeases(st, "owner-3", 3*time.Second, time.Second)
	rel := l.Acquire(a)
	if alive, _ := st.Alive(ctx, []uuid.UUID{a}); !alive[a] {
		t.Fatal("Acquire did not write the lease")
	}
	rel()
	rel() // idempotent
	if alive, _ := st.Alive(ctx, []uuid.UUID{a}); alive[a] || l.Count() != 0 {
		t.Fatal("release did not drop the lease")
	}
}
