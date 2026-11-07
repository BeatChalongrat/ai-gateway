"""Per-request phase timer — contextvar based, non-invasive.

Usage from orchestrator / routes::

    timer = PhaseTimer()
    token = _current_phase_timer.set(timer)
    try:
        with record_phase("auth"):
            ...
        with record_phase("rate_limit"):
            ...
    finally:
        _current_phase_timer.reset(token)
        timer.emit_structured_log(logger)

The timer is additive: repeated ``record_phase("dlp_regex")`` calls sum
into a single bucket. This matters because DLP runs on both inbound and
outbound — we want the total cost per phase, not one arbitrary snapshot.

Consumers:
    * ``MetricsMiddleware`` reads off the timer to populate
      ``gateway_request_phase_duration_seconds``.
    * The orchestrator emits a ``request_phases`` structured log line
      with every timing — the field names are the canonical phase
      identifiers used by the perf-baseline report template.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from app.observability.metrics import REQUEST_PHASE_DURATION


# Canonical phase names — changing these breaks the perf-baseline report
# template. Add new phases to the end; do not rename.
CANONICAL_PHASES: tuple[str, ...] = (
    "auth",
    "rate_limit",
    "routing",
    "dlp_regex",
    "dlp_ner",
    "guardrails",
    "cache",
    "provider",
    "response_assembly",
)


@dataclass
class PhaseTimer:
    """Accumulates time spent in each named phase of a single request."""

    phases_ms: dict[str, float] = field(default_factory=dict)
    started_at: float = field(default_factory=time.perf_counter)

    def add(self, phase: str, seconds: float) -> None:
        """Add ``seconds`` to the running total for ``phase``."""
        ms = seconds * 1000.0
        self.phases_ms[phase] = self.phases_ms.get(phase, 0.0) + ms
        try:
            REQUEST_PHASE_DURATION.labels(phase=phase).observe(seconds)
        except Exception:
            # Never let telemetry break a request. Prometheus histogram
            # observe can't really fail, but keep the guard anyway so
            # unit tests that stub the registry don't blow up.
            pass

    def as_log_fields(self) -> dict[str, int]:
        """Render the accumulated phases as a dict of ``<phase>_ms=int``.

        The field names exactly match the directive's allowed-fixes list
        (``auth_ms``, ``rate_limit_ms``, …) so the structured log line is
        parsable by the k6 runner without any translation step.
        """
        out: dict[str, int] = {}
        for phase in CANONICAL_PHASES:
            out[f"{phase}_ms"] = int(round(self.phases_ms.get(phase, 0.0)))
        out["total_ms"] = int(round((time.perf_counter() - self.started_at) * 1000.0))
        return out


_current_phase_timer: ContextVar[PhaseTimer | None] = ContextVar(
    "current_phase_timer", default=None
)


def current_phase_timer() -> PhaseTimer | None:
    """Return the timer bound to the current request, or None."""
    return _current_phase_timer.get()


@contextmanager
def record_phase(phase: str) -> Iterator[None]:
    """Time a block and attribute the duration to ``phase``.

    No-op (aside from the timing) when no PhaseTimer is bound to the
    current context. This lets the orchestrator call ``record_phase``
    unconditionally — unit tests that don't set up a timer still pass,
    and background tasks that happen to invoke the same code path don't
    crash on missing context.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        timer = _current_phase_timer.get()
        if timer is not None:
            timer.add(phase, elapsed)
        else:
            # Even without a timer, still feed the process-wide histogram
            # so scraping /metrics off an untimed code path (e.g. a unit
            # test's direct orchestrator call) yields signal.
            try:
                REQUEST_PHASE_DURATION.labels(phase=phase).observe(elapsed)
            except Exception:
                pass


def bind_phase_timer(timer: PhaseTimer):
    """Bind a timer to the current context and return the reset token."""
    return _current_phase_timer.set(timer)


def unbind_phase_timer(token) -> None:
    """Release a timer previously bound with ``bind_phase_timer``."""
    _current_phase_timer.reset(token)
