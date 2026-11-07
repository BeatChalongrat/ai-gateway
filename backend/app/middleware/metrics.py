"""Request metrics middleware — feeds both the legacy in-memory
``get_metrics()`` dict (read by ``GET /metrics.json``-style debug
endpoints) AND the Prometheus histograms in ``app.observability``.

Two behaviours were changed for the perf-baseline work:

1. **Cardinality fix.** The previous implementation labeled by
   ``scope["path"]`` — the concrete URL. On routes like
   ``/admin/requests/{id}``, ``/portal/projects/{id}/keys/{key_id}``,
   or any route under ``/static/*``, this produced unbounded
   cardinality. We now resolve the route template via
   ``scope["route"].path`` (populated by Starlette after routing),
   falling back to ``"unknown"`` when no route matched (404s, the
   CORS preflight on an unknown path, etc.). The legacy dict also
   uses the templated path to stay consistent.

2. **Per-request PhaseTimer.** A fresh ``PhaseTimer`` is bound to the
   contextvar before the downstream handler runs, and released after
   the response completes. The orchestrator, provider dispatcher,
   and DLP registry use ``record_phase(...)`` to attribute their work
   to that timer. On response completion, the accumulated per-phase
   values are emitted as a structured log line and the total request
   time feeds ``http_request_duration_seconds``.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

import structlog
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.metrics import HTTP_IN_FLIGHT, HTTP_REQUEST_DURATION
from app.observability.phase_timer import (
    PhaseTimer,
    bind_phase_timer,
    unbind_phase_timer,
)


logger = structlog.get_logger("request_phases")
_std_logger = logging.getLogger("request_phases")

_start_time = time.time()
_request_counts: dict[str, int] = defaultdict(int)
_status_counts: dict[int, int] = defaultdict(int)
_error_counts: dict[str, int] = defaultdict(int)


def get_metrics() -> dict:
    """Legacy in-memory counter view (kept for existing callers).

    The new Prometheus exposition lives at ``GET /metrics`` — this
    function backs a human-friendly JSON view that the admin UI and
    smoke tests already consume.
    """
    return {
        "uptime_seconds": round(time.time() - _start_time),
        "total_requests": sum(_request_counts.values()),
        "total_errors": sum(_error_counts.values()),
        "requests_by_route": dict(_request_counts),
        "requests_by_status": dict(_status_counts),
        "errors_by_route": dict(_error_counts),
    }


def _resolve_route_template(scope: Scope) -> str:
    """Return the templated route path for a request, or ``"unknown"``.

    Starlette populates ``scope["route"]`` during ``app.__call__``
    dispatch, but only AFTER routing succeeds. To capture the template
    for timing middleware that runs before routing, we replay
    ``app.routes`` ourselves. The replay is cheap (~a few microseconds
    for ~80 routes) and avoids the timing gap where scope["route"]
    is not yet set.
    """
    # Fast path — Starlette already set it.
    route = scope.get("route")
    if route is not None:
        path = getattr(route, "path", None)
        if path:
            return path

    # Slow path — walk the router. We don't hold a reference to the
    # FastAPI app here, so use scope['app'].routes which Starlette
    # guarantees to populate.
    app = scope.get("app")
    if app is None or not hasattr(app, "routes"):
        return "unknown"

    for candidate in app.routes:
        try:
            match, _ = candidate.matches(scope)
        except Exception:
            continue
        if match == Match.FULL:
            return getattr(candidate, "path", None) or "unknown"

    return "unknown"


class MetricsMiddleware:
    """ASGI middleware that emits both in-memory counters and Prometheus
    histograms, and binds a per-request ``PhaseTimer``.

    Light enough that it can sit in the always-on middleware stack — no
    ``BaseHTTPMiddleware``, no response body buffering.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        # Resolve route template before the handler runs so we pass the
        # right label even when the handler raises before routing is
        # finalized (rare, but the Prometheus middleware in other stacks
        # got this wrong for years).
        route = _resolve_route_template(scope)

        # Per-request phase timer — the orchestrator + route handlers
        # attribute their phases to this; we emit it on completion.
        timer = PhaseTimer()
        token = bind_phase_timer(timer)
        start = time.perf_counter()

        try:
            HTTP_IN_FLIGHT.labels(route=route).inc()
        except Exception:
            pass

        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            try:
                HTTP_IN_FLIGHT.labels(route=route).dec()
            except Exception:
                pass
            try:
                HTTP_REQUEST_DURATION.labels(
                    route=route, method=method, status=str(status_code)
                ).observe(elapsed)
            except Exception:
                pass

            # Legacy in-memory counters — keyed on route template now.
            _request_counts[route] += 1
            _status_counts[status_code] += 1
            if status_code >= 500:
                _error_counts[route] += 1

            # Emit the per-phase structured log line. Consumer is the
            # perf-baseline runner — it parses these to build the report
            # table. We keep it at INFO level so structlog's JSON
            # renderer picks it up.
            try:
                phase_fields = timer.as_log_fields()
                logger.info(
                    "request_phases",
                    route=route,
                    method=method,
                    status=status_code,
                    **phase_fields,
                )
            except Exception as exc:
                # Telemetry must never break a response.
                _std_logger.debug("phase_log_emit_failed: %s", exc)

            unbind_phase_timer(token)
