package service

// FIX41 chunk 2 — the durable half of the run ledger.
//
// Skips cleanly when redis-webchat is not up: the mirror is an
// optimisation over chunk 1, and a box without Redis must still run the
// suite rather than fail on a dependency it does not have.

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

func testRedis(t *testing.T) *redis.Client {
	t.Helper()
	url := os.Getenv("FIX41_BENCH_REDIS_URL")
	if url == "" {
		url = "redis://localhost:6380/1"
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		testdeps.Missing(t, "bad redis url: %v", err)
	}
	c := redis.NewClient(opts)
	if err := c.Ping(context.Background()).Err(); err != nil {
		_ = c.Close()
		testdeps.Missing(t, "redis unreachable: %v", err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}

func frameFor(runID string, seq int64, event string) LedgerEvent {
	return LedgerEvent{
		Seq:   seq,
		Event: event,
		Data:  json.RawMessage(fmt.Sprintf(`{"run_id":%q,"seq":%d,"delta":"x"}`, runID, seq)),
	}
}

// A run recorded by one broker must be replayable by a DIFFERENT one —
// that is the whole point: the second broker stands for the process that
// came up after the restart.
func TestRunLedger_SurvivesAProcessThatIsGone(t *testing.T) {
	rdb := testRedis(t)
	msgID := uuid.New()
	runID := uuid.NewString()

	first := NewStreamBroker()
	store := NewRedisRunStore(rdb, time.Minute, 100)
	first.SetRunStore(store)
	first.OpenRun(msgID, runID)
	for i := 1; i <= 5; i++ {
		ev := "content_delta"
		if i == 1 {
			ev = "message_start"
		}
		i := i
		first.Record(msgID, ev, func(seq, final int64) []byte {
			return frameFor(runID, int64(i), ev).Data
		})
	}
	store.Close() // flush, as a graceful shutdown would
	t.Cleanup(func() { NewRedisRunStore(rdb, time.Minute, 100).Forget(msgID) })

	// The restarted process: new broker, empty memory, same Redis.
	second := NewStreamBroker()
	second.SetRunStore(NewRedisRunStore(rdb, time.Minute, 100))
	if _, _, ok := second.SubscribeFrames(msgID, 0); ok {
		t.Fatal("the new broker must not claim to hold a run it never saw")
	}
	att, err, ok := second.LoadStoredFrames(context.Background(), msgID, 0)
	if !ok || err != nil {
		t.Fatalf("LoadStoredFrames ok=%v err=%v", ok, err)
	}
	if len(att.Replay) != 5 {
		t.Fatalf("replayed %d frames, want 5", len(att.Replay))
	}
	for i, le := range att.Replay {
		if le.Seq != int64(i+1) {
			t.Errorf("frame %d has seq %d, want %d", i, le.Seq, i+1)
		}
	}
	if att.RunID != runID {
		t.Errorf("run id = %q, want %q", att.RunID, runID)
	}
	if !att.Interrupted {
		t.Error("a run with no terminal frame must come back marked Interrupted — otherwise the handler ends it on message_complete and shows a truncated answer as a finished one")
	}
	if att.Live != nil {
		t.Error("the generation cannot continue in a process that never had it")
	}
}

func TestRunLedger_TerminatedRunIsNotMarkedInterrupted(t *testing.T) {
	rdb := testRedis(t)
	msgID := uuid.New()
	runID := uuid.NewString()

	b := NewStreamBroker()
	store := NewRedisRunStore(rdb, time.Minute, 100)
	b.SetRunStore(store)
	b.OpenRun(msgID, runID)
	b.Record(msgID, "message_start", func(seq, final int64) []byte { return frameFor(runID, 1, "message_start").Data })
	b.Record(msgID, "message_complete", func(seq, final int64) []byte { return frameFor(runID, 2, "message_complete").Data })
	store.Close()
	t.Cleanup(func() { NewRedisRunStore(rdb, time.Minute, 100).Forget(msgID) })

	other := NewStreamBroker()
	other.SetRunStore(NewRedisRunStore(rdb, time.Minute, 100))
	att, err, ok := other.LoadStoredFrames(context.Background(), msgID, 0)
	if !ok || err != nil {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
	if att.Interrupted {
		t.Error("a run that ended properly must not be reported as interrupted")
	}
	if att.FinalSeq != 2 {
		t.Errorf("FinalSeq = %d, want 2", att.FinalSeq)
	}
}

// The cap drops the OLDEST frames. A cursor pointing into the dropped
// region must be refused, not quietly under-served.
func TestRunLedger_StoredCapReportsTooOld(t *testing.T) {
	rdb := testRedis(t)
	msgID := uuid.New()
	runID := uuid.NewString()

	b := NewStreamBroker()
	store := NewRedisRunStore(rdb, time.Minute, 3)
	b.SetRunStore(store)
	b.OpenRun(msgID, runID)
	for i := 1; i <= 10; i++ {
		i := i
		b.Record(msgID, "content_delta", func(seq, final int64) []byte {
			return frameFor(runID, int64(i), "content_delta").Data
		})
	}
	store.Close()
	t.Cleanup(func() { NewRedisRunStore(rdb, time.Minute, 3).Forget(msgID) })

	other := NewStreamBroker()
	other.SetRunStore(NewRedisRunStore(rdb, time.Minute, 3))
	if _, err, ok := other.LoadStoredFrames(context.Background(), msgID, 2); !ok || err == nil {
		t.Fatalf("cursor inside the dropped region: ok=%v err=%v, want a too-old error", ok, err)
	}
	att, err, ok := other.LoadStoredFrames(context.Background(), msgID, 7)
	if !ok || err != nil {
		t.Fatalf("cursor at the boundary: ok=%v err=%v", ok, err)
	}
	if len(att.Replay) != 3 || att.Replay[0].Seq != 8 {
		t.Errorf("replay = %d frames from seq %d, want 3 from 8", len(att.Replay), att.Replay[0].Seq)
	}
}

func TestRunLedger_ForgetRemovesTheRun(t *testing.T) {
	rdb := testRedis(t)
	msgID := uuid.New()
	runID := uuid.NewString()
	b := NewStreamBroker()
	store := NewRedisRunStore(rdb, time.Minute, 100)
	b.SetRunStore(store)
	b.OpenRun(msgID, runID)
	b.Record(msgID, "message_start", func(seq, final int64) []byte { return frameFor(runID, 1, "message_start").Data })
	store.Close()

	b.ForgetStoredRun(msgID)
	if _, _, ok := b.LoadStoredFrames(context.Background(), msgID, 0); ok {
		t.Error("a forgotten run must not come back")
	}
}

// Without a store the broker behaves exactly as chunk 1 did. A deployment
// with no Redis must degrade, not break.
func TestRunLedger_NoStoreIsNotAnError(t *testing.T) {
	b := NewStreamBroker()
	if _, err, ok := b.LoadStoredFrames(context.Background(), uuid.New(), 0); ok || err != nil {
		t.Errorf("no store: ok=%v err=%v, want (false, nil)", ok, err)
	}
	b.ForgetStoredRun(uuid.New()) // must not panic
}

// FIX42 D1 — the overflow is counted, once per run, and the run is
// marked so later frames do not keep hitting the full queue. The store is
// built by hand with an unbuffered channel and NO writer goroutine, so the
// first Append overflows deterministically; no Redis is needed (the DEL
// that follows a drop is fire-and-forget and its error is discarded).
func TestRunLedger_OverflowIsCountedOncePerRun(t *testing.T) {
	s := &redisRunStore{
		rdb:       redis.NewClient(&redis.Options{Addr: "127.0.0.1:1"}),
		ttl:       time.Minute,
		maxEvents: 10,
		in:        make(chan storedFrame),
		done:      make(chan struct{}),
		dropped:   make(map[uuid.UUID]bool),
	}
	before := RunLedgerOverflowDrops()
	a, b := uuid.New(), uuid.New()

	s.Append(a, frameFor("run-a", 1, "content_delta"))
	if got := RunLedgerOverflowDrops() - before; got != 1 {
		t.Fatalf("first overflow: counter advanced by %d, want 1", got)
	}
	s.Append(a, frameFor("run-a", 2, "content_delta"))
	if got := RunLedgerOverflowDrops() - before; got != 1 {
		t.Fatalf("same run again: counter advanced by %d, want still 1 (run is marked dropped)", got)
	}
	s.Append(b, frameFor("run-b", 1, "content_delta"))
	if got := RunLedgerOverflowDrops() - before; got != 2 {
		t.Fatalf("second run: counter advanced by %d, want 2", got)
	}
	s.mu.Lock()
	da, db := s.dropped[a], s.dropped[b]
	s.mu.Unlock()
	if !da || !db {
		t.Fatalf("dropped marks: a=%v b=%v, want both true", da, db)
	}
}
