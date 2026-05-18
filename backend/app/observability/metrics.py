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


def render_prometheus_exposition() -> tuple[bytes, str]:
    """Serialize the registry into the Prometheus text exposition format.

    Returns a ``(body, content_type)`` tuple. ``CONTENT_TYPE_LATEST`` is
    the OpenMetrics-compatible ``text/plain; version=0.0.4; charset=utf-8``
    header per the directive.
    """
    return generate_latest(metrics_registry), CONTENT_TYPE_LATEST
