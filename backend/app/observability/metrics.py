"""Prometheus metric declarations + registry wiring.

The registry is per-process. Under gunicorn ``--workers=N`` each worker
holds its own counters — a production multi-worker deployment should
add the ``prometheus_client.multiprocess`` collector, but that requires
setting ``PROMETHEUS_MULTIPROC_DIR`` and using ``Collector`` overrides.
For the perf-baseline work the backend is exercised as a single uvicorn
process so the default in-process registry is sufficient.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ---------------------------------------------------------------------------
# Bucket sets — tuned for web latency in seconds (not ms).
# The required exact metric names and label shapes come from directive §2.
# ---------------------------------------------------------------------------

_WEB_LATENCY_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)
# Per-phase latency is typically sub-100ms except ``provider_call`` and
# ``dlp_ner``. Keep the same buckets so we can stack the histogram across
# phases without bucket mismatch.
_PHASE_LATENCY_BUCKETS = _WEB_LATENCY_BUCKETS

metrics_registry = CollectorRegistry()


# ---------------------------------------------------------------------------
# HTTP-level metrics (populated by MetricsMiddleware).
# ---------------------------------------------------------------------------

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Duration of HTTP requests in seconds, labeled by templated route "
    "(not concrete path, to bound cardinality), method, and status.",
    labelnames=("route", "method", "status"),
    buckets=_WEB_LATENCY_BUCKETS,
    registry=metrics_registry,
)

HTTP_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "Number of HTTP requests currently being processed by the app.",
    labelnames=("route",),
    registry=metrics_registry,
)


# ---------------------------------------------------------------------------
# Request-phase attribution (populated by orchestrator + public_chat hooks).
# Label names match the directive (§4 of allowed-fixes) so the k6 runner
# can feed them directly into the required per-phase cost table.
# ---------------------------------------------------------------------------

REQUEST_PHASE_DURATION = Histogram(
    "gateway_request_phase_duration_seconds",
    "Per-phase latency of a chat completions request (auth, rate_limit, "
    "routing, dlp_regex, dlp_ner, guardrails, cache, provider, "
    "response_assembly). The ``phase`` label uses the canonical names "
    "consumed by the perf-baseline runner.",
    labelnames=("phase",),
    buckets=_PHASE_LATENCY_BUCKETS,
    registry=metrics_registry,
)


# ---------------------------------------------------------------------------
# LLM provider latency (wraps provider_dispatcher.dispatch call sites).
# ---------------------------------------------------------------------------

LLM_PROVIDER_LATENCY = Histogram(
    "llm_provider_latency_seconds",
    "Latency of outbound LLM provider calls, excluding gateway overhead.",
    labelnames=("provider",),
    buckets=_WEB_LATENCY_BUCKETS,
    registry=metrics_registry,
)


# ---------------------------------------------------------------------------
# DLP scan sub-phases — split into regex / ner so Round B vs Round A
# deltas are readable without cross-table math.
# ---------------------------------------------------------------------------

DLP_SCAN_DURATION = Histogram(
    "dlp_scan_duration_seconds",
    "Duration of DLP scans split by phase (regex or ner).",
    labelnames=("phase",),
    buckets=_PHASE_LATENCY_BUCKETS,
    registry=metrics_registry,
)


# ---------------------------------------------------------------------------
# Datastore pool / command metrics.
# ``db_pool_active`` is set by a 5s background task reading SQLAlchemy
# pool stats. ``redis_command_duration_seconds`` is populated at the
# rate-limiter and cache call sites (documented coverage — full redis-py
# instrumentation would require monkey-patching the client).
# ---------------------------------------------------------------------------

DB_POOL_ACTIVE = Gauge(
    "db_pool_active",
    "Active SQLAlchemy connections checked out from the pool.",
    registry=metrics_registry,
)

# Phase-1 async request-log queue depth. Sampled at every enqueue() and
# after every batch flush so saturation is visible in /metrics without
# needing a separate sampler task. A persistent non-zero value means
# the worker can't keep up with incoming records — bump
# ``REQUEST_LOG_BATCH_SIZE`` or check Postgres write latency.
REQUEST_LOG_QUEUE_SIZE = Gauge(
    "request_log_queue_size",
    "Pending RequestRecord items in the async request-log queue.",
    registry=metrics_registry,
)

REDIS_COMMAND_DURATION = Histogram(
    "redis_command_duration_seconds",
    "Duration of Redis commands instrumented at the rate-limiter and "
    "cache call sites. Other redis-py calls (incr on cache counters, "
    "get_stats) are NOT wrapped — see perf/README.md 'coverage' section.",
    labelnames=("operation",),
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
    registry=metrics_registry,
)


# ---------------------------------------------------------------------------
# Re-audit Track O — the failure modes the polyglot design hinges on were
# previously unobservable (audit H4/H5/H6/H8). These close that gap.
# ---------------------------------------------------------------------------

# DB pool saturation. ``db_pool_active`` (above) is checked-out count;
# ``db_pool_overflow`` climbing toward ``db_pool_max`` is the leading
# indicator of imminent pool-timeout 500s (the connection-per-request
# ceiling the Phase-2 audit flagged). ``db_pool_checkout_timeouts_total``
# counts requests that actually hit ``pool_timeout`` and 500'd.
DB_POOL_OVERFLOW = Gauge(
    "db_pool_overflow",
    "SQLAlchemy overflow connections currently in use (beyond pool_size). "
    "Approaching db_pool_max means imminent checkout timeouts.",
    registry=metrics_registry,
)

DB_POOL_MAX = Gauge(
    "db_pool_max",
    "Configured maximum connections per worker (pool_size + max_overflow).",
    registry=metrics_registry,
)

DB_POOL_CHECKOUT_TIMEOUTS = Counter(
    "db_pool_checkout_timeouts_total",
    "Count of DB-session checkouts that exceeded pool_timeout and failed "
    "(measured at the request DB-session dependency).",
    registry=metrics_registry,
)

# ASYNC pool saturation — the chat hot path (``/v1/chat/completions``)
# uses a SEPARATE async engine/pool from the sync gauges above. The load
# baseline at 150 concurrent exhausted THIS pool ("QueuePool limit of size
# 10 overflow 15 reached, timeout 5.00" -> 500), which the sync
# ``db_pool_*`` series did not reflect. These close that observability gap.
ASYNC_DB_POOL_CHECKED_OUT = Gauge(
    "async_db_pool_checked_out",
    "Async-engine connections currently checked out (chat hot path).",
    registry=metrics_registry,
)

ASYNC_DB_POOL_OVERFLOW = Gauge(
    "async_db_pool_overflow",
    "Async-engine overflow connections in use beyond pool_size. "
    "Approaching async_db_pool_max means imminent checkout-timeout 500s.",
    registry=metrics_registry,
)

ASYNC_DB_POOL_MAX = Gauge(
    "async_db_pool_max",
    "Configured async max connections per worker (pool_size + max_overflow).",
    registry=metrics_registry,
)

ASYNC_DB_POOL_CHECKOUT_TIMEOUTS = Counter(
    "async_db_pool_checkout_timeouts_total",
    "Async DB-session checkouts that exceeded pool_timeout and 500'd "
    "(measured at the chat hot-path async DB dependency).",
    registry=metrics_registry,
)

# Sidecar fail-open / fallback visibility. A flapping sidecar previously
# only showed up in logs; ``sidecar_fallback_total`` makes degradation
# alertable. ``engine`` ∈ {dlp, rate_limiter, log_worker};
# ``reason`` ∈ {timeout, error, unavailable, rejected}.
SIDECAR_FALLBACK_TOTAL = Counter(
    "sidecar_fallback_total",
    "Times a polyglot sidecar call fell back to the in-process path.",
    labelnames=("engine", "reason"),
    registry=metrics_registry,
)

SIDECAR_REQUEST_TOTAL = Counter(
    "sidecar_request_total",
    "Sidecar dispatch outcomes. status ∈ {success, fallback}.",
    labelnames=("engine", "status"),
    registry=metrics_registry,
)

# Rate-limiter degradation (audit H6/H8). ``rate_limit_fail_open_total``:
# Redis was unreachable so the request was admitted UNLIMITED (cost/DoS
# exposure). ``rate_limit_lua_fallback_total``: the atomic Lua EVAL errored
# and the limiter silently fell to the NON-atomic sequential path
# (over-admission race returns).
RATE_LIMIT_FAIL_OPEN_TOTAL = Counter(
    "rate_limit_fail_open_total",
    "Requests admitted because the rate-limiter's Redis backend was down "
    "(fail-open — NOT rate limited).",
    registry=metrics_registry,
)

RATE_LIMIT_LUA_FALLBACK_TOTAL = Counter(
    "rate_limit_lua_fallback_total",
    "Times the atomic Lua EVAL failed and the limiter fell back to the "
    "non-atomic sequential path (over-admission race possible).",
    registry=metrics_registry,
)

RATE_LIMIT_CONSERVATIVE_DENY_TOTAL = Counter(
    "rate_limit_conservative_deny_total",
    "Requests denied by the in-process conservative limiter during a Redis "
    "outage (only active when RATE_LIMIT_REDIS_OUTAGE_MODE=conservative).",
    registry=metrics_registry,
)

# Event-loop block monitor (audit H4). Previously print()-only; now an
# alertable Prometheus signal. The histogram captures the lag
# distribution; the counter tallies >100ms blocks.
EVENT_LOOP_BLOCK_SECONDS = Histogram(
    "event_loop_block_seconds",
    "Measured event-loop lag from the 1s monitor tick (how long the loop "
    "was blocked beyond the expected sleep).",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=metrics_registry,
)

EVENT_LOOP_BLOCK_TOTAL = Counter(
    "event_loop_block_total",
    "Count of detected event-loop blocks exceeding 100ms.",
    registry=metrics_registry,
)


# ---------------------------------------------------------------------------
# Media generation (Sprint 1 — multi-modal generation foundation).
# Declared now so ``/metrics`` exposes the series (with zero values) before
# any job runs; the job loop + Sprint 2 provider adapters populate the
# labels. ``modality`` ∈ {video, audio_tts, music, image_edit};
# ``status`` ∈ {done, failed}; ``reason`` is the generation_jobs.error_code
# (e.g. no_adapter, provider_error, timeout).
# ---------------------------------------------------------------------------

GEN_DURATION_SECONDS = Histogram(
    "gen_duration_seconds",
    "End-to-end media-generation job duration in seconds (submit → terminal).",
    labelnames=("modality", "provider", "status"),
    buckets=(5, 15, 30, 60, 120, 300, 600),
    registry=metrics_registry,
)

GEN_COST_CREDITS = Histogram(
    "gen_cost_credits",
    "Credits charged per completed media-generation job.",
    labelnames=("modality", "provider"),
    buckets=(10, 50, 200, 500, 1000, 5000, 20000),
    registry=metrics_registry,
)

GEN_FAILURE_TOTAL = Counter(
    "gen_failure_total",
    "Media-generation job failures, labeled by failure reason (error_code).",
    labelnames=("modality", "provider", "reason"),
    registry=metrics_registry,
)

GEN_JOBS_ACTIVE = Gauge(
    "gen_jobs_active",
    "Media-generation jobs currently in the 'processing' state.",
    labelnames=("modality",),
    registry=metrics_registry,
)

GEN_QUEUE_DEPTH = Gauge(
    "gen_queue_depth",
    "Queued media-generation jobs awaiting a worker.",
    registry=metrics_registry,
)


def render_prometheus_exposition() -> tuple[bytes, str]:
    """Serialize the registry into the Prometheus text exposition format.

    Returns a ``(body, content_type)`` tuple. ``CONTENT_TYPE_LATEST`` is
    the OpenMetrics-compatible ``text/plain; version=0.0.4; charset=utf-8``
    header per the directive.
    """
    return generate_latest(metrics_registry), CONTENT_TYPE_LATEST
