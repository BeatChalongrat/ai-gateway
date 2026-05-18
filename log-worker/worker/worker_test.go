package worker

import (
	"database/sql"
	"database/sql/driver"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	pb "github.com/beatchalongrat/ai-gateway/log-worker/pb"
)

// errFakeTransient is the sentinel insertFn stubs return to simulate
// a Postgres outage that the retry path should absorb (or eventually
// give up on).
var errFakeTransient = errors.New("fake transient error")

// ---------------------------------------------------------------------------
// Stub sql/driver so tests run without a live Postgres. We can't easily
// intercept the COPY protocol (it's lib/pq specific and not part of the
// driver.Driver interface), so the tests below exercise the worker's
// channel + flush triggers using a stubbed db.InsertBatch — see
// worker_test_hook.go which re-points the inserter for tests.
// ---------------------------------------------------------------------------

type fakeConn struct{}

func (fakeConn) Prepare(_ string) (driver.Stmt, error) { return fakeStmt{}, nil }
func (fakeConn) Close() error                          { return nil }
func (fakeConn) Begin() (driver.Tx, error)             { return fakeTx{}, nil }

type fakeStmt struct{}

func (fakeStmt) Close() error                                   { return nil }
func (fakeStmt) NumInput() int                                  { return -1 }
func (fakeStmt) Exec(_ []driver.Value) (driver.Result, error)   { return driver.RowsAffected(0), nil }
func (fakeStmt) Query(_ []driver.Value) (driver.Rows, error)    { return nil, nil }

type fakeTx struct{}

func (fakeTx) Commit() error   { return nil }
func (fakeTx) Rollback() error { return nil }

type fakeDriver struct{}

func (fakeDriver) Open(_ string) (driver.Conn, error) { return fakeConn{}, nil }

func init() {
	sql.Register("fake-pq", fakeDriver{})
}

func newTestWorker(t *testing.T, cfg Config) *Worker {
	t.Helper()
	db, err := sql.Open("fake-pq", "")
	if err != nil {
		t.Fatalf("open fake-pq: %v", err)
	}
	if cfg.DB == nil {
		cfg.DB = db
	}
	w := New(cfg)
	t.Cleanup(func() { _ = db.Close() })
	return w
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

// TestEnqueueAcceptsBelowBuffer verifies that Enqueue returns true
// for the first N records when N ≤ BufferSize.
func TestEnqueueAcceptsBelowBuffer(t *testing.T) {
	w := newTestWorker(t, Config{BufferSize: 100, BatchSize: 100, FlushInterval: time.Hour})
	for i := 0; i < 50; i++ {
		if !w.Enqueue(&pb.LogRecord{RequestId: "x"}) {
			t.Fatalf("expected accepted at iter %d", i)
		}
	}
	depth, dropped, _ := w.Stats()
	if depth != 50 {
		t.Errorf("queueDepth = %d, want 50", depth)
	}
	if dropped != 0 {
		t.Errorf("dropped = %d, want 0", dropped)
	}
}

// TestEnqueueDropsOldestWhenFull verifies the drop-oldest fallback
// kicks in once BufferSize is reached.
func TestEnqueueDropsOldestWhenFull(t *testing.T) {
	w := newTestWorker(t, Config{BufferSize: 3, BatchSize: 100, FlushInterval: time.Hour})
	for i := 0; i < 5; i++ {
		w.Enqueue(&pb.LogRecord{RequestId: "x"})
	}
	depth, dropped, _ := w.Stats()
	if depth != 3 {
		t.Errorf("queueDepth = %d, want 3", depth)
	}
	if dropped < 2 {
		t.Errorf("dropped = %d, want ≥2", dropped)
	}
}

// TestStopOnClosedWorkerIsNoop confirms the second Stop call doesn't
// panic — common pattern when the SIGTERM handler races with an
// explicit Stop().
func TestStopOnClosedWorkerIsNoop(t *testing.T) {
	w := newTestWorker(t, Config{BufferSize: 10, BatchSize: 10, FlushInterval: time.Hour})
	w.Start()
	w.Stop(time.Second)
	w.Stop(time.Second) // must not panic
}

// TestBatchSizeFlushTrigger asserts that batch flushes when the
// buffered count reaches BatchSize, irrespective of FlushInterval.
// We use a tiny ticker interval but a much smaller BatchSize, and
// verify that the queue drains via the size trigger (not the
// timer) by enqueueing exactly BatchSize records in a tight loop.
func TestBatchSizeFlushTrigger(t *testing.T) {
	flushCalls := atomic.Int32{}
	stub := stubInsert(&flushCalls)
	defer stub.restore()

	w := newTestWorker(t, Config{BufferSize: 100, BatchSize: 5, FlushInterval: time.Hour})
	w.Start()
	defer w.Stop(time.Second)

	for i := 0; i < 5; i++ {
		w.Enqueue(&pb.LogRecord{RequestId: "x"})
	}

	// Wait up to 500ms for the size-triggered flush.
	deadline := time.Now().Add(500 * time.Millisecond)
	for time.Now().Before(deadline) {
		if flushCalls.Load() >= 1 {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if flushCalls.Load() < 1 {
		t.Fatalf("flush did not fire; calls=%d", flushCalls.Load())
	}
}

// TestTickerFlushTrigger verifies that a record below the batch
// threshold still flushes within ~FlushInterval. Uses a short
// interval so the test stays under 200ms.
func TestTickerFlushTrigger(t *testing.T) {
	flushCalls := atomic.Int32{}
	stub := stubInsert(&flushCalls)
	defer stub.restore()

	w := newTestWorker(t, Config{
		BufferSize:    100,
		BatchSize:     100,
		FlushInterval: 50 * time.Millisecond,
	})
	w.Start()
	defer w.Stop(time.Second)

	w.Enqueue(&pb.LogRecord{RequestId: "x"})

	// Wait up to 300ms — flush should fire within the first interval.
	deadline := time.Now().Add(300 * time.Millisecond)
	for time.Now().Before(deadline) {
		if flushCalls.Load() >= 1 {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if flushCalls.Load() < 1 {
		t.Fatalf("ticker flush did not fire; calls=%d", flushCalls.Load())
	}
}

// TestFlushRetriesOnError exercises the audit-fix retry-with-backoff
// path: two failures then a success should result in three insertFn
// calls total and zero dropped batches.
func TestFlushRetriesOnError(t *testing.T) {
	// Shrink the backoff so the test runs in milliseconds, not the
	// real ~600ms total. We swap in tiny delays then restore so
	// sibling tests are unaffected.
	origBackoffs := flushBackoffs
	flushBackoffs = []time.Duration{
		1 * time.Millisecond,
		1 * time.Millisecond,
	}
	defer func() { flushBackoffs = origBackoffs }()

	calls := atomic.Int32{}
	stub := stubInsertWithBehavior(&calls, func(n int32) error {
		// Fail the first two attempts, then succeed.
		if n < 3 {
			return errFakeTransient
		}
		return nil
	})
	defer stub.restore()

	w := newTestWorker(t, Config{
		BufferSize:    100,
		BatchSize:     1,
		FlushInterval: 50 * time.Millisecond,
	})
	w.Start()
	defer w.Stop(time.Second)

	w.Enqueue(&pb.LogRecord{RequestId: "retry-me"})

	deadline := time.Now().Add(500 * time.Millisecond)
	for time.Now().Before(deadline) {
		if calls.Load() >= 3 {
			break
		}
		time.Sleep(2 * time.Millisecond)
	}

	if got := calls.Load(); got != 3 {
		t.Fatalf("expected 3 insertFn calls (2 retries + 1 success), got %d", got)
	}
	if _, _, flushed := w.Stats(); flushed != 1 {
		t.Errorf("expected 1 record flushed after retry success, got %d", flushed)
	}
}

// TestFlushDropsAfterMaxRetries verifies the batch is dropped once
// the retry budget is exhausted — the worker keeps running, the
// records are simply lost.
func TestFlushDropsAfterMaxRetries(t *testing.T) {
	origBackoffs := flushBackoffs
	flushBackoffs = []time.Duration{
		1 * time.Millisecond,
		1 * time.Millisecond,
	}
	defer func() { flushBackoffs = origBackoffs }()

	calls := atomic.Int32{}
	stub := stubInsertWithBehavior(&calls, func(_ int32) error {
		return errFakeTransient
	})
	defer stub.restore()

	w := newTestWorker(t, Config{
		BufferSize:    100,
		BatchSize:     1,
		FlushInterval: 50 * time.Millisecond,
	})
	w.Start()
	defer w.Stop(time.Second)

	w.Enqueue(&pb.LogRecord{RequestId: "doomed"})

	deadline := time.Now().Add(500 * time.Millisecond)
	for time.Now().Before(deadline) {
		if calls.Load() >= int32(1+len(flushBackoffs)) {
			break
		}
		time.Sleep(2 * time.Millisecond)
	}

	wantCalls := int32(1 + len(flushBackoffs)) // initial + retries
	if got := calls.Load(); got != wantCalls {
		t.Fatalf("expected %d insertFn calls, got %d", wantCalls, got)
	}
	if _, _, flushed := w.Stats(); flushed != 0 {
		t.Errorf("expected 0 flushed after retry exhaustion, got %d", flushed)
	}
}

// TestGracefulShutdownDrains verifies pending records are flushed
// before Stop() returns.
func TestGracefulShutdownDrains(t *testing.T) {
	flushed := atomic.Int32{}
	stub := stubInsert(&flushed)
	defer stub.restore()

	w := newTestWorker(t, Config{
		BufferSize:    100,
		BatchSize:     100,        // size won't trigger
		FlushInterval: time.Hour,  // ticker won't trigger
	})
	w.Start()

	// Enqueue 10 records that would otherwise sit forever.
	for i := 0; i < 10; i++ {
		w.Enqueue(&pb.LogRecord{RequestId: "x"})
	}

	w.Stop(2 * time.Second)

	if flushed.Load() < 1 {
		t.Fatalf("expected at least one flush during graceful drain, got %d", flushed.Load())
	}
}

// ---------------------------------------------------------------------------
// Internal: stub the db.InsertBatch call via the package-level hook.
// ---------------------------------------------------------------------------

type insertStub struct {
	mu   sync.Mutex
	orig func(*sql.DB, []*pb.LogRecord) error
}

func (s *insertStub) restore() {
	insertFn = s.orig
}

func stubInsert(calls *atomic.Int32) *insertStub {
	s := &insertStub{orig: insertFn}
	insertFn = func(_ *sql.DB, batch []*pb.LogRecord) error {
		calls.Add(1)
		return nil
	}
	return s
}

// stubInsertWithBehavior installs a stub whose return value depends
// on the call index — used by the retry tests to simulate "fail N
// times then succeed".
func stubInsertWithBehavior(
	calls *atomic.Int32,
	behavior func(callNum int32) error,
) *insertStub {
	s := &insertStub{orig: insertFn}
	insertFn = func(_ *sql.DB, batch []*pb.LogRecord) error {
		n := calls.Add(1)
		return behavior(n)
	}
	return s
}
