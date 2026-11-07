package service

// FIX41 chunk 2 — the run ledger that outlives the process.
//
// WHY REDIS, AND WHY OFF THE HOT PATH. Four options were measured before
// anything was written (FIX41's E1 ledger-backends probe,
// 404-frame turn, budget: +15 ms p95 per turn):
//
//   L4  no ledger                          0 us/turn   resume after restart: NO
//   L1  in-process slice (chunk 1)      8.22 us/turn   resume after restart: NO
//   L2a Redis, one RPUSH per frame    324937 us/turn   resume after restart: yes
//   L2b Redis, pipelined behind the run 17.18 us/turn  resume after restart: yes
//   L3  Postgres, one INSERT per frame 424829 us/turn  resume after restart: yes
//
// L2a and L3 miss the budget by more than twenty times. That is the trap
// worth naming: a per-frame cost of ~1 ms reads as free and is 400 ms once
// a turn emits 400 deltas. The unit that matters is the turn.
//
// So: the in-memory ledger stays the read path for a live process (8 us),
// and a background writer mirrors the same bytes into Redis behind it
// (17 us of channel send). Only a process that has LOST its memory — a
// restart, or another replica — reads Redis back.
//
// WHAT IS STORED. Exactly the bytes that went out over SSE, which have
// already been through the gateway's DLP masking. chat-edge does no
// masking of its own and must never hold a rawer copy than the client
// does; this store is byte-identical to the wire by construction, because
// the writer is handed the same slice writeSSEEvent sent.
//
// WHAT IS NOT PROMISED. The generation itself does not survive the
// restart — the process that held the upstream connection is gone, and
// keeping it running would be a background run, which FIX41 deliberately
// does not build. What survives is the ANSWER SO FAR, in order, followed
// by an honest terminal saying it was interrupted.

import (
	"context"
	"encoding/json"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// RunStore persists a run's frames so a different process can replay them.
type RunStore interface {
	// Append records one frame. It MUST NOT block the caller: a turn is
	// never made slower by bookkeeping.
	Append(msgID uuid.UUID, ev LedgerEvent)
	// Load returns the stored frames for msgID, oldest first.
	Load(ctx context.Context, msgID uuid.UUID) ([]LedgerEvent, bool)
	// Forget drops a run's stored frames.
	Forget(msgID uuid.UUID)
	Close()
}

type redisRunStore struct {
	rdb       *redis.Client
	ttl       time.Duration
	maxEvents int64

	in   chan storedFrame
	done chan struct{}
	once sync.Once

	// dropped runs are ones whose Redis copy is known to be incomplete
	// (the writer's queue overflowed). Their key is deleted rather than
	// left as a partial ledger, because a ledger with an invisible hole
	// is worse than no ledger: the client would be shown a truncated
	// answer with nothing to indicate the gap.
	mu      sync.Mutex
	dropped map[uuid.UUID]bool
}

type storedFrame struct {
	msgID uuid.UUID
	ev    LedgerEvent
}

// ledgerOverflowDrops counts runs whose durable copy was thrown away
// because the writer's queue was full (STREAM_CONTRACT.md §8.1 case 2).
// Process-wide, monotonic, reset only by a restart. It exists so the
// overflow is COUNTABLE — before it, the only trace was one slog.Warn
// line per run, which nobody watches. Read through RunLedgerOverflowDrops
// and surfaced on /health as run_ledger_overflow_drops. Counting is the
// whole change: the drop itself behaves exactly as before.
var ledgerOverflowDrops atomic.Int64

// RunLedgerOverflowDrops reports how many runs lost their Redis copy to
// writer overflow since the process started.
func RunLedgerOverflowDrops() int64 { return ledgerOverflowDrops.Load() }

// runLedgerQueue is the writer's backlog. Sized for tens of concurrent
// turns at full tilt; overflow drops the run's Redis copy rather than
// slowing the turn down or storing something with a hole in it.
const runLedgerQueue = 16384

// NewRedisRunStore starts the background writer. Close stops it.
func NewRedisRunStore(rdb *redis.Client, ttl time.Duration, maxEvents int) RunStore {
	if rdb == nil {
		return nil
	}
	if ttl <= 0 {
		ttl = ledgerDefaultTTL
	}
	if maxEvents <= 0 {
		maxEvents = ledgerDefaultMaxEvents
	}
	s := &redisRunStore{
		rdb:       rdb,
		ttl:       ttl,
		maxEvents: int64(maxEvents),
		in:        make(chan storedFrame, runLedgerQueue),
		done:      make(chan struct{}),
		dropped:   make(map[uuid.UUID]bool),
	}
	go s.run()
	return s
}

func runLedgerKey(msgID uuid.UUID) string { return "chat:runledger:" + msgID.String() }

func (s *redisRunStore) Append(msgID uuid.UUID, ev LedgerEvent) {
	s.mu.Lock()
	skip := s.dropped[msgID]
	s.mu.Unlock()
	if skip {
		return
	}
	select {
	case s.in <- storedFrame{msgID: msgID, ev: ev}:
	default:
		// The writer is behind. Do NOT block the turn, and do not keep a
		// partial ledger: mark the run and delete what is there.
		s.mu.Lock()
		s.dropped[msgID] = true
		s.mu.Unlock()
		n := ledgerOverflowDrops.Add(1)
		slog.Warn("run ledger writer overflowed; dropping this run's durable copy",
			"message_id", msgID.String(), "overflow_drops_total", n)
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			_ = s.rdb.Del(ctx, runLedgerKey(msgID)).Err()
		}()
	}
}

func (s *redisRunStore) run() {
	pipe := s.rdb.Pipeline()
	queued := 0
	touched := map[uuid.UUID]bool{}
	flush := func() {
		if queued == 0 {
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		for id := range touched {
			key := runLedgerKey(id)
			// Keep the newest maxEvents frames and refresh the window.
			pipe.LTrim(ctx, key, -s.maxEvents, -1)
			pipe.Expire(ctx, key, s.ttl)
			delete(touched, id)
		}
		if _, err := pipe.Exec(ctx); err != nil && err != redis.Nil {
			slog.Warn("run ledger flush failed", "error", err)
		}
		cancel()
		queued = 0
	}
	tick := time.NewTicker(25 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case f, ok := <-s.in:
			if !ok {
				flush()
				close(s.done)
				return
			}
			b, err := json.Marshal(f.ev)
			if err != nil {
				continue
			}
			pipe.RPush(context.Background(), runLedgerKey(f.msgID), b)
			touched[f.msgID] = true
			queued++
			if queued >= 64 {
				flush()
			}
		case <-tick.C:
			flush()
		}
	}
}

func (s *redisRunStore) Load(ctx context.Context, msgID uuid.UUID) ([]LedgerEvent, bool) {
	raw, err := s.rdb.LRange(ctx, runLedgerKey(msgID), 0, -1).Result()
	if err != nil || len(raw) == 0 {
		return nil, false
	}
	out := make([]LedgerEvent, 0, len(raw))
	for _, r := range raw {
		var ev LedgerEvent
		if json.Unmarshal([]byte(r), &ev) != nil {
			continue
		}
		out = append(out, ev)
	}
	if len(out) == 0 {
		return nil, false
	}
	return out, true
}

func (s *redisRunStore) Forget(msgID uuid.UUID) {
	s.mu.Lock()
	delete(s.dropped, msgID)
	s.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = s.rdb.Del(ctx, runLedgerKey(msgID)).Err()
}

func (s *redisRunStore) Close() {
	s.once.Do(func() {
		close(s.in)
		select {
		case <-s.done:
		case <-time.After(3 * time.Second):
		}
	})
}
