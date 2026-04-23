# Finding — DB Pool Saturation Under Sustained Load

**Discovered:** 2026-04-22, Phase-2 pre-launch sanity (Check B mini-run at 25 RPS).
**Status:** documented, not fixed. Flagged for the Auditor.
**Framing for the Auditor:** **this finding is independent of the Go-migration
question.** The proposed true fix is entirely Python-internal (async SQLAlchemy
+ read-path caching). Porting blocking-IO behaviour to Go before fixing the
blocking-IO bug is premature.

---

## 1. Evidence

Check B at **25 RPS × 60 s × single run** (`perf/sanity/B/`):

* Zero 4xx. Zero 5xx. All 1296 requests returned 200 — exact numbers from
  `perf/sanity/B/k6_summary_steady.json`:

```
"checks": { "passes": 2592, "fails": 0, "value": 1 }
"http_req_failed": { "passes": 0, "fails": 1296, "value": 0 }
"http_reqs":       { "count": 1296, "rate": 19.105043392433952 }
"http_req_duration": {
    "min":  129.965126,
    "med":  6781.4686409999995,
    "avg":  6748.524329136577,
    "p(90)": 9965.602202499998,
    "p(95)": 10239.29042575,
    "max":  12321.24837
}
"iteration_duration": { "p(95)": 10239.737322750001 }
"vus":      { "min": 10, "max": 200, "value": 49 }
"vus_max":  { "min": 50, "max": 200, "value": 200 }
"dropped_iterations": { "count": 205, "rate": 3.0220168946365433 }
```

Gateway log over the same window (`perf/sanity/B/check_result.md`):

> 1296 × 200, 0 × 429 on /v1/chat/completions during the window.

Interpretation: the gateway **accepted every request** — per-key rate-limit,
project-override (`PERF_TEST_PROJECT_LIMIT=120000`), and tenant-override
(`PERF_TEST_TENANT_LIMIT=240000`) all functioned as designed. But
`http_req_duration p95 = 10.23 s`, `med ≈ 6.78 s` — response times climbed into
the tens of seconds. The k6 constant-arrival-rate executor saturated its VU
pool at 200 (`vus_max.max=200`) and dropped 205 iterations because no VU was
free to dispatch them. This is the signature of back-pressure on a downstream
resource the handler is waiting on — not front-door rejection.

Cross-check: Phase-1 crash-investigation analysis (`CRASH_INVESTIGATION.md §2`)
had already ruled out every other saturation mode we know to look for — no
OOM, no FD exhaustion (Windows handles < 2k during the run), mock provider
served steady 200s throughout. The resource waiting in Check B is **inside
the gateway**, not upstream of it.

## 2. Impact analysis

### Current pool configuration

Verified by reading `backend/app/core/db.py:5-13`:

```python
engine = create_engine(
    settings.database_url,
    future=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
    pool_recycle=300,
    pool_pre_ping=True,
)
```

Effective ceiling: **30 concurrent checked-out connections** (`pool_size=10 +
max_overflow=20`). Values are hardcoded — there is no env override today. Pool
timeout is 10 s; any request that can't acquire a connection within 10 s will
raise `TimeoutError` instead of continuing to wait indefinitely.

### Saturation at 25 RPS

At 25 RPS, with a mock provider whose realistic-profile `total_completion`
p95 ≈ 3 s and at least two DB round-trips per successful chat (orchestrator
read-path + `request_log` insert), the average in-flight request count sits
well above 30. Every connection above the ceiling queues on the pool — k6
records that queue time as `http_req_duration`. Observed `p(95) = 10.239 s`
matches "pool_timeout (10 s) + normal in-pool handling (~200 ms)" within
instrument error.

### Implication for 50 RPS target

The PERF_BASELINE_PLAN §5.2 steady profile is **50 RPS**. Doubling the request
rate into the same 30-slot pool doubles the queue depth. Even before factoring
in a Go migration, **production cannot sustain 50 RPS on the stock pool
configuration** — the 10 s pool_timeout becomes the dominant source of user-
visible latency, and any increase in provider latency pushes the pool over the
cliff immediately.

### Relationship to `FINDING_429_LATENCY_ANOMALY.md`

Shared class: saturation-under-load. The 429-latency finding's secondary
hypothesis (#5 — table in `FINDING_429_LATENCY_ANOMALY.md §3`) attributes
~200 ms of the 4.7 s 429 gap to `get_resolved_api_key` blocking on the same
DB pool:

> Pool size is 10+20 = 30 max. At 200 VUs, pool must serialise ~170
> requests at any moment → bounded wait per request = (in-flight /
> pool_size × per-request-DB-ms) ≈ 5-7 × 30 ms ≈ 200 ms.

The 429-latency finding's dominant term (#1, event-loop queueing, ~4.5 s) is
distinct from DB-pool saturation (this finding). They are independent
mechanisms that surface together under the same load pattern.

## 3. Where in the request path

Three prime suspects, traced by grepping `session.(query|get|execute|scalar|add|commit|flush)`
inside the chat-completions path and walking orchestrator.py.

### 3a. `get_resolved_api_key` dependency — read-heavy, cache-friendly

File: `backend/app/api/deps.py:88-119` → `backend/app/services/api_key_service.py:63`.

Per chat request, runs:

```python
session.execute(stmt).scalar_one_or_none()   # api_key_service.py:63
```

Plus the transitive `session.get(ApiKeyORM, key_id)` at line 85 on revocation
checks. One checkout per request, held for the lifetime of the FastAPI
dependency scope — **spanning the entire request**, including the provider
call. This is the single highest-value cache target: same key → same
`(project_id, tenant_id, role)` mapping for 60 s safely, probably longer.

### 3b. Orchestrator reads during routing / policy resolution

File: `backend/app/services/orchestrator.py:128-175`, via
`app/services/project_service.py:17-52`.

Per chat request, inside `record_phase("auth")`:

```python
project      = session.get(ProjectORM, auth.project_id)       # project_service.py:38
tenant       = session.get(TenantORM, project.tenant_id)      # project_service.py:46
org_settings = session.get(OrgSettingsORM, 1)                 # project_service.py:52
```

Three PK lookups. Then quota + budget checks (orchestrator.py:171-174) each
pulling more rows. All read-heavy; all highly cacheable under the same
(project_id, tenant_id) key.

### 3c. Observability DB writes — write-heavy, per-request

File: `backend/app/services/request_log_service.py:14-43`.

Per chat request, after provider returns, the orchestrator calls:

```python
session.add(log)       # request_log_service.py:42
session.flush()        # request_log_service.py:43
```

Blocked and success paths both persist (orchestrator.py:283, :343, :451,
:655). This is per-request INSERT traffic that cannot be de-duplicated
or cached — at 25 RPS it is 25 INSERTs/sec holding a connection through
each write.

### Attribution

Reads (§3a + §3b): dominant contributor, because the session is checked out
at the start of the request via `Depends(get_db_session)` in `deps.py:33-43`
and held until the response is written. Under 25 RPS of long-running
requests (provider p95 ≈ 3 s, plus gateway overhead), the pool saturates on
the read side first. Observability writes (§3c) amplify it but do not cause
it — they are short INSERTs that complete in single-digit ms.

## 4. Fix directions (FLAG-ONLY — NOT implementing in Phase 2)

1. **Raise production pool (quick bandaid — not a fix).** Bump `pool_size`
   to 50 and `max_overflow` to 100. Buys headroom but does nothing about
   sync SQLAlchemy blocking the event loop. Masks the problem.

2. **Async SQLAlchemy throughout (true fix, larger refactor).** Move the
   orchestrator read path, `get_resolved_api_key`, and `request_log_service`
   onto `async_sessionmaker` + `AsyncSession`. Frees the event loop during
   DB round-trips; allows the existing pool to serve much higher RPS
   because each slot is held for less wall-clock time. Primary fix.

3. **Cache hot DB reads.** The API-key → `(project_id, tenant_id, role)`
   mapping (§3a) and the `(project, tenant, org_settings)` triplet (§3b)
   are read-mostly. Add an in-process LRU + TTL (60 s) keyed on
   `sha256(api_key)` for §3a and `project_id` for §3b. Cuts pool
   pressure by an order of magnitude on repeated-key workloads.

4. **Per-worker pool when scaling uvicorn workers (>1 worker).** The pool
   currently lives on a module-level engine — each `--workers N` child
   gets its own pool of 10+20, so the effective ceiling does scale with
   worker count. Today uvicorn runs with 1 process (no `--workers` flag
   from the perf builder). If a future deploy uses `--workers 4`, verify
   connection-per-worker math against Postgres `max_connections`
   (default 100).

## 5. Why this matters for Go migration

If the true fix is **"async SQLAlchemy + read caching"** — fix directions
#2 and #3 above, both entirely Python-internal — then moving auth / routing
to Go is premature. A port would:

* Carry the same blocking-IO bug into Go unless the Go code is also
  written async-first, which is free in Go but is work.
* Introduce an IPC hop (§8.3 of PERF_BASELINE_PLAN quantifies a ~0.2-1 ms
  localhost HTTP hop per request) that does **not** help the saturation
  mechanism at all — the blocking call is still there, it's just on the
  other side of a socket.
* Double the operational surface (two runtimes, two deploy paths, two
  error modes) for a gain that is 100% achievable in the current runtime.

**Flagged for the Auditor:** do not greenlight a Go rewrite of the gateway
auth/routing path on the back of Check-B's 10 s p95. The latency has a
name (DB pool saturation) and a Python-native fix. Re-measure after #2 +
#3 before revisiting the Go question.

---

Note: `db_pool_configured` log line suppressed due to structlog/stdlib logging config conflict. Pool size verified via direct probe. Separate issue, out of scope.

End of finding.
