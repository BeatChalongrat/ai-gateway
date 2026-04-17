"""In-process background task runner.

Provides a simple bounded queue for deferred, non-request-critical work.
Tasks run in the event loop via asyncio — suitable for lightweight work
like audit persistence, webhook delivery, and cleanup.

For heavy workloads, swap for Celery/ARQ with a Redis broker.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Awaitable, Callable

import structlog

logger = structlog.get_logger(__name__)

MAX_QUEUE_SIZE = 1000


class BackgroundTaskRunner:
    """Simple async task runner with bounded queue and error isolation."""

    def __init__(self, max_queue: int = MAX_QUEUE_SIZE) -> None:
        self._queue: deque[tuple[Callable[..., Awaitable], tuple, dict]] = deque(
            maxlen=max_queue
        )
        self._running = False
        self._processed = 0
        self._failed = 0

    def enqueue(
        self,
        func: Callable[..., Awaitable],
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        """Add a task to the queue. Returns False if queue is full."""
        if len(self._queue) >= (self._queue.maxlen or MAX_QUEUE_SIZE):
            logger.warning("background_queue_full", dropped_task=func.__name__)
            return False
        self._queue.append((func, args, kwargs))
        return True

    async def process_all(self) -> int:
        """Process all queued tasks. Returns number processed."""
        count = 0
        while self._queue:
            func, args, kwargs = self._queue.popleft()
            try:
                await func(*args, **kwargs)
                self._processed += 1
                count += 1
            except Exception as exc:
                self._failed += 1
                logger.error(
                    "background_task_failed",
                    task=func.__name__,
                    error=str(exc),
                )
        return count

    @property
    def pending(self) -> int:
        return len(self._queue)

    @property
    def stats(self) -> dict:
        return {
            "pending": self.pending,
            "processed": self._processed,
            "failed": self._failed,
        }
