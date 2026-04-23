# FINDING — Gateway saturation at sustained concurrent load

## Executive summary

At the current configuration (single-process uvicorn, sync psycopg /
SQLAlchemy + Presidio-loaded spaCy + per-request new httpx clients) the
gateway **cannot sustain 50 RPS** and is already >8× the SLO budget
at 25 RPS. The proximate CPU cost at 50 RPS is **SQLAlchemy / psycopg
(32%) + spaCy / Presidio (26%) + Redis (15%)**, **not httpx (0.4%)**.
The gateway has ≥3 documented Python-fixable bottlenecks; premature Go
migration would optimise the wrong layer.

## Evidence

### Mini-Check-B50 (50 RPS × 60 s) — FAILED 2× (see `perf/sanity/B50_container/check_result.md`)

| metric | observed |
|---|---|
| `http_req_duration.p50` | 30 225 ms |
| `http_req_duration.p95` | 59 994 ms (k6 timeout wall) |
| `http_req_failed` | 27.4 % (100 of 365 client-side timeouts) |
| `dropped_iterations` | 2 635 (32/s) |
| `proc_cpu_pct` p50 / p95 / max | 87.9 / 96.9 / **103.0** |
| backend 5xx | 0 |

### Mini-Check-B25 (25 RPS × 60 s, post-rescope) — FAILED on p95 only (see `perf/sanity/B25_container/check_result.md`)

| criterion | target | observed | verdict |
|---|---|---|---|
| p95 | < 3 000 ms | **25 846 ms** | FAIL |
| 5xx count | 0 | 0 | PASS |
| 429 count | 0 | 0 | PASS |
| CPU band | 40 – 70 % | p50 45.5 / p95 58.5 / mean **46.1** | PASS |

Headline: at 25 RPS the gateway has ~40 % CPU headroom and latency is
still 8× the budget. CPU is NOT the proximate bottleneck at 25 RPS;
something else (queue, async-concurrency, or sync IO) is.

### py-spy @ 50 RPS saturation (`perf/profiles/gateway_50rps_raw.txt`)

* 5802 samples @ 100 Hz × 90 s, attached to backend python pid 22276,
  captured concurrently with `steady.js` at RPS=50 against the
  containerised mock.
* SVG flame graph: `perf/profiles/gateway_50rps_saturation.svg`.
* Text top-20: `perf/profiles/gateway_50rps_top20.txt`.

## Attribution (leaf-frame own %, 5802 samples)

| Category | % of CPU | Key functions |
|---|---:|---|
| SQLAlchemy / ORM (psycopg3 sync) | **32.16 %** | `psycopg\waiting.py:269 wait_select`, `_cursor_base.py:598 _send_query_prepared`, `_connection_base.py:476 _exec_command` |
| spaCy / NER / Presidio | **25.82 %** | `thinc\layers\maxout.py:52 forward`, `spacy\language.py:1049 __call__`, `attributeruler.py:149 match` |
| Other (DNS / import stat / detect) | 15.41 % | `socket.py:962 getaddrinfo`, `_path_stat` |
| Redis (sync client) | **15.01 %** | `redis\_parsers\socket.py:65 _read_from_socket`, `redis\connection.py:627 _connect`, `send_packed_command` |
| Logging / structlog | 5.15 % | `logging.Handler.emit`, `Handler.flush`, `stamper_iso_utc` |
| Event loop / asyncio | 3.40 % | `proactor_events.py:828 _write_to_self`, `windows_events.py:566 send` |
| Middleware / ASGI / FastAPI / Starlette | 1.55 % | `solve_dependencies`, `get_route_path` |
| Pydantic / JSON | 1.12 % | `orjson.dumps`, `json.raw_decode` |
| httpx (connection / request) | **0.38 %** | `anyio semaphore ops`, `cancel_shielded_checkpoint` |

Totals: 100.00 % / 5802 samples classified.

**Honesty note** — "Other" contains `getaddrinfo` (2.40 %) and the
frozen-importer path-stat (1.29 %). Neither is "the" bottleneck; both
are symptomatic of the same root (a new httpx client per request →
fresh DNS + TLS handshake → reimport warmup on cold caches).
`httpx` leaf-own % is only 0.38 % because **the hot work in the new-
client-per-request pattern happens in `redis._connect` /
`psycopg._connect` / `socket.getaddrinfo` — the connection setup, not
the httpx library itself**. That is exactly the anti-pattern a
singleton AsyncClient + shared pool would remove.

## Fix directions (flag-only; ranked by expected impact)

These are stated in order of expected payoff. None is implemented —
they are the **measurement targets** for the re-baseline after the
Auditor sequence.

1. **Singleton `httpx.AsyncClient`** with a shared connection pool
   (limits=httpx.Limits(max_connections=200, max_keepalive=100)).
   Today each request builds and tears down a client → fresh DNS +
   TLS per call. Expected payoff: eliminate the `getaddrinfo`
   (2.40 %) + the connection-establishment portion of
   `redis\connection.py:627 _connect` (3.12 %) + psycopg `_connect`
   costs; also cut latency-floor at low RPS by skipping the handshake
   RTT. Preserved as a validation target by prior rounds; now
   prioritised.
2. **Async Redis with pipelined / Lua rate-limit.** The rate-limiter
   today issues 12 Redis commands per request. Moving to a single
   Lua script + async client removes 11 round-trips and removes the
   `redis\_parsers\socket.py:65 _read_from_socket` (7.15 %) hot path.
   Expected payoff: single-digit % CPU + a large p95 reduction
   because each saved Redis RTT is ~0.3 – 1.0 ms on-box but adds up
   to 12× per request.
3. **psycopg async + SQLAlchemy async engine.** 32.16 % of CPU is
   `wait_select` + sync cursor ops. The `wait_select` call is the
   selector waiting for the socket — proof that the worker is
   spending time blocked on sync DB IO. Going async (psycopg3
   AsyncConnection + AsyncSession) lets the event loop serve other
   requests during that wait. Expected payoff: largest single chunk
   of CPU reduction AND removes the DB-pool-saturation class of
   bug recorded in `FINDING_DB_POOL_SATURATION.md`.
4. **Lazy / off-request-path NER.** Presidio + spaCy forward passes
   (25.82 %) run on every request under Variant B/C. Moving NER to a
   background queue (only block if the user policy says "mask" / "deny"
   and the regex tier already flagged) cuts this to near-zero on the
   hot path. Feature-flag gated.
5. **uvloop via Linux Docker.** The Windows ProactorEventLoop
   (`proactor_events.py:828 _write_to_self` — 1.17 %) is slower per-
   wakeup than uvloop by 3-8×. Platform change, separate SKU.
6. **Multi-worker uvicorn (GIL bypass).** Changes the measurement
   model — every worker holds its own histograms / pools — so this
   is a caveat-heavy change. Listed for completeness.
7. **Go migration.** LAST resort after the Python fixes above are
   attempted and re-baselined. See next section.

## Implication for Go migration decision

Gateway has **≥3 documented Python-fixable bottlenecks** (singleton
httpx, async Redis, psycopg async). Premature Go migration would
optimise the wrong layer — moving to Go would still leave 26 % of
CPU on spaCy + Presidio (which is not "Python language" cost but
ML-inference cost that Go inherits via C extensions or worse, a
re-implementation), 32 % on DB IO (psycopg3 has no Go equivalent with
parity for the SQLAlchemy feature set), and 15 % on Redis (same
shape of problem). The promise of a Go rewrite is mostly the
event-loop speedup (~3 % today) and the GIL bypass (multi-worker
scale-out, already achievable in Python via uvicorn `--workers N`).

**Recommend Auditor sequence:** fix httpx pool + async Redis + evaluate
uvloop, re-baseline, THEN decide about Go. If the re-baseline at 50
RPS still fails after 1 – 3 are applied, Go becomes defensible. Today
it is not.

## Relationship to other findings

This finding is a **meta-finding** — the other two are instances of
the same underlying shape.

* `FINDING_429_LATENCY_ANOMALY.md` — 4.7 s latency on a 429 response.
  Caused by the same "sync-on-hot-path" shape: the rate-limiter's 12
  Redis calls are blocking even when the rate-limiter decision is
  reject. The 429 path SHOULD be fast-local (returned before any DB
  work); instead it's bottlenecked on the shared httpx / DB / Redis
  pool that the rest of the request also queues on.
* `FINDING_DB_POOL_SATURATION.md` — DB pool exhausts at 25 RPS (pre-
  config-raise). The 32 % `wait_select` + `_send_query_prepared` tail
  above is the same thing: sync psycopg means every concurrent
  request holds a pool connection while the event loop could be
  multiplexing.
* **This finding** — gateway cannot sustain target load.

All three reduce to: **"gateway not tuned for sustained concurrent
load — sync IO on the hot path + no connection reuse."** Fixing any
one in isolation helps; fixing all three is the correct order-of-ops
for a defensible pre-Go re-baseline.

## Artefacts

| File | Purpose |
|---|---|
| `perf/sanity/B50_container/check_result.md` | prior 50-RPS failure evidence |
| `perf/sanity/B25_container/check_result.md` | 25-RPS rescope failure evidence (p95 only) |
| `perf/sanity/log_fix/probe_log_lines.json` | 10/10 verified phase-log probe lines post log-pipeline fix |
| `perf/profiles/gateway_50rps_saturation.svg` | py-spy flamegraph, 120 s, concurrent with 50 RPS load |
| `perf/profiles/gateway_50rps_raw.txt` | py-spy raw collapsed stacks, 5802 samples |
| `perf/profiles/gateway_50rps_top20.txt` | top-20 CPU consumers + category labels |
| `perf/profiles/gateway_50rps_attribution.md` | category table (same numbers as above, narrative form) |

## Expected value of Go migration — revised

Based on py-spy attribution, per-component Go-migration ROI analysis:

| Component | CPU % | Bound | Go viable? | Expected gain from Go | Recommendation |
|---|---:|---|---|---|---|
| SQLAlchemy / psycopg | 32 % | I/O | NO | ~0 % (I/O-bound, not CPU) | Python async (asyncpg / async SQLAlchemy) |
| spaCy / Presidio NER | 26 % | CPU | MAYBE | 30-50 % on this component = 8-13 % total | Consider ONLY after Python optimization attempts |
| "Other" / DNS / `getaddrinfo` / `_connect` | 15 % | I/O setup | NO | ~0 % (solved by httpx singleton in Python) | Python fix |
| Redis (sync client) | 15 % | I/O | NO | ~0 % (I/O-bound) | Python async (aioredis + Lua EVAL) |
| Logging / structlog | 5 % | Mixed | NO | < 1 % gain | Python async handler |
| Event loop / asyncio | 3 % | Go wins here | YES but tiny | < 3 % total | Not worth migrating for 3 % |
| Middleware / ASGI / FastAPI | 1.6 % | CPU | YES but tiny | < 1 % total | Not worth migrating for 1.6 % |
| Pydantic / JSON | 1.1 % | CPU | — | < 1 % | Not worth it |
| httpx | 0.4 % | — | — | rounding error | Preserve-for-validation hypothesis invalidated |

**Conclusion.** At most one component (spaCy NER) is a plausible Go
migration candidate — and only after Python optimization attempts
fail. Everything else is either I/O-bound (Go does not help with
I/O wait time) or too small to justify migration overhead. The
original hypothesis that httpx per-call creation was a meaningful
cost is **disproved by measurement**: httpx own-CPU is 0.4 %, and the
adjacent setup cost (`getaddrinfo` + `_connect`) is Python-fixable
via a singleton `AsyncClient` with shared pool.

## Recommended fix order

Python-first sequence. After step 5 the question "should we use Go
anywhere?" becomes answerable with data, not speculation.

| # | Step | Effort | Expected CPU reduction | Priority |
|---|---|---|---|---|
| 1 | `httpx` singleton with shared connection pool | 1 day | ~15 % (collapses `getaddrinfo` + `_connect`) | Quick win |
| 2 | Async Redis + Lua EVAL for rate limiter | 2-3 days | ~14 % (15 → < 1 %) | Quick compound gain |
| 3 | Async SQLAlchemy (replace sync psycopg on hot path) | 2-3 weeks | ~27 % (32 → < 5 %) | Largest single gain |
| 4 | spaCy / Presidio optimization (smaller model / skip-on-low-risk / caching) | 1 week | ~10-18 % (26 → 8-15 %) | Largest second gain |
| 5 | **Re-baseline** — measure post-Python-fix state | 1 day | — | Decision point |
| 6 | **Re-evaluate Go candidates** from stabilized baseline | — | — | Go decision gate |
| 7 | Linux uvloop deployment comparison (parallel to 1-4) | deploy change | platform-level, 10-20 % overall | Test in parallel |

**Decision logic after step 5:**
- If p95 < 3 000 ms at 50 RPS → gateway sustains target in Python. Go migration not needed.
- If p95 still > 3 000 ms → remaining bottleneck identifies the true Go candidate (likely NER or a specific hot loop that Python async cannot address).

## What this FINDING does NOT do

* Does NOT fix the httpx / Redis / DB bottlenecks — they are the
  measurement target for the next round.
* Does NOT change mock or provider behaviour. Mock container
  calibration (`MOCK_CALIBRATION_CONTAINERISED.md`) is intact.
* Does NOT launch Phase 3 — that is explicitly gated behind user
  review of this document.
