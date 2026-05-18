"""Thin gRPC client for the Go ``log-worker`` sidecar.

The orchestrator does not call this directly; it dispatches through
``app.services.request_log_queue.enqueue`` which checks
``settings.log_worker_engine`` and routes to either the legacy
asyncio queue (engine=python) or this client (engine=go).

Failure modes (all observable in the ``log_worker_unavailable``
logger event):

* ``grpcio`` not installed → ``LogWorkerUnavailable`` raised once
  at import time. The queue treats this as "engine=go is mis-
  configured" and falls through to the Python path.
* Channel construction error → same path.
* RPC timeout / RPC error → caller (queue) catches and falls back
  to ``_sync_fallback``.

The client is intentionally minimal — there's no streaming, no
retries, no client-side batching. The sidecar already buffers; the
gateway's job is to hand off one record per request and move on.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from app.domain.observability import RequestRecord


logger = structlog.get_logger(__name__)


class LogWorkerUnavailable(RuntimeError):
    """Raised when grpcio isn't installed or the stub cannot be imported."""


def _import_stubs() -> tuple[Any, Any, Any]:
    """Import the generated stubs lazily.

    ``grpcio`` is an optional dependency — older deployments that
    stayed on the Python queue path don't have it. We raise
    ``LogWorkerUnavailable`` so the queue can fall back cleanly.
    """
    try:
        import grpc  # noqa: F401  -- imported for side-effect / probe
        from . import logworker_pb2 as pb
        from . import logworker_pb2_grpc as pb_grpc
        import grpc as _grpc

        return _grpc, pb, pb_grpc
    except Exception as exc:  # noqa: BLE001
        raise LogWorkerUnavailable(
            f"log-worker gRPC stubs are not importable: {exc}. "
            "Set LOG_WORKER_ENGINE=python or install grpcio."
        ) from exc


def record_to_proto(record: "RequestRecord") -> Any:
    """Translate a :class:`RequestRecord` into a ``LogRecord`` proto.

    Mirrors the field set persisted by ``RequestLogService.persist``
    so the Go COPY produces identical rows to the Python path.
    String columns that are ``None`` on the record become empty
    strings on the wire — the Go inserter maps empty back to SQL
    NULL via ``emptyToNil`` so the round-trip preserves nullness.
    """
    import time as _time

    _grpc, pb, _ = _import_stubs()
    return pb.LogRecord(
        request_id=record.request_id or "",
        tenant_id=record.tenant_id or "",
        project_id=record.project_id or "",
        provider=record.provider or "",
        model=record.model or "",
        status=(
            record.status.value
            if hasattr(record.status, "value")
            else str(record.status)
        ),
        policy_action=record.policy_action or "",
        latency_ms=float(record.latency_ms or 0),
        prompt_tokens=int(record.prompt_tokens or 0),
        completion_tokens=int(record.completion_tokens or 0),
        total_tokens=int(record.total_tokens or 0),
        estimated_cost_usd=float(record.estimated_cost_usd or 0.0),
        error_code=record.error_code or "",
        router_used=record.router_used or "",
        fallback_used=bool(record.fallback_used),
        # RequestRecord.fallback_attempts is now a list[dict] (the JSONB
        # column handles serialization for the Python path). The proto
        # wire format keeps a string — serialize here.
        fallback_attempts_json=(
            json.dumps(record.fallback_attempts, ensure_ascii=False)
            if record.fallback_attempts
            else ""
        ),
        original_provider=record.original_provider or "",
        original_model=record.original_model or "",
        dlp_input_findings=int(record.dlp_input_findings or 0),
        dlp_input_action=record.dlp_input_action or "",
        dlp_output_findings=int(record.dlp_output_findings or 0),
        dlp_categories=(
            ",".join(record.dlp_categories)
            if record.dlp_categories
            else ""
        ),
        policy_profile_id=record.policy_profile_id or "",
        input_raw=record.input_raw or "",
        input_masked=record.input_masked or "",
        output_raw=record.output_raw or "",
        output_final=record.output_final or "",
        dlp_findings_json=record.dlp_findings_json or "",
        input_raw_encrypted=record.input_raw_encrypted or "",
        created_at_unix_ms=int(_time.time() * 1000),
    )


class LogWorkerClient:
    """Process-singleton gRPC client.

    Holds one channel per process (configured channel-pool semantics
    inside grpcio cover concurrent RPCs over the single channel). The
    channel is built lazily on first ``enqueue`` so a misconfigured
    ``log_worker_grpc_address`` doesn't crash the process at import.
    """

    _instance_lock = threading.Lock()
    _instance: "LogWorkerClient | None" = None

    def __init__(self, address: str, timeout_seconds: float) -> None:
        self.address = address
        self.timeout_seconds = timeout_seconds
        self._channel: Any | None = None
        self._stub: Any | None = None
        self._build_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Singleton helpers
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "LogWorkerClient":
        from app.core.config import settings

        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(
                    address=settings.log_worker_grpc_address,
                    timeout_seconds=settings.log_worker_grpc_timeout_seconds,
                )
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the cached singleton — used by tests that need a fresh channel."""
        with cls._instance_lock:
            if cls._instance is not None and cls._instance._channel is not None:
                try:
                    cls._instance._channel.close()
                except Exception:  # noqa: BLE001
                    pass
            cls._instance = None

    # ------------------------------------------------------------------
    # RPC
    # ------------------------------------------------------------------

    async def enqueue(self, record: "RequestRecord") -> bool:
        """Send one record. Returns True iff the sidecar accepted it.

        Raises ``LogWorkerUnavailable`` only when grpcio / the stubs
        cannot be imported at all. Per-call failures (timeout,
        Unavailable, etc.) are caught and returned as ``False`` so the
        queue's caller can fall back to the legacy persist without
        an except wall.
        """
        try:
            grpc, _pb, _pb_grpc = _import_stubs()
        except LogWorkerUnavailable:
            raise

        stub = self._ensure_stub(grpc, _pb_grpc)
        proto_record = record_to_proto(record)
        try:
            response = await stub.Enqueue(
                proto_record, timeout=self.timeout_seconds
            )
            return bool(getattr(response, "accepted", False))
        except grpc.RpcError as exc:  # type: ignore[union-attr]
            logger.warning(
                "log_worker_rpc_failed",
                code=getattr(exc, "code", lambda: None)(),
                details=str(exc)[:200],
            )
            return False
        except asyncio.TimeoutError:
            logger.warning("log_worker_rpc_timeout")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "log_worker_rpc_unexpected_error",
                error=str(exc)[:200],
            )
            return False

    def _ensure_stub(self, grpc: Any, pb_grpc: Any) -> Any:
        with self._build_lock:
            if self._stub is not None:
                return self._stub
            self._channel = grpc.aio.insecure_channel(
                self.address,
                options=(
                    # Keepalive matches the gRPC defaults the Go side
                    # accepts without explicit configuration.
                    ("grpc.keepalive_time_ms", 30_000),
                    ("grpc.keepalive_timeout_ms", 5_000),
                    ("grpc.keepalive_permit_without_calls", 1),
                    ("grpc.http2.max_pings_without_data", 0),
                ),
            )
            self._stub = pb_grpc.LogWorkerServiceStub(self._channel)
            return self._stub


__all__ = [
    "LogWorkerClient",
    "LogWorkerUnavailable",
    "record_to_proto",
]
