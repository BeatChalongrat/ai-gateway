package service

// FIX41 E1 — where does the run ledger live? Measured, not argued.
//
// The brief's rule, declared before any number was taken: pick the
// option whose p95 for a NORMAL TURN grows by no more than 15 ms and
// whose restart-resume (probe P2) passes. If nothing satisfies both,
// report the numbers and let the owner decide.
//
// The unit is a turn, not a frame, because that is what a person waits
// on — and it is the unit that exposes the trap: a per-frame cost that
// looks free (0.2 ms) is 80 ms once a turn emits 400 deltas.
//
// Opt-in: FIX41_LEDGER_BENCH=1 (needs redis-webchat on :6380 and
// postgres-webchat on :5433). Writes its table to $FIX41_PROBE_OUT.

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
	"github.com/redis/go-redis/v9"
)

// A turn shaped like a real one: the opening frame, a few phase markers,
// a long run of content deltas, and a terminal.
const (
	benchDeltas = 400
	benchRuns   = 200
	// The two network-bound options are sampled less: at ~1 ms per frame
	// a full 200-turn sweep is minutes of round trips to prove a number
	// that is already an order of magnitude over budget.
	benchRunsSlow = 30
)

func benchFrames() []LedgerEvent {
	out := make([]LedgerEvent, 0, benchDeltas+10)
	add := func(ev string, payload string) {
		out = append(out, LedgerEvent{Seq: int64(len(out) + 1), Event: ev, Data: json.RawMessage(payload)})
	}
	add("message_start", `{"run_id":"00000000-0000-0000-0000-000000000000","seq":1,"message_id":"11111111-1111-1111-1111-111111111111"}`)
	add("model_resolved", `{"run_id":"00000000-0000-0000-0000-000000000000","seq":2,"model":"gpt-5.4-mini"}`)
	add("analysis_status", `{"run_id":"00000000-0000-0000-0000-000000000000","seq":3,"status":"started"}`)
	for i := 0; i < benchDeltas; i++ {
		add("content_delta", fmt.Sprintf(`{"run_id":"00000000-0000-0000-0000-000000000000","seq":%d,"delta":"ข้อความภาษาไทยสักท่อนหนึ่ง "}`, len(out)+1))
	}
	add("message_complete", fmt.Sprintf(`{"run_id":"00000000-0000-0000-0000-000000000000","seq":%d,"final_seq":%d,"message_id":"11111111-1111-1111-1111-111111111111"}`, len(out)+1, len(out)+1))
	return out
}

func percentile(d []time.Duration, p float64) time.Duration {
	if len(d) == 0 {
		return 0
	}
	s := append([]time.Duration(nil), d...)
	sort.Slice(s, func(i, j int) bool { return s[i] < s[j] })
	i := int(float64(len(s)-1) * p)
	return s[i]
}

type ledgerOption struct {
	Key          string  `json:"key"`
	Name         string  `json:"name"`
	PerTurnP50MS float64 `json:"per_turn_p50_ms"`
	PerTurnP95MS float64 `json:"per_turn_p95_ms"`
	PerFrameUS   float64 `json:"per_frame_us"`
	// PerTurnMeanUS is total elapsed / turns. Reported because on this box
	// time.Since has ~1 ms granularity, so any option faster than that
	// reads as a flat 0.0000 in p50/p95 — a number that looks like a
	// measurement and is really the clock giving up. The mean over many
	// turns gets under the resolution honestly.
	PerTurnMeanUS   float64  `json:"per_turn_mean_us"`
	BytesPerRun     int      `json:"bytes_per_run"`
	SurvivesRestart bool     `json:"survives_restart"`
	CrossReplica    bool     `json:"cross_replica"`
	Notes           []string `json:"notes,omitempty"`
	Error           string   `json:"error,omitempty"`
}

func TestFix41LedgerBackendBench(t *testing.T) {
	if os.Getenv("FIX41_LEDGER_BENCH") != "1" {
		t.Skip("set FIX41_LEDGER_BENCH=1 to measure the ledger backends")
	}
	frames := benchFrames()
	bytesPerRun := 0
	for _, f := range frames {
		bytesPerRun += len(f.Data) + len(f.Event) + 16
	}
	ctx := context.Background()
	var results []ledgerOption

	// ── L4 — no ledger at all. The floor. ────────────────────────────────
	{
		var d []time.Duration
		for r := 0; r < benchRuns; r++ {
			start := time.Now()
			for range frames {
				// The pre-FIX41 write path did nothing beyond the socket.
			}
			d = append(d, time.Since(start))
		}
		results = append(results, ledgerOption{
			Key: "L4", Name: "no ledger (pre-FIX41 snapshot only)",
			PerTurnP50MS: ms(percentile(d, 0.50)), PerTurnP95MS: ms(percentile(d, 0.95)),
			PerFrameUS: us(percentile(d, 0.95), len(frames)), PerTurnMeanUS: meanUS(d), BytesPerRun: 0,
			SurvivesRestart: false, CrossReplica: false,
			Notes: []string{"the client is told to reload the message from the API when a resume fails"},
		})
	}

	// ── L1 — in-process slice, which is what chunk 1 ships. ──────────────
	{
		var d []time.Duration
		for r := 0; r < benchRuns; r++ {
			var mu sync.Mutex
			ledger := make([]LedgerEvent, 0, len(frames))
			start := time.Now()
			for _, f := range frames {
				mu.Lock()
				ledger = append(ledger, f)
				mu.Unlock()
			}
			d = append(d, time.Since(start))
		}
		results = append(results, ledgerOption{
			Key: "L1", Name: "in-process slice (chunk 1)",
			PerTurnP50MS: ms(percentile(d, 0.50)), PerTurnP95MS: ms(percentile(d, 0.95)),
			PerFrameUS: us(percentile(d, 0.95), len(frames)), PerTurnMeanUS: meanUS(d), BytesPerRun: bytesPerRun,
			SurvivesRestart: false, CrossReplica: false,
			Notes: []string{"resume across a restart is impossible: the only copy is this process's memory"},
		})
	}

	// ── L2a — Redis, one round trip per frame. The obvious version. ──────
	rdb := benchRedis(t)
	if rdb != nil {
		var d []time.Duration
		var failed string
		for r := 0; r < benchRunsSlow && failed == ""; r++ {
			key := "fix41:bench:sync:" + uuid.NewString()
			start := time.Now()
			for _, f := range frames {
				b, _ := json.Marshal(f)
				if err := rdb.RPush(ctx, key, b).Err(); err != nil {
					failed = err.Error()
					break
				}
			}
			_ = rdb.Expire(ctx, key, 10*time.Minute).Err()
			d = append(d, time.Since(start))
			rdb.Del(ctx, key)
		}
		results = append(results, ledgerOption{
			Key: "L2a", Name: "Redis list, one RPUSH per frame (synchronous)",
			PerTurnP50MS: ms(percentile(d, 0.50)), PerTurnP95MS: ms(percentile(d, 0.95)),
			PerFrameUS: us(percentile(d, 0.95), len(frames)), PerTurnMeanUS: meanUS(d), BytesPerRun: bytesPerRun,
			SurvivesRestart: true, CrossReplica: true, Error: failed,
			Notes: []string{"a per-frame cost that reads as free is not free 400 times"},
		})

		// ── L2b — Redis, pipelined off the hot path. ─────────────────────
		d = nil
		failed = ""
		for r := 0; r < benchRuns && failed == ""; r++ {
			key := "fix41:bench:async:" + uuid.NewString()
			done := make(chan struct{})
			out := make(chan LedgerEvent, 4096)
			go func() {
				defer close(done)
				pipe := rdb.Pipeline()
				n := 0
				flush := func() {
					if n == 0 {
						return
					}
					if _, err := pipe.Exec(ctx); err != nil {
						failed = err.Error()
					}
					n = 0
				}
				tick := time.NewTicker(20 * time.Millisecond)
				defer tick.Stop()
				for {
					select {
					case f, ok := <-out:
						if !ok {
							flush()
							return
						}
						b, _ := json.Marshal(f)
						pipe.RPush(ctx, key, b)
						n++
						if n >= 64 {
							flush()
						}
					case <-tick.C:
						flush()
					}
				}
			}()
			start := time.Now()
			for _, f := range frames {
				// This is the cost the TURN pays: a buffered channel send.
				out <- f
			}
			elapsed := time.Since(start)
			close(out)
			<-done
			d = append(d, elapsed)
			rdb.Del(ctx, key)
		}
		results = append(results, ledgerOption{
			Key: "L2b", Name: "Redis list, pipelined off the hot path (in-memory stays the read path)",
			PerTurnP50MS: ms(percentile(d, 0.50)), PerTurnP95MS: ms(percentile(d, 0.95)),
			PerFrameUS: us(percentile(d, 0.95), len(frames)), PerTurnMeanUS: meanUS(d), BytesPerRun: bytesPerRun,
			SurvivesRestart: true, CrossReplica: true, Error: failed,
			Notes: []string{
				"the turn pays a channel send; the round trips happen behind it",
				"a frame can be in flight when the process dies — the window is one flush interval",
			},
		})
	} else {
		results = append(results, ledgerOption{Key: "L2", Name: "Redis", Error: "redis-webchat unreachable on :6380"})
	}

	// ── L3 — Postgres, a new table. ──────────────────────────────────────
	if pool := benchPool(t); pool != nil {
		t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
		_, err := pool.Exec(ctx, `CREATE UNLOGGED TABLE IF NOT EXISTS fix41_bench_ledger (
			run_id uuid NOT NULL, seq bigint NOT NULL, event text NOT NULL,
			data jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
			PRIMARY KEY (run_id, seq))`)
		if err != nil {
			results = append(results, ledgerOption{Key: "L3", Name: "Postgres table", Error: err.Error()})
		} else {
			var d []time.Duration
			var failed string
			for r := 0; r < benchRunsSlow && failed == ""; r++ {
				runID := uuid.New()
				start := time.Now()
				for _, f := range frames {
					if _, err := pool.Exec(ctx,
						`INSERT INTO fix41_bench_ledger (run_id, seq, event, data) VALUES ($1,$2,$3,$4)`,
						runID, f.Seq, f.Event, []byte(f.Data)); err != nil {
						failed = err.Error()
						break
					}
				}
				d = append(d, time.Since(start))
			}
			_, _ = pool.Exec(ctx, `DROP TABLE IF EXISTS fix41_bench_ledger`)
			results = append(results, ledgerOption{
				Key: "L3", Name: "Postgres table (one INSERT per frame, migration 080)",
				PerTurnP50MS: ms(percentile(d, 0.50)), PerTurnP95MS: ms(percentile(d, 0.95)),
				PerFrameUS: us(percentile(d, 0.95), len(frames)), PerTurnMeanUS: meanUS(d), BytesPerRun: bytesPerRun,
				SurvivesRestart: true, CrossReplica: true, Error: failed,
				Notes: []string{
					"measured on an UNLOGGED table — a real (logged) one is slower, so this is the optimistic bound",
					"needs a cleanup job; chat-edge would be writing every token of every turn to durable storage",
				},
			})
		}
	} else {
		results = append(results, ledgerOption{Key: "L3", Name: "Postgres table", Error: "postgres-webchat unreachable on :5433"})
	}

	for _, r := range results {
		t.Logf("%-4s %-62s p50=%9.4fms p95=%9.4fms mean=%10.2fus/turn restart=%v %s",
			r.Key, r.Name, r.PerTurnP50MS, r.PerTurnP95MS, r.PerTurnMeanUS, r.SurvivesRestart, r.Error)
	}

	if out := os.Getenv("FIX41_PROBE_OUT"); out != "" {
		_ = os.MkdirAll(out, 0o755)
		b, _ := json.MarshalIndent(map[string]any{
			"frames_per_turn": len(frames),
			"runs_sampled":    benchRuns,
			"budget_ms":       15,
			"options":         results,
		}, "", "  ")
		p := filepath.Join(out, "E1-ledger-backends.json")
		if err := os.WriteFile(p, b, 0o644); err != nil {
			t.Fatalf("write %s: %v", p, err)
		}
		t.Logf("wrote %s", p)
	}
}

func ms(d time.Duration) float64 { return float64(d.Nanoseconds()) / 1e6 }

// meanUS is total elapsed across all sampled turns, per turn. Immune to
// the per-sample clock resolution that flattens sub-millisecond options.
func meanUS(d []time.Duration) float64 {
	if len(d) == 0 {
		return 0
	}
	var total time.Duration
	for _, x := range d {
		total += x
	}
	return float64(total.Nanoseconds()) / 1e3 / float64(len(d))
}
func us(d time.Duration, n int) float64 {
	if n == 0 {
		return 0
	}
	return float64(d.Nanoseconds()) / 1e3 / float64(n)
}

func benchRedis(t *testing.T) *redis.Client {
	t.Helper()
	url := os.Getenv("FIX41_BENCH_REDIS_URL")
	if url == "" {
		url = "redis://localhost:6380/1"
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		t.Logf("redis url: %v", err)
		return nil
	}
	c := redis.NewClient(opts)
	if err := c.Ping(context.Background()).Err(); err != nil {
		t.Logf("redis unreachable: %v", err)
		_ = c.Close()
		return nil
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}

func benchPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	// FIX119 Part C1 — a private clone of the test template, never the
	// live database (internal/testdb refuses it and fails without a DB).
	return testdb.Fresh(t)
}
