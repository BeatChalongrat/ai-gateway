"""Runtime execution of custom detectors on the scan hot path (Sprint 6, E).

A custom detector row becomes a synchronous ``BaseDetector`` subclass
(:class:`CustomMatchDetector`) that the registry runs alongside the 31
built-ins. Design constraints that drive this module:

  * The scan path is SYNCHRONOUS — ``detect()`` cannot ``await``. So per-match
    bookkeeping (match counts, timeout failures, auto-disable) is deferred:
    ``detect()`` enqueues lightweight events onto a thread-safe deque, and the
    loader's async task drains them (:func:`drain_events`) into the DB.
  * ``detect()`` MUST NEVER raise — a bad custom detector can't be allowed to
    break the whole scan. Every path is wrapped; failures return ``[]``.
  * Per-attempt 50ms timeout via the ``regex`` module (inside the matching
    primitive). On timeout the detector enqueues a timeout event and yields
    nothing.
  * Status gates enforcement: ``active`` returns matches (enforced via the
    existing category→action policy); ``testing`` computes + counts matches
    for admin review but returns ``[]`` (no enforcement); ``draft``/
    ``disabled`` never reach here (the loader only caches active/testing).

Semantic custom detectors are validated + stored but NOT executed at runtime
in Sprint 6 (no new spaCy models ship) — ``build_detector`` skips them.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.observability.metrics import (
    DLP_CUSTOM_DETECTOR_LATENCY_SECONDS,
    DLP_CUSTOM_DETECTOR_MATCH_TOTAL,
    DLP_CUSTOM_DETECTOR_TIMEOUT_TOTAL,
)
from app.services.dlp.custom_detector_matching import run_matches
from app.services.dlp.detectors.base import BaseDetector

log = logging.getLogger(__name__)

# Deferred event queue drained by the loader's async task. ``deque.append`` /
# ``popleft`` are atomic in CPython, so the sync detect() and the async drain
# can share it without an extra lock. Events: ("match"|"timeout", detector_id).
_EVENTS: "deque[tuple[str, str]]" = deque(maxlen=10000)

_SEVERITY_MAP = {
    "low": SensitivityLevel.LOW,
    "medium": SensitivityLevel.MEDIUM,
    "high": SensitivityLevel.HIGH,
    "critical": SensitivityLevel.CRITICAL,
}

# Detection types executed at runtime in Sprint 6 (semantic is skipped).
_RUNTIME_TYPES = {"regex", "keyword_list"}


def enqueue_event(kind: str, detector_id: str) -> None:
    _EVENTS.append((kind, detector_id))


def drain_pending_events() -> list[tuple[str, str]]:
    """Pop all queued events (used by the loader drain + tests)."""
    out: list[tuple[str, str]] = []
    while True:
        try:
            out.append(_EVENTS.popleft())
        except IndexError:
            break
    return out


class CustomMatchDetector(BaseDetector):
    """A regex/keyword custom detector wrapped as a sync BaseDetector."""

    def __init__(self, data: dict) -> None:
        self._id = data["id"]
        self.name = f"custom:{data['name']}"
        self.category = data["category"]
        self.default_sensitivity = _SEVERITY_MAP.get(
            data.get("severity", "medium"), SensitivityLevel.MEDIUM
        )
        self._detection_type = data["detection_type"]
        self._pattern = data.get("pattern") or {}
        self._status = data.get("status", "active")

    def detect(self, text: str) -> list[DetectionMatch]:
        # Hard isolation: this method must never raise on the scan path.
        try:
            t0 = time.monotonic()
            try:
                spans = run_matches(self._detection_type, self._pattern, text)
            except TimeoutError:
                enqueue_event("timeout", self._id)
                DLP_CUSTOM_DETECTOR_TIMEOUT_TOTAL.labels(self._id).inc()
                log.warning("custom detector %s timed out", self._id)
                return []
            finally:
                DLP_CUSTOM_DETECTOR_LATENCY_SECONDS.labels(
                    self._id, self._detection_type
                ).observe(time.monotonic() - t0)

            if not spans:
                return []

            # Found something — count it (for both testing + active).
            enqueue_event("match", self._id)
            DLP_CUSTOM_DETECTOR_MATCH_TOTAL.labels(self._id, self.category).inc()

            if self._status != "active":
                # testing → log for admin review, but apply NO enforcement.
                log.info(
                    "custom detector %s (testing) matched %d span(s)",
                    self._id, len(spans),
                )
                return []

            return [
                DetectionMatch(
                    detector_name=self.name,
                    entity_type=f"custom_{self.category}",
                    category=self.category,
                    start=s,
                    end=e,
                    matched_text=t,
                    severity=self.default_sensitivity,
                    metadata={"custom_detector_id": self._id},
                )
                for (s, e, t) in spans
            ]
        except Exception:  # noqa: BLE001 — never break the scan
            log.exception("custom detector %s crashed", self._id)
            return []


def build_detector(data: dict) -> Optional[CustomMatchDetector]:
    """Build a runtime detector from a cached row dict, or None if unsupported.

    Returns None for semantic detectors (not executed in Sprint 6) and for any
    row that fails to construct — the loader skips Nones so one bad row can
    never poison the cache.
    """
    try:
        if data.get("detection_type") not in _RUNTIME_TYPES:
            return None
        return CustomMatchDetector(data)
    except Exception:  # noqa: BLE001
        log.warning("skipping unbuildable custom detector %s", data.get("id"))
        return None


async def drain_events(service) -> None:
    """Drain queued match/timeout events into the DB (async, off hot path).

    ``service`` is a ``CustomDetectorService``. On the 3rd consecutive timeout
    for a detector, auto-disable it (system action).
    """
    events = drain_pending_events()
    if not events:
        return
    # Collapse matches (one bump per detector per drain is enough for the
    # last_matched_at heartbeat); timeouts are recorded individually so the
    # threshold counter is accurate.
    matched: set[str] = set()
    for kind, detector_id in events:
        if kind == "match":
            matched.add(detector_id)
        elif kind == "timeout":
            try:
                reached = await service.record_timeout(detector_id)
                if reached:
                    await service.disable(
                        detector_id,
                        changed_by_user_id="system",
                        reason="auto-disabled: 3 consecutive regex timeouts",
                    )
            except Exception:  # noqa: BLE001
                log.warning("failed to record timeout for %s", detector_id)
    for detector_id in matched:
        try:
            await service.record_match(detector_id)
        except Exception:  # noqa: BLE001
            log.warning("failed to record match for %s", detector_id)
