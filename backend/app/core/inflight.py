"""FIX26 §2.2 — per-worker in-flight request registry + admission cap.

The FIX24 backend wedge presented as "request START, never a completion,
every later request hangs, /health stays 200". Two things this module
gives the readiness probe and the chat route to make that class of
failure *visible* and *bounded*, whatever its root cause:

* a registry of chat requests currently in flight in THIS worker, with
  their start time — so ``/ready`` can say "there are N requests older
  than the hard timeout" (= something is stuck past its deadline);
* a bounded admission semaphore — one stuck request cannot consume the
  worker: when the cap is reached, new chat requests get a fast 503
  instead of queueing behind the stuck ones.

Worker-local by design (gunicorn runs one event loop per process); the
probe reports the worker it landed on. No I/O here; unit-tested.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class InflightSnapshot:
    count: int
    oldest_age_s: float
    stuck: int          # requests older than ``stuck_after_s``
    limit: int
    available: int      # admission slots free


class AdmissionTimeout(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"chat admission cap reached ({limit} in flight in this worker)")
        self.limit = limit


class InflightRegistry:
    def __init__(self, limit: int = 64) -> None:
        self._limit = max(1, int(limit))
        self._sem: asyncio.Semaphore | None = None
        self._started: dict[str, float] = {}
        self._seq = 0

    # The semaphore is created lazily on the running loop (module-level
    # construction at import time would bind it to no loop).
    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._limit)
        return self._sem

    @property
    def limit(self) -> int:
        return self._limit

    def snapshot(self, stuck_after_s: float) -> InflightSnapshot:
        now = time.monotonic()
        ages = [now - t for t in self._started.values()]
        return InflightSnapshot(
            count=len(ages),
            oldest_age_s=round(max(ages), 3) if ages else 0.0,
            stuck=sum(1 for a in ages if a > stuck_after_s),
            limit=self._limit,
            available=max(0, self._limit - len(ages)),
        )

    async def admit(self, *, timeout_s: float, request_id: str | None = None) -> str:
        """Take a slot (bounded wait) and register the request; returns the key.

        Raises ``AdmissionTimeout`` when no slot frees within ``timeout_s``.
        """
        sem = self._semaphore()
        try:
            await asyncio.wait_for(sem.acquire(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise AdmissionTimeout(self._limit) from exc
        self._seq += 1
        key = f"{request_id or 'req'}#{self._seq}"
        self._started[key] = time.monotonic()
        return key

    def release(self, key: str) -> bool:
        """Idempotent: the second release of a key is a no-op."""
        if self._started.pop(key, None) is None:
            return False
        self._semaphore().release()
        return True


# The one registry per worker process. ``configure`` lets startup apply
# the settings value; tests build their own instances.
_registry = InflightRegistry()


def registry() -> InflightRegistry:
    return _registry


def configure(limit: int) -> InflightRegistry:
    global _registry
    _registry = InflightRegistry(limit)
    return _registry
