# `total_ms` can exceed the sum of its phases, with nothing accounting for the gap

**Status:** open. Instrumentation gap, not a performance bug in itself — but it
is what makes a certain class of performance bug invisible.

Salvaged from a 2026-04-22 perf investigation. Everything else in that
investigation described an architecture that no longer exists (see the note at
the bottom); this part still describes today's code.

---

## The gap

`total_ms` and the per-phase fields do not measure the same span.

- **`total_ms`** is `time.perf_counter() - PhaseTimer.started_at`
  (`observability/phase_timer.py:83`), and `started_at` defaults to the moment
  the timer is constructed (`:59`) — which happens in `MetricsMiddleware.__call__`
  **before** the request is passed downstream (`middleware/metrics.py:129`).
- **The first phase** to open is `rate_limit`, inside the orchestrator
  (`services/orchestrator.py:267`), followed by `auth` (`:329`).

Nothing instruments the span between those two points. That span contains:

- FastAPI route resolution and request-body parsing,
- `Depends(get_resolved_api_key)` — a database lookup on `api_keys`,
- the DB session construction that lookup needs,
- and any time the request spent waiting for an event-loop slot before
  `MetricsMiddleware` ran at all.

So a request can log every phase as near-zero and still report a large
`total_ms`, with no field naming where the time went.

## Why it matters

It is worst on the paths that do the least work. A request rejected early —
a 429, an auth failure — exits before most phases open, so its phase table is
almost all zeros. If such a request is slow, the log says so and says nothing
about why. That is precisely the shape a queueing or pool-contention problem
takes, and precisely the shape this instrumentation cannot see.

The original investigation measured a ~4.8 s `total_ms` against ~29 ms of
summed phases on rejected requests under load. **Do not treat that number as
current** — it was measured against sync SQLAlchemy, sync `redis-py`,
per-request httpx clients and in-process spaCy, none of which is still true.
The number is stale. The blind spot that hid it is not.

## What would close it

Either open a phase around dependency resolution so the pre-orchestrator span
is attributed, or record a second timestamp when the orchestrator is entered
and emit the difference as its own field. The second is smaller: one field, no
change to `CANONICAL_PHASES` consumers. Neither has been scheduled.

An easy first check that this is still live: pick any log line where
`total_ms` materially exceeds the sum of the `*_ms` fields. The residual is
this gap.

---

### What was *not* salvaged, and why

Three findings from 2026-04-22 (`FINDING_429_LATENCY_ANOMALY`,
`FINDING_DB_POOL_SATURATION`, `FINDING_GATEWAY_SATURATION`) shared a diagnosis:
the gateway could not sustain 50 RPS because of sync SQLAlchemy/psycopg (~32%
CPU), in-process Presidio/spaCy (~26%), sync Redis (~15%), and per-request
httpx clients. All four causes have since been removed:

| Then | Now |
|---|---|
| sync `redis-py`, one connection | `redis.asyncio` on a shared pool (`rate_limit_service.py:11,34`), plus a Rust gRPC limiter sidecar |
| sync SQLAlchemy on the request path | async sessions (`api/routes/public_chat.py` and siblings) |
| a new `httpx.AsyncClient` per request | shared client factory (`services/providers/http_client.py`) |
| Presidio/spaCy in-process | a separate `ner-service` container |

Those documents also recommended against migrating the proxy path to Go on the
grounds that it would "optimise the wrong layer". The Python bottlenecks were
fixed *and* the Go gateway shipped, so the recommendation is spent. The RPS
figures they quote describe a stack that no longer exists and should not be
cited. Current capacity claims live in
[`../CAPACITY_VALIDATION_150_250.md`](../CAPACITY_VALIDATION_150_250.md) and
[`../LOAD_TEST_GROWTH_BASELINE.md`](../LOAD_TEST_GROWTH_BASELINE.md).
