# Phase 0 — Benchmark Gate

Generated: 2026-05-30, branch `perf/phase-0-sidecars`. Stack: local Docker compose, mock provider.
This is the **baseline for Phase 1**, and an honest before/after for the two flips that shipped
(Go log-worker, Rust rate-limiter). DLP stays `python` (flip descoped — see `DLP_PARITY_REPORT.md`).

## Method
- Hot path: `POST /v1/chat/completions`, `model:auto`, via the published gateway port (8080),
  against the in-cluster mock provider. Key `gw_4be18…`.
- 50 sequential requests per config; rate-limit window flushed before each run (so 429s don't
  pollute latency). Client-side `time_total` (curl) → p50/p95/p99.
- Configs toggled via engine env + `docker compose up -d backend` (recreate). DLP=python in both.
- ⚠️ N=50 → **tail percentiles (p95/p99) are noisy**; treat p50 as the stable signal.

## Chat-path latency (end-to-end, incl. mock provider)

| Config | log-worker | rate-limiter | DLP | p50 | p95 | p99 | max | errors |
|---|---|---|---|---|---|---|---|---|
| **A — baseline** | python | python | python | **98.1 ms** | 169.2 ms | 436.5 ms | 3218 ms | 0/50 |
| **B — Phase-0** | **go** | **rust** | python | **98.3 ms** | 150.5 ms | 224.0 ms | 4501 ms | 0/50 |

**Reading:** p50 is **identical (~98 ms)** — the request is dominated by the provider call + the
**synchronous Python DLP scan on the event loop**, neither of which the Phase-0 flips touch. p95/p99
look better under B but the gap is within N=50 noise. The `max` in both is a cold-start first
request (see loop-block below). **Conclusion: the Phase-0 flips are latency-neutral at this scale —
as expected.** Their value is *operational / headroom*, not p50:
- **Rust rate-limiter** — Python rate-limit was already async-on-Redis (not GIL-bound), so latency
  is unchanged; the win is parallel-scope checks + moving the work off the Python process under
  high concurrency.
- **Go log-worker** — the Python queue already offloaded the INSERT via `to_thread`; the win is
  COPY-batch efficiency + lower gateway memory + a separate failure domain, not request latency.

The **real latency wins are Phase 1 / the DLP track**: shared httpx provider pool (−50–200 ms TTFB),
Rust DLP (removes the sync scan from the loop), async Postgres.

## Component micro-latency

| Component | Python | Rust/Go | Source |
|---|---|---|---|
| DLP scan (per small input) | **0.42 ms** p50 | **2.63 ms** p50 (gRPC) | `dlp_parity_runner.py` (DLP not flipped — informational) |
| Rate-limit decision | ~1.5–3 ms (3 seq pipelines) | parallel scopes + ~0.3 ms gRPC | audit + parity |
| Log-worker insert | inline `to_thread` COPY | Go COPY batch (off-process) | Session 0.2 |

**Note on DLP:** Rust is *slower* per call on small inputs because the gRPC round-trip (~2 ms)
dominates sub-ms detection. Rust DLP's payoff is **GIL-offload under concurrency** (the sync Python
scan currently blocks the single event loop per worker), not single-request p50. This reinforces
that the DLP flip must be justified by throughput/tail under load — and gated on parity (currently
FAIL).

## Event-loop blocking (loop-monitor, `main.py:293`, threshold 100 ms)
- During the 50-request runs: **1** block event, **2.32 s**, on the **cold-start first request**
  (lazy DLP registry / model init), matching the ~3.2–4.5 s `max`. Steady-state: **0** blocks.
- Caveat: this run is sequential/low-concurrency, so it does **not** stress the known steady-state
  hazard — the **synchronous Python DLP scan blocking the loop under concurrency** (audit P0 B2).
  A proper 50-RPS soak is deferred to the Phase-1 baseline once a load tool that respects the
  per-key 60/min cap (multi-key) is in place.

## Functional validation (from Sessions 0.2–0.3)
- **Rust rate-limiter:** 75-parallel burst → 20×200 + 55×429, Rust `denied_total` +55 (enforced);
  failover to Python on sidecar stop; resume on restart.
- **Go log-worker:** `flushed_total` +5 on traffic; sidecar-down → 3 rows recovered via Python
  fallback (R5 fix) with `log_worker_grpc_fallback` logged; no loss.

## Gate result
- No latency regression from the Phase-0 flips (p50 flat; errors 0/50 both configs). ✅
- Baseline recorded for Phase 1.
- The dominant measured cost (provider call + sync Python DLP) is exactly what Phase 1 + the DLP
  parity track target. The Phase-0 value delivered is correctness/operational (audit-row durability
  via R5, rate-limit enforcement parity, sidecar headroom), not p50 latency — which is the honest
  and expected outcome of "turn on what's already built".
