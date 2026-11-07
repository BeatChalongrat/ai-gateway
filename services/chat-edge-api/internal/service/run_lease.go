package service

// FIX131 E1 — who owns a streaming turn right now.
//
// A turn's assistant row says 'streaming' from the moment it is inserted
// until the finish callback of the process running it writes the ending.
// If that process dies first, the row says 'streaming' for good; the
// interrupted-turn sweep (interrupted_sweep.go) exists to settle such rows.
// The one thing the sweep must never do is settle a turn some process is
// still running — a second chat-edge instance, or this one after a slow
// start, would otherwise close a live turn under the person reading it.
//
// So every running turn holds a LEASE: a short-lived key in Redis that
// names the process (instance id) and is renewed while the turn runs. A
// row is only ever settled by the sweep when no lease exists for it.
//
//   - Acquired right after the streaming row is written (or, for a parked
//     question that streams again, BEFORE its row flips back to streaming),
//     released by the turn's release — after the finish callback has
//     written the row.
//   - Renewed by one loop per process (RenewEvery), with SET … EX, not
//     EXPIRE: a Redis that lost its keys (restart without persistence)
//     gets them back on the next renewal, and the sweep's two-observation
//     rule (interrupted_sweep.go) is sized so a live owner always renews
//     in between.
//   - Redis down: Acquire keeps the lease locally and the renewal writes it
//     once Redis answers; Alive reports the error and the sweep does
//     nothing that pass. A lease that cannot be checked is treated as held.
//
// Vendor practice, for the record: a background/streamed response on the
// big platforms is owned by a worker that heartbeats it; a response whose
// worker stopped heartbeating is marked incomplete/interrupted, never left
// "in progress". This is that, sized for one or a few instances.

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"os"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// Lease defaults. TTL is how long a lease outlives its last renewal —
// how long a crashed process's turns stay protected before the sweep may
// consider them. RenewEvery must be well under TTL.
const (
	RunLeaseDefaultTTL        = 45 * time.Second
	RunLeaseDefaultRenewEvery = 15 * time.Second
	runLeaseKeyPrefix         = "chat:runlease:"
)

// LeaseStore is where leases live. Redis in production (NewRedisLeaseStore),
// an in-memory map in tests.
type LeaseStore interface {
	// Put writes (or refreshes) a lease for every id, owned by owner, for ttl.
	Put(ctx context.Context, owner string, ids []uuid.UUID, ttl time.Duration) error
	// Drop removes the lease of every id that is still owned by owner.
	Drop(ctx context.Context, owner string, ids []uuid.UUID) error
	// Alive reports which of ids carry a live lease, whoever owns it.
	Alive(ctx context.Context, ids []uuid.UUID) (map[uuid.UUID]bool, error)
}

// RunLeases is this process's set of held leases plus the renewal loop.
// All methods are nil-safe: a nil *RunLeases holds nothing and reports
// nothing alive, which is the pre-FIX131 behaviour a direct-constructed
// test service keeps.
type RunLeases struct {
	store      LeaseStore
	owner      string
	ttl        time.Duration
	renewEvery time.Duration

	mu   sync.Mutex
	held map[uuid.UUID]struct{}
}

// NewRunLeases builds the lease set. owner "" mints an instance id
// (hostname + random suffix: two processes never share one, even on one
// host).
func NewRunLeases(store LeaseStore, owner string, ttl, renewEvery time.Duration) *RunLeases {
	if owner == "" {
		owner = NewInstanceID()
	}
	if ttl <= 0 {
		ttl = RunLeaseDefaultTTL
	}
	if renewEvery <= 0 || renewEvery >= ttl {
		renewEvery = ttl / 3
	}
	return &RunLeases{store: store, owner: owner, ttl: ttl, renewEvery: renewEvery, held: map[uuid.UUID]struct{}{}}
}

// NewInstanceID names this process for lease ownership.
func NewInstanceID() string {
	host, _ := os.Hostname()
	if host == "" {
		host = "chat-edge"
	}
	var b [4]byte
	_, _ = rand.Read(b[:])
	return host + "-" + hex.EncodeToString(b[:])
}

// Owner is this process's instance id.
func (l *RunLeases) Owner() string {
	if l == nil {
		return ""
	}
	return l.owner
}

// TTL / RenewEvery expose the timing the sweep sizes itself against.
func (l *RunLeases) TTL() time.Duration {
	if l == nil {
		return 0
	}
	return l.ttl
}

func (l *RunLeases) RenewEvery() time.Duration {
	if l == nil {
		return 0
	}
	return l.renewEvery
}

// Acquire takes the lease for one turn's row and returns its release
// (idempotent). The write to the store is best effort: when it fails the
// lease is still held locally and the next renewal writes it.
func (l *RunLeases) Acquire(msgID uuid.UUID) (release func()) {
	if l == nil {
		return func() {}
	}
	l.mu.Lock()
	l.held[msgID] = struct{}{}
	l.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	if err := l.store.Put(ctx, l.owner, []uuid.UUID{msgID}, l.ttl); err != nil {
		slog.Warn("run lease: acquire not written; the next renewal retries", "message_id", msgID.String(), "err", err.Error())
	}
	cancel()
	var once sync.Once
	return func() {
		once.Do(func() { l.Release(msgID) })
	}
}

// Release drops the lease for msgID (no-op when not held).
func (l *RunLeases) Release(msgID uuid.UUID) {
	if l == nil {
		return
	}
	l.mu.Lock()
	_, ok := l.held[msgID]
	delete(l.held, msgID)
	l.mu.Unlock()
	if !ok {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := l.store.Drop(ctx, l.owner, []uuid.UUID{msgID}); err != nil {
		// Harmless: the key expires after TTL on its own.
		slog.Warn("run lease: release not written; the lease expires on its own", "message_id", msgID.String(), "err", err.Error())
	}
}

// HeldLocally reports whether THIS process holds msgID's lease.
func (l *RunLeases) HeldLocally(msgID uuid.UUID) bool {
	if l == nil {
		return false
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	_, ok := l.held[msgID]
	return ok
}

// Held lists the leases this process holds.
func (l *RunLeases) Held() []uuid.UUID {
	if l == nil {
		return nil
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([]uuid.UUID, 0, len(l.held))
	for id := range l.held {
		out = append(out, id)
	}
	return out
}

// Count is how many leases this process holds.
func (l *RunLeases) Count() int {
	if l == nil {
		return 0
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.held)
}

// Alive reports, for each id, whether ANY process holds its lease. The
// ones this process holds are answered locally. A store error is returned
// as is: the caller must then treat every id as held.
func (l *RunLeases) Alive(ctx context.Context, ids []uuid.UUID) (map[uuid.UUID]bool, error) {
	out := make(map[uuid.UUID]bool, len(ids))
	if l == nil {
		return out, nil
	}
	var remote []uuid.UUID
	for _, id := range ids {
		if l.HeldLocally(id) {
			out[id] = true
			continue
		}
		remote = append(remote, id)
	}
	if len(remote) == 0 {
		return out, nil
	}
	alive, err := l.store.Alive(ctx, remote)
	if err != nil {
		return nil, err
	}
	for id, ok := range alive {
		if ok {
			out[id] = true
		}
	}
	return out, nil
}

// RenewOnce rewrites every held lease with a fresh TTL.
func (l *RunLeases) RenewOnce(ctx context.Context) error {
	if l == nil {
		return nil
	}
	ids := l.Held()
	if len(ids) == 0 {
		return nil
	}
	wctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	return l.store.Put(wctx, l.owner, ids, l.ttl)
}

// Run renews the held leases every RenewEvery until ctx ends. It must keep
// running while a shutdown drains turns — main.go gives it a context that
// ends after the drain, not the background one that ends when it starts.
func (l *RunLeases) Run(ctx context.Context) {
	if l == nil {
		return
	}
	t := time.NewTicker(l.renewEvery)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := l.RenewOnce(ctx); err != nil && ctx.Err() == nil {
				slog.Warn("run lease: renewal failed; held leases may expire", "held", l.Count(), "err", err.Error())
			}
		}
	}
}

// ReleaseAll drops every held lease (the end of a shutdown).
func (l *RunLeases) ReleaseAll() {
	for _, id := range l.Held() {
		l.Release(id)
	}
}

// ---------------------------------------------------------------------------
// Redis store
// ---------------------------------------------------------------------------

type redisLeaseStore struct {
	rdb    *redis.Client
	prefix string
}

// NewRedisLeaseStore keeps leases under prefix+<message id> ("" = the
// production prefix chat:runlease:). Tests pass a prefix of their own.
func NewRedisLeaseStore(rdb *redis.Client, prefix string) LeaseStore {
	if prefix == "" {
		prefix = runLeaseKeyPrefix
	}
	return &redisLeaseStore{rdb: rdb, prefix: prefix}
}

func (s *redisLeaseStore) key(id uuid.UUID) string { return s.prefix + id.String() }

func (s *redisLeaseStore) Put(ctx context.Context, owner string, ids []uuid.UUID, ttl time.Duration) error {
	if len(ids) == 0 {
		return nil
	}
	pipe := s.rdb.Pipeline()
	for _, id := range ids {
		pipe.Set(ctx, s.key(id), owner, ttl)
	}
	_, err := pipe.Exec(ctx)
	return err
}

// dropIfOwner deletes a lease only when this owner still holds it, so a
// late release can never remove a lease another process took since.
var dropIfOwner = redis.NewScript(`if redis.call("GET", KEYS[1]) == ARGV[1] then return redis.call("DEL", KEYS[1]) else return 0 end`)

func (s *redisLeaseStore) Drop(ctx context.Context, owner string, ids []uuid.UUID) error {
	for _, id := range ids {
		if err := dropIfOwner.Run(ctx, s.rdb, []string{s.key(id)}, owner).Err(); err != nil && err != redis.Nil {
			return err
		}
	}
	return nil
}

func (s *redisLeaseStore) Alive(ctx context.Context, ids []uuid.UUID) (map[uuid.UUID]bool, error) {
	out := make(map[uuid.UUID]bool, len(ids))
	if len(ids) == 0 {
		return out, nil
	}
	keys := make([]string, len(ids))
	for i, id := range ids {
		keys[i] = s.key(id)
	}
	vals, err := s.rdb.MGet(ctx, keys...).Result()
	if err != nil {
		return nil, err
	}
	for i, v := range vals {
		out[ids[i]] = v != nil
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// In-memory store (tests, and a deployment with no Redis at all)
// ---------------------------------------------------------------------------

// MemoryLeaseStore is a LeaseStore in process memory. It only protects
// turns of this process, which is all a single instance without Redis has.
type MemoryLeaseStore struct {
	mu   sync.Mutex
	now  func() time.Time
	keys map[uuid.UUID]memLease
	// Err, when set, is returned by every call (a Redis outage).
	Err error
}

type memLease struct {
	owner string
	until time.Time
}

func NewMemoryLeaseStore() *MemoryLeaseStore {
	return &MemoryLeaseStore{now: time.Now, keys: map[uuid.UUID]memLease{}}
}

// SetClock is a test hook.
func (m *MemoryLeaseStore) SetClock(now func() time.Time) { m.mu.Lock(); m.now = now; m.mu.Unlock() }

// Flush drops every lease (a Redis that restarted without persistence).
func (m *MemoryLeaseStore) Flush() { m.mu.Lock(); m.keys = map[uuid.UUID]memLease{}; m.mu.Unlock() }

// PutForeign writes a lease owned by another process (tests).
func (m *MemoryLeaseStore) PutForeign(id uuid.UUID, owner string, ttl time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.keys[id] = memLease{owner: owner, until: m.now().Add(ttl)}
}

func (m *MemoryLeaseStore) Put(_ context.Context, owner string, ids []uuid.UUID, ttl time.Duration) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.Err != nil {
		return m.Err
	}
	for _, id := range ids {
		m.keys[id] = memLease{owner: owner, until: m.now().Add(ttl)}
	}
	return nil
}

func (m *MemoryLeaseStore) Drop(_ context.Context, owner string, ids []uuid.UUID) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.Err != nil {
		return m.Err
	}
	for _, id := range ids {
		if l, ok := m.keys[id]; ok && l.owner == owner {
			delete(m.keys, id)
		}
	}
	return nil
}

func (m *MemoryLeaseStore) Alive(_ context.Context, ids []uuid.UUID) (map[uuid.UUID]bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.Err != nil {
		return nil, m.Err
	}
	out := make(map[uuid.UUID]bool, len(ids))
	now := m.now()
	for _, id := range ids {
		l, ok := m.keys[id]
		out[id] = ok && now.Before(l.until)
	}
	return out, nil
}
