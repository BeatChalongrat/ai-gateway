"""Observability — Prometheus metrics + per-phase request timing.

This package is load-bearing for the perf-baseline work. It exposes:
    * ``metrics_registry`` — the singleton ``prometheus_client.CollectorRegistry``
      used by ``GET /metrics``.
    * Pre-declared metric objects (histograms / gauges) imported by the
      middleware and the hot-path code that records them.
    * A ``PhaseTimer`` contextvar + context manager used by the orchestrator
      and streaming route to attribute per-phase latency to a request.

Kept deliberately lightweight (no heavy imports at module load) so that
``import app.observability`` is cheap on startup.
"""

from app.observability.metrics import (
    DB_POOL_ACTIVE,
    DLP_SCAN_DURATION,
    HTTP_IN_FLIGHT,
    HTTP_REQUEST_DURATION,
    LLM_PROVIDER_LATENCY,
    REDIS_COMMAND_DURATION,
    REQUEST_PHASE_DURATION,
    metrics_registry,
    render_prometheus_exposition,
)
from app.observability.phase_timer import (
    PhaseTimer,
    current_phase_timer,
    record_phase,
)

__all__ = [
    "DB_POOL_ACTIVE",
    "DLP_SCAN_DURATION",
    "HTTP_IN_FLIGHT",
    "HTTP_REQUEST_DURATION",
    "LLM_PROVIDER_LATENCY",
    "REDIS_COMMAND_DURATION",
    "REQUEST_PHASE_DURATION",
    "PhaseTimer",
    "current_phase_timer",
    "metrics_registry",
    "record_phase",
    "render_prometheus_exposition",
]
