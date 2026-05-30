"""Async fire-and-forget request-log queue.

The gateway's success path used to call
``RequestLogService.persist(session, record)`` synchronously inside the
async request handler — every request paid one INSERT (with
``synchronous_commit=on`` that meant one fsync) before returning to
the caller. Even after Phase-1 Fix-3 turned synchronous_commit off,
the INSERT itself still serialized on the request's DB session.

This module moves the INSERT off the request path:

  1. ``enqueue(record, fallback_session=...)`` is called from the
     orchestrator. It is **synchronous and non-blocking** — it puts the
     record on an ``asyncio.Queue`` and returns immediately.
  2. A background worker started by the FastAPI lifespan drains the
     queue every ``settings.request_log_flush_interval_ms`` (or as
     soon as ``settings.request_log_batch_size`` items accumulate,
     whichever first) and bulk-inserts via ``session.bulk_save_objects``.
  3. On queue full (``settings.request_log_queue_max``), the oldest
     pending record is dropped to make room for the new one. This is
     deliberate — we'd rather lose a few audit rows under a Postgres
     outage than block the request path.

Test / pre-startup behaviour
----------------------------

The pytest suite uses an in-memory SQLite engine and does NOT run the
FastAPI lifespan. The worker is therefore **not** running during those
tests, and ``enqueue()`` falls back to a synchronous persist on the
caller's session — preserving the pre-Phase-1 behaviour exactly.

The fallback is keyed on ``self._queue is None``: once
``start_worker()`` has been awaited, every subsequent ``enqueue`` is
queued; until then (and after ``stop_worker``) it persists inline.

Alert evaluation
----------------

The orchestrator used to call ``get_by_id(session, request_id)`` to
hand the just-persisted row to the alert evaluator. With the queue
the row is not yet in the DB when the evaluator runs, so the
orchestrator now builds an unsaved in-memory ``RequestLogORM`` via
``build_request_log_orm(record)`` and passes that. The evaluator
only reads ``tenant_id`` / ``project_id`` from the row (see
``AlertEvaluator._scope_matches``), so an unsaved instance is fine.
"""

from __future__ import annotations

import asyncio
import time
from functools import partial
from typing import TYPE_CHECKING, Optional

import structlog

from app.core.config import settings
from app.domain.observability import RequestRecord
from app.models.request_log import RequestLogORM
from app.observability.metrics import (
    REQUEST_LOG_QUEUE_SIZE,
    SIDECAR_FALLBACK_TOTAL,
    SIDECAR_REQUEST_TOTAL,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)


def build_request_log_orm(record: RequestRecord) -> RequestLogORM:
    """Pure transformation: ``RequestRecord`` -> unsaved ``RequestLogORM``.

    Mirrors the field-mapping inside ``RequestLogService.persist`` so
    the bulk-insert path produces identical rows to the legacy sync
    path. Kept here (not on the service class) so the queue worker
    has no dependency on ``RequestLogService`` itself.
    """
    return RequestLogORM(
        request_id=record.request_id,
        tenant_id=record.tenant_id,
        project_id=record.project_id,
        provider=record.provider,
        model=record.model,
        status=record.status.value if hasattr(record.status, "value") else str(record.status),
        policy_action=record.policy_action,
        latency_ms=record.latency_ms,
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        total_tokens=record.total_tokens,
        estimated_cost_usd=record.estimated_cost_usd,
        reasoning_tokens=record.reasoning_tokens,
        reasoning_cost=record.reasoning_cost,
        error_code=record.error_code,
        dlp_input_findings=record.dlp_input_findings,
        dlp_input_action=record.dlp_input_action,
        dlp_output_findings=record.dlp_output_findings,
        dlp_categories=",".join(record.dlp_categories) if record.dlp_categories else None,
        router_used=record.router_used,
        policy_profile_id=record.policy_profile_id,
        input_raw=record.input_raw,
        input_masked=record.input_masked,
        output_raw=record.output_raw,
        output_final=record.output_final,
        dlp_findings_json=record.dlp_findings_json,
        input_raw_encrypted=record.input_raw_encrypted,
        fallback_used=bool(record.fallback_used),
        fallback_attempts=record.fallback_attempts,
        original_provider=record.original_provider,
        original_model=record.original_model,
    )


class _RequestLogQueue:
    """Singleton container for the queue + worker task.

    Implemented as a class so the queue object can be replaced cleanly
    on each ``start_worker`` call (the queue is tied to one event
    loop). The module-level ``enqueue`` / ``start_worker`` /
    ``stop_worker`` functions delegate to the shared ``_singleton``.
    """

    def __init__(self) -> None:
        self._queue: Optional[asyncio.Queue[RequestRecord]] = None
        self._worker: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._dropped: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    async def start_worker(self) -> None:
        if self.is_running:
            return
        self._queue = asyncio.Queue(maxsize=settings.request_log_queue_max)
        self._stop_event = asyncio.Event()
        self._worker = asyncio.create_task(self._run())
        REQUEST_LOG_QUEUE_SIZE.set(0)
        logger.info(
            "request_log_queue_started",
            max_size=settings.request_log_queue_max,
            batch_size=settings.request_log_batch_size,
            flush_interval_ms=settings.request_log_flush_interval_ms,
        )

    async def stop_worker(self) -> None:
        """Signal the worker to drain + exit, then await its completion.

        Lifespan calls this with a short timeout — if the worker hasn't
        finished draining, we cancel it. Any records left in the queue
        at that point are lost (acceptable on a graceful shutdown that
        is genuinely shutting the process down).
        """
        if not self.is_running:
            return
        assert self._stop_event is not None
        assert self._worker is not None
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._worker, timeout=5.0)
        except asyncio.TimeoutError:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        finally:
            self._worker = None
            self._queue = None
            self._stop_event = None
        logger.info("request_log_queue_stopped", dropped_total=self._dropped)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(
        self,
        record: RequestRecord,
        *,
        fallback_session: "Session | None" = None,
    ) -> None:
        """Fire-and-forget enqueue.

        Phase-3 fan-out: when ``settings.log_worker_engine == "go"``,
        the record is dispatched to the Go ``log-worker`` sidecar over
        gRPC. The RPC is fire-and-forget (max 0.5s timeout); any
        failure (sidecar down, RPC error, grpcio not installed) falls
        through to the legacy asyncio queue or the inline sync path,
        so a sidecar outage is silently survived.

        When the engine is ``python`` (default) and the worker is
        running, the record is queued and returns immediately. When
        the worker is **not** running (tests, the brief window before
        ``start_worker`` has finished, or after ``stop_worker`` on
        shutdown) the record is persisted inline: on the
        ``fallback_session`` when one is provided, otherwise on a
        fresh ``SessionLocal``.
        """
        if settings.log_worker_engine == "go":
            # Schedule the gRPC call on the running loop without
            # blocking the caller. If there is no running loop (sync
            # call from a thread), we drop straight to the legacy
            # queue/sync path so this path stays universally non-
            # blocking. A *per-RPC* failure (sidecar reachable but
            # erroring/timing out) is recovered later in
            # ``_on_grpc_done`` by re-routing to the legacy path — so a
            # dispatched-but-failed record is never lost.
            if self._dispatch_to_grpc(record):
                return
            # fall through on grpc *dispatch setup* failure → legacy path

        self._enqueue_local(record, fallback_session)

    def _enqueue_local(
        self,
        record: RequestRecord,
        fallback_session: "Session | None" = None,
    ) -> None:
        """Put a record on the legacy asyncio queue (drop-oldest on
        full); persist inline when no worker is running.

        Shared by the ``engine=python`` path and the ``engine=go``
        per-RPC fallback in ``_on_grpc_done`` — a flapping Go sidecar
        therefore degrades to the in-process queue instead of dropping
        audit rows.
        """
        if self._queue is None:
            self._sync_fallback(record, fallback_session)
            return
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            # Drop oldest pending record, then enqueue the new one.
            # We must call ``task_done`` for every ``get`` so the queue's
            # ``join`` count stays consistent (the worker uses ``get``
            # internally; ``get_nowait`` from outside follows the same
            # contract).
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._dropped += 1
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(record)
            except asyncio.QueueFull:
                # Still full somehow — drop the incoming record. Should
                # not happen in single-loop usage but the branch keeps
                # us non-crashing if the queue is hammered concurrently.
                self._dropped += 1
                logger.warning(
                    "request_log_queue_dropped",
                    request_id=record.request_id,
                    dropped_total=self._dropped,
                )
                return
        REQUEST_LOG_QUEUE_SIZE.set(self._queue.qsize())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _dispatch_to_grpc(self, record: RequestRecord) -> bool:
        """Schedule a fire-and-forget gRPC Enqueue on the running loop.

        Returns True if the dispatch was scheduled (caller can return);
        False on any setup failure (caller falls through to the legacy
        queue path). Per-RPC failures are NOT returned synchronously —
        they're handled in ``_on_grpc_done``, which re-routes the record
        to the legacy queue so a slow/erroring sidecar never blocks the
        request handler *and* never loses the audit row.
        """
        try:
            from app.services.log_worker_client import (
                LogWorkerClient,
                LogWorkerUnavailable,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "log_worker_client_import_failed", error=str(exc)[:200]
            )
            return False

        try:
            client = LogWorkerClient.instance()
        except LogWorkerUnavailable as exc:
            # grpcio missing or stubs broken — fall back to legacy
            # path. Logged at info so a misconfiguration shows up
            # without flooding production WARN.
            logger.info("log_worker_unavailable", reason=str(exc)[:200])
            return False

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — the caller is a sync thread. Skip the
            # gRPC path; the legacy queue / sync persist still runs.
            return False

        # ``create_task`` schedules on the running loop without
        # blocking. The done-callback (bound to this record) recovers
        # the row on any per-RPC failure; it never raises.
        task = loop.create_task(client.enqueue(record))
        task.add_done_callback(partial(self._on_grpc_done, record))
        return True

    def _on_grpc_done(self, record: RequestRecord, task: asyncio.Task) -> None:
        """Done-callback for the fire-and-forget gRPC Enqueue.

        On any per-RPC failure — the call returned ``accepted=False``
        (rejected) or raised (timeout / Unavailable / unexpected) — the
        record was dispatched but never landed on the legacy queue, so
        it would otherwise be silently lost (R5). Re-route it through
        ``_enqueue_local`` so a flapping Go sidecar degrades to the
        Python queue/inline persist instead of dropping audit rows.

        Runs on the event loop (done-callbacks do), so ``_enqueue_local``
        is non-blocking. The caller's request session is long gone by
        now, hence no ``fallback_session``.
        """
        try:
            ok = task.result()
        except asyncio.CancelledError:
            # Shutdown cancelled the in-flight RPC — drop quietly; the
            # graceful-shutdown drain owns remaining rows.
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "log_worker_grpc_task_failed", error=str(exc)[:200]
            )
            ok = False
        if ok:
            SIDECAR_REQUEST_TOTAL.labels(
                engine="log_worker", status="success"
            ).inc()
            return
        # gRPC rejected/failed → re-route to the local queue (audit H4:
        # count it so a flapping Go sidecar is visible, not just logged).
        SIDECAR_FALLBACK_TOTAL.labels(
            engine="log_worker", reason="rejected"
        ).inc()
        SIDECAR_REQUEST_TOTAL.labels(
            engine="log_worker", status="fallback"
        ).inc()
        logger.warning(
            "log_worker_grpc_fallback",
            request_id=record.request_id,
            reason="rpc_rejected_or_failed",
        )
        self._enqueue_local(record, None)

    def _sync_fallback(
        self,
        record: RequestRecord,
        session: "Session | None",
    ) -> None:
        """Persist a record synchronously when no worker is running.

        Mirrors ``RequestLogService.persist`` — we add to the supplied
        session and flush. The session's commit is the caller's
        responsibility (matches the legacy contract). When the caller
        does not supply a session we open one ourselves and commit
        immediately (used by background paths that pre-date the queue).
        """
        try:
            orm = build_request_log_orm(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("request_log_orm_build_failed", error=str(exc))
            return
        if session is not None:
            try:
                session.add(orm)
                session.flush()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "request_log_sync_fallback_failed_caller_session",
                    error=str(exc),
                )
            return
        # No caller session — open + commit a dedicated session.
        from app.core.db import SessionLocal

        own_session = SessionLocal()
        try:
            own_session.add(orm)
            own_session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "request_log_sync_fallback_failed_own_session", error=str(exc)
            )
            own_session.rollback()
        finally:
            own_session.close()

    async def _run(self) -> None:
        """Background worker loop. Exits when ``_stop_event`` is set."""
        assert self._queue is not None
        assert self._stop_event is not None
        flush_interval_s = max(
            0.001, settings.request_log_flush_interval_ms / 1000.0
        )
        batch_size = max(1, settings.request_log_batch_size)
        batch: list[RequestRecord] = []
        last_flush = time.monotonic()
        try:
            while not self._stop_event.is_set():
                # Wait either for an item or for the flush interval to
                # elapse. ``asyncio.wait_for`` raises ``TimeoutError`` on
                # the interval which we treat as "time to flush whatever
                # is in the batch".
                remaining = flush_interval_s - (time.monotonic() - last_flush)
                if remaining <= 0:
                    remaining = 0.001
                try:
                    record = await asyncio.wait_for(
                        self._queue.get(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    pass
                else:
                    batch.append(record)
                    self._queue.task_done()
                    # Drain any additional ready items into the same batch
                    # so a burst of N enqueues turns into one INSERT.
                    while len(batch) < batch_size:
                        try:
                            record = self._queue.get_nowait()
                            batch.append(record)
                            self._queue.task_done()
                        except asyncio.QueueEmpty:
                            break
                if batch and (
                    len(batch) >= batch_size
                    or (time.monotonic() - last_flush) >= flush_interval_s
                ):
                    await asyncio.to_thread(self._flush, batch)
                    batch = []
                    last_flush = time.monotonic()
                    REQUEST_LOG_QUEUE_SIZE.set(self._queue.qsize())
        except asyncio.CancelledError:
            raise
        finally:
            # Drain whatever is left so a graceful shutdown loses zero
            # rows that already made it onto the queue.
            try:
                while not self._queue.empty():
                    try:
                        batch.append(self._queue.get_nowait())
                        self._queue.task_done()
                    except asyncio.QueueEmpty:
                        break
                if batch:
                    await asyncio.to_thread(self._flush, batch)
                REQUEST_LOG_QUEUE_SIZE.set(0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("request_log_queue_drain_failed", error=str(exc))

    def _flush(self, batch: list[RequestRecord]) -> None:
        """Bulk-insert a batch on a dedicated session.

        Runs inside ``asyncio.to_thread`` so the worker's event loop
        stays responsive even when Postgres is slow. Each batch is
        committed independently — a per-row failure does not stop the
        worker, and the next batch starts with a fresh session.
        """
        from app.core.db import SessionLocal

        session = SessionLocal()
        try:
            session.bulk_save_objects(
                [build_request_log_orm(r) for r in batch]
            )
            session.commit()
            logger.info(
                "request_log_batch_flushed",
                count=len(batch),
            )
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.warning(
                "request_log_batch_flush_failed",
                error=str(exc),
                count=len(batch),
            )
        finally:
            session.close()


_singleton = _RequestLogQueue()


def enqueue(
    record: RequestRecord, *, fallback_session: "Session | None" = None
) -> None:
    """Module-level entry point used by the orchestrator."""
    _singleton.enqueue(record, fallback_session=fallback_session)


async def start_worker() -> None:
    """Start the background worker. Idempotent — safe to call twice."""
    await _singleton.start_worker()


async def stop_worker() -> None:
    """Drain + stop the worker. Idempotent — safe to call when not running."""
    await _singleton.stop_worker()


def queue_size() -> int:
    """Current pending size — exposed for diagnostics / tests."""
    q = _singleton._queue
    return q.qsize() if q is not None else 0


def is_running() -> bool:
    """Whether the worker task is alive."""
    return _singleton.is_running
