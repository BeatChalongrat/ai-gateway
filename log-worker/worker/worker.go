// Package worker buffers LogRecord enqueues and flushes batches
// into Postgres via the COPY protocol.
//
// Design notes
//
//  1. The channel is sized large (50k by default) so a brief Postgres
//     stall doesn't immediately drop records. Sustained slowness
//     triggers the drop-oldest policy in Enqueue.
//
//  2. Flush triggers fire whichever-first: BatchSize records buffered,
//     or FlushInterval elapsed since the previous flush. A burst of
//     50 records in 5 ms becomes one COPY; a single record at the end
//     of a quiet minute still flushes within FlushInterval.
//
//  3. A flush failure does NOT requeue records — the batch is logged
//     and discarded. This matches the Python queue's behaviour
//     (request_log_queue._flush). Audit completeness on a Postgres
//     outage is sacrificed for liveness; the alternative (block
//     enqueue) would back-pressure the gateway request path.
package worker

import (
	"database/sql"
	"log"
	"sync"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"

	"github.com/beatchalongrat/ai-gateway/log-worker/db"
	pb "github.com/beatchalongrat/ai-gateway/log-worker/pb"
)

// insertFn is the COPY-batch hook. Bound to db.InsertBatch by
// default; overridden in unit tests so the worker can be exercised
// without a live Postgres.
var insertFn = db.InsertBatch

// Config bundles the tunables that come from environment variables.
type Config struct {
	DB            *sql.DB
	BatchSize     int
	FlushInterval time.Duration
	BufferSize    int
}

// Worker is the in-memory queue + background flusher.
type Worker struct {
	cfg    Config
	ch     chan *pb.LogRecord
	stop   chan struct{}
	wg     sync.WaitGroup
	closed atomic.Bool

	queueDepth   atomic.Int64
	droppedTotal atomic.Int64
	flushedTotal atomic.Int64
}

// --- Prometheus metrics --------------------------------------------------
//
// Defined as package-level so the same scrape source survives a
// hypothetical worker restart inside the same process. The names are
// chosen to match the audit doc's grep targets.

var (
	metricQueueDepth = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "log_worker_queue_depth",
		Help: "Current count of LogRecords buffered but not yet flushed.",
	})
	metricDroppedTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "log_worker_dropped_total",
		Help: "Lifetime LogRecords dropped due to channel overflow.",
	})
	metricBatchSize = promauto.NewHistogram(prometheus.HistogramOpts{
		Name: "log_worker_batch_size",
		Help: "Records per COPY batch.",
		// 1..2000 covers single-record flushes through pathologically
		// large bursts that hit the batch-size cap.
		Buckets: []float64{1, 10, 25, 50, 100, 250, 500, 1000, 2000},
	})
	metricInsertLatency = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "log_worker_insert_latency_ms",
		Help:    "End-to-end COPY latency in milliseconds.",
		Buckets: prometheus.ExponentialBucketsRange(0.5, 5000, 12),
	})
	metricInsertErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "log_worker_insert_errors_total",
		Help: "COPY transactions that failed (batch discarded).",
	})
	metricFlushedTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "log_worker_flushed_total",
		Help: "Records successfully written via COPY.",
	})
	metricRetryTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "log_worker_retry_total",
		Help: "COPY retries (post-failure re-attempts before drop).",
	})
)

// flushBackoffs is the per-attempt sleep between COPY retries.
// Length implicitly defines the retry budget (here: 2 retries after
// the initial attempt → up to 3 attempts total). Sized so a short
// Postgres restart (~1s) is absorbed without dropping a batch.
var flushBackoffs = []time.Duration{
	100 * time.Millisecond,
	500 * time.Millisecond,
}

// New constructs a worker but does NOT start the background flusher.
// Call Start() once after registering the gRPC server so the channel
// is alive when the first Enqueue lands.
func New(cfg Config) *Worker {
	if cfg.BatchSize <= 0 {
		cfg.BatchSize = 100
	}
	if cfg.FlushInterval <= 0 {
		cfg.FlushInterval = 100 * time.Millisecond
	}
	if cfg.BufferSize <= 0 {
		cfg.BufferSize = 50000
	}
	return &Worker{
		cfg:  cfg,
		ch:   make(chan *pb.LogRecord, cfg.BufferSize),
		stop: make(chan struct{}),
	}
}

// Start launches the background flush goroutine.
func (w *Worker) Start() {
	w.wg.Add(1)
	go w.runFlusher()
}

// Stop signals the flusher to drain and exit, then waits up to
// timeout for the drain to finish. Records remaining after the
// timeout are counted as dropped.
func (w *Worker) Stop(timeout time.Duration) {
	if w.closed.Swap(true) {
		return
	}
	close(w.stop)

	done := make(chan struct{})
	go func() {
		w.wg.Wait()
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(timeout):
		log.Printf("worker stop: drain timeout after %s, %d records may be lost",
			timeout, w.queueDepth.Load())
	}
}

// Enqueue accepts a record. Returns false when the buffer is full
// even after the drop-oldest fallback (an edge case under sustained
// overload).
func (w *Worker) Enqueue(rec *pb.LogRecord) bool {
	if w.closed.Load() {
		return false
	}
	select {
	case w.ch <- rec:
		w.queueDepth.Add(1)
		metricQueueDepth.Inc()
		return true
	default:
		// Channel full — drop oldest then retry. This mirrors the
		// Python queue's policy: a Postgres outage drops audit rows,
		// not request bodies.
		select {
		case dropped := <-w.ch:
			_ = dropped
			w.droppedTotal.Add(1)
			metricDroppedTotal.Inc()
			w.queueDepth.Add(-1)
			metricQueueDepth.Dec()
		default:
			// Race: another goroutine drained between SELECT and now.
			// Try one more direct send.
		}
		select {
		case w.ch <- rec:
			w.queueDepth.Add(1)
			metricQueueDepth.Inc()
			return true
		default:
			w.droppedTotal.Add(1)
			metricDroppedTotal.Inc()
			return false
		}
	}
}

// Stats returns (queueDepth, droppedTotal, flushedTotal) for /Health.
func (w *Worker) Stats() (int64, int64, int64) {
	return w.queueDepth.Load(), w.droppedTotal.Load(), w.flushedTotal.Load()
}

func (w *Worker) runFlusher() {
	defer w.wg.Done()

	ticker := time.NewTicker(w.cfg.FlushInterval)
	defer ticker.Stop()

	batch := make([]*pb.LogRecord, 0, w.cfg.BatchSize)
	flush := func() {
		if len(batch) == 0 {
			return
		}
		size := len(batch)
		metricBatchSize.Observe(float64(size))

		// Retry the COPY a couple of times before dropping the batch.
		// A short Postgres restart (graceful reload, primary-failover
		// promotion, network blip) often falls inside the backoff
		// budget, and audit completeness > liveness on a small
		// recoverable outage.
		start := time.Now()
		var err error
		for attempt := 0; attempt <= len(flushBackoffs); attempt++ {
			if attempt > 0 {
				metricRetryTotal.Inc()
				time.Sleep(flushBackoffs[attempt-1])
			}
			err = insertFn(w.cfg.DB, batch)
			if err == nil {
				break
			}
			log.Printf(
				"flush attempt %d/%d failed batch=%d err=%v",
				attempt+1, len(flushBackoffs)+1, size, err,
			)
		}
		elapsedMS := float64(time.Since(start).Milliseconds())
		metricInsertLatency.Observe(elapsedMS)

		if err != nil {
			metricInsertErrors.Inc()
			log.Printf(
				"flush exhausted retries batch=%d dropping records last_err=%v",
				size, err,
			)
		} else {
			w.flushedTotal.Add(int64(size))
			metricFlushedTotal.Add(float64(size))
		}
		w.queueDepth.Add(-int64(size))
		metricQueueDepth.Sub(float64(size))
		batch = batch[:0]
	}

	for {
		select {
		case rec, ok := <-w.ch:
			if !ok {
				flush()
				return
			}
			batch = append(batch, rec)
			// Drain any additional ready records into the same batch
			// so a burst becomes one COPY without N small INSERTs.
			for len(batch) < w.cfg.BatchSize {
				select {
				case more, more_ok := <-w.ch:
					if !more_ok {
						flush()
						return
					}
					batch = append(batch, more)
				default:
					// channel empty for now — fall through to the
					// outer select / ticker for the next event.
					goto checkFlush
				}
			}
		checkFlush:
			if len(batch) >= w.cfg.BatchSize {
				flush()
			}
		case <-ticker.C:
			flush()
		case <-w.stop:
			// Drain remaining records before exit so a graceful
			// shutdown loses zero records that already made it
			// onto the channel.
			drainTimeout := time.After(5 * time.Second)
			for {
				select {
				case rec, ok := <-w.ch:
					if !ok {
						flush()
						return
					}
					batch = append(batch, rec)
					if len(batch) >= w.cfg.BatchSize {
						flush()
					}
				case <-drainTimeout:
					flush()
					return
				default:
					flush()
					return
				}
			}
		}
	}
}
