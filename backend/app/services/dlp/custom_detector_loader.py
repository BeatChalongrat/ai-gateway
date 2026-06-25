"""Hot-reload cache for custom detectors via Redis pub/sub (Sprint 6, D).

One instance per backend worker. Holds an in-memory snapshot of BUILT
synchronous detector instances that the DLP registry's custom-provider hook
reads on the scan hot path — ``get_cached_detectors()`` is sync and never
touches the DB or Redis, so a scan never blocks.

Lifecycle:
  * ``start()`` — eager initial build, then spawn two background tasks: a
    pub/sub subscriber (rebuilds the snapshot on any ``reload`` message) and a
    periodic drain (flushes deferred match/timeout events to the DB and
    auto-disables timed-out detectors).
  * ``stop()`` — cancel both tasks.

Fail-safe: any rebuild error drops the snapshot to an EMPTY list — a broken
cache must never block or falsify a scan (no custom detection beats wrong
custom detection). Mutations elsewhere call :func:`publish_reload` to fan the
invalidation out to every worker.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.services.dlp.custom_detector_runtime import build_detector, drain_events
from app.services.dlp.custom_detector_service import CustomDetectorService
from app.services.dlp.detectors.base import BaseDetector

log = logging.getLogger(__name__)

CUSTOM_DETECTOR_CHANNEL = "dlp:custom_detectors:reload"

_DRAIN_INTERVAL_SEC = 2.0


class CustomDetectorLoader:
    def __init__(self, redis_client, db_session_factory) -> None:
        self._redis = redis_client
        self._db_session_factory = db_session_factory
        self._detectors: list[BaseDetector] = []
        self._sub_task: Optional[asyncio.Task] = None
        self._drain_task: Optional[asyncio.Task] = None
        self._stopped = False

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        await self.rebuild()
        self._sub_task = asyncio.create_task(self._subscribe_loop())
        self._drain_task = asyncio.create_task(self._drain_loop())
        log.info(
            "custom detector loader started (%d active, subscribed to %s)",
            len(self._detectors), CUSTOM_DETECTOR_CHANNEL,
        )

    async def stop(self) -> None:
        self._stopped = True
        for task in (self._sub_task, self._drain_task):
            if task is not None:
                task.cancel()

    # -- hot-path read (SYNC) ----------------------------------------------

    def get_cached_detectors(self) -> list[BaseDetector]:
        """Return the current built snapshot. Called by the registry provider
        on the synchronous scan path — no await, no I/O."""
        return self._detectors

    # -- rebuild ------------------------------------------------------------

    async def rebuild(self) -> None:
        """Rebuild the snapshot from the active/testing rows. Fail-safe."""
        try:
            async with self._db_session_factory() as session:
                service = CustomDetectorService(session)
                rows = await service.list_active()
                snapshots = [r.to_dict() for r in rows]
            built = [d for d in (build_detector(s) for s in snapshots) if d is not None]
            self._detectors = built
        except Exception:  # noqa: BLE001 — never block/falsify a scan
            log.error("custom detector cache rebuild failed", exc_info=True)
            self._detectors = []

    # -- background tasks ---------------------------------------------------

    async def _subscribe_loop(self) -> None:
        # Poll get_message() rather than block on listen(): the shared Redis
        # pool sets socket_timeout=1s, which makes a blocking listen() raise
        # every second. get_message(timeout=…) returns None on no-message via
        # a non-destructive can_read(), so the subscription stays intact.
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(CUSTOM_DETECTOR_CHANNEL)
            while not self._stopped:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — transient blip; keep listening
                    await asyncio.sleep(0.5)
                    continue
                if message is not None and message.get("type") == "message":
                    log.info("custom detector reload received")
                    await self.rebuild()
        except asyncio.CancelledError:
            try:
                await pubsub.unsubscribe(CUSTOM_DETECTOR_CHANNEL)
            except Exception:  # noqa: BLE001
                pass
            raise

    async def _drain_loop(self) -> None:
        while not self._stopped:
            try:
                async with self._db_session_factory() as session:
                    await drain_events(CustomDetectorService(session))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("custom detector drain failed", exc_info=True)
            await asyncio.sleep(_DRAIN_INTERVAL_SEC)


async def publish_reload(redis_client) -> None:
    """Fan a cache-invalidation signal out to every worker."""
    await redis_client.publish(CUSTOM_DETECTOR_CHANNEL, "reload")
