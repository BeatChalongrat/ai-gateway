"""Thin gRPC client for the Rust ``rate-limiter`` sidecar.

The legacy in-process limiter and this client share a single public
contract — both return a dict shaped like::

    {
        "allowed": bool,
        "scope": str | None,
        "remaining": int,
        "reset_at": int,
        "limit": int,
    }

so ``RateLimitService.check_all_async`` can swap implementations
without changing any caller in the orchestrator.

Failure modes (all observable in the ``rate_limiter_unavailable``
logger event):

* ``grpcio`` not installed → ``RateLimiterUnavailable`` raised once
  at import time. The service catches and falls back to Python.
* RPC timeout / RPC error → caller catches and falls back to Python.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import structlog


logger = structlog.get_logger(__name__)


class RateLimiterUnavailable(RuntimeError):
    """Raised when grpcio isn't installed or the stubs cannot be imported."""


def _import_stubs() -> tuple[Any, Any, Any]:
    """Lazy import of grpc + the generated stubs."""
    try:
        from . import ratelimit_pb2 as pb
        from . import ratelimit_pb2_grpc as pb_grpc
        import grpc as _grpc

        return _grpc, pb, pb_grpc
    except Exception as exc:  # noqa: BLE001
        raise RateLimiterUnavailable(
            f"rate-limiter gRPC stubs are not importable: {exc}. "
            "Set RATE_LIMITER_ENGINE=python or install grpcio."
        ) from exc


def proto_to_dict(resp: Any) -> dict:
    """Translate ``CheckAllResponse`` proto into the legacy dict shape.

    The Python service returns the per-request dict pulled from the
    *winning* scope (the first to deny, or the key-scope on allow).
    We mirror that here so a swap to Rust is byte-equivalent at the
    ``check_all_async`` boundary.
    """
    # Pick the result that explains the outcome:
    #   - on allow: the api_key scope (matches the Python service —
    #     remaining/reset come from the per-key check).
    #   - on deny: the denied scope's result. ``denied_scope`` carries
    #     the label.
    results = list(resp.results)
    by_scope: dict[str, Any] = {r.scope: r for r in results}

    if not resp.allowed:
        denied = resp.denied_scope or "api_key"
        winner = by_scope.get(denied) or (results[0] if results else None)
        return {
            "allowed": False,
            "scope": denied,
            "remaining": int(winner.remaining) if winner else 0,
            "reset_at": int(winner.reset_at) if winner else 0,
            "limit": int(winner.limit) if winner else 0,
        }

    # Allow path — surface the key-scope counters in the response so
    # downstream consumers see consistent ``remaining`` values.
    winner = by_scope.get("api_key") or (results[0] if results else None)
    return {
        "allowed": True,
        "scope": None,
        "remaining": int(winner.remaining) if winner else 0,
        "reset_at": int(winner.reset_at) if winner else 0,
        "limit": int(winner.limit) if winner else 0,
    }


class RateLimiterClient:
    """Process-singleton gRPC client. One channel per process."""

    _instance_lock = threading.Lock()
    _instance: "RateLimiterClient | None" = None

    def __init__(self, address: str, timeout_seconds: float) -> None:
        self.address = address
        self.timeout_seconds = timeout_seconds
        self._channel: Any | None = None
        self._stub: Any | None = None
        self._build_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "RateLimiterClient":
        from app.core.config import settings

        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(
                    address=settings.rate_limiter_grpc_address,
                    timeout_seconds=settings.rate_limiter_grpc_timeout_seconds,
                )
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the cached singleton — used by tests."""
        with cls._instance_lock:
            if cls._instance is not None and cls._instance._channel is not None:
                try:
                    cls._instance._channel.close()
                except Exception:  # noqa: BLE001
                    pass
            cls._instance = None

    async def check_all(
        self,
        *,
        key_id: str,
        project_id: str,
        tenant_id: str,
        key_limit: int = 60,
        project_limit: int = 300,
        tenant_limit: int = 1000,
        window_seconds: int = 60,
    ) -> dict:
        """Send one CheckAll RPC; return the legacy-shape dict.

        Raises ``RateLimiterUnavailable`` only when grpcio / the stubs
        cannot be imported at all. Per-call failures (timeout,
        Unavailable, etc.) are propagated — the caller (service
        layer) catches and falls back to the Python implementation.
        """
        grpc, pb, _pb_grpc = _import_stubs()
        stub = self._ensure_stub(grpc, _pb_grpc)
        req = pb.CheckAllRequest(
            key_id=key_id,
            project_id=project_id,
            tenant_id=tenant_id,
            key_limit=int(key_limit),
            project_limit=int(project_limit),
            tenant_limit=int(tenant_limit),
            window_seconds=int(window_seconds),
        )
        resp = await stub.CheckAll(req, timeout=self.timeout_seconds)
        return proto_to_dict(resp)

    def _ensure_stub(self, grpc: Any, pb_grpc: Any) -> Any:
        with self._build_lock:
            if self._stub is not None:
                return self._stub
            self._channel = grpc.aio.insecure_channel(
                self.address,
                options=(
                    ("grpc.keepalive_time_ms", 30_000),
                    ("grpc.keepalive_timeout_ms", 5_000),
                    ("grpc.keepalive_permit_without_calls", 1),
                    ("grpc.http2.max_pings_without_data", 0),
                ),
            )
            self._stub = pb_grpc.RateLimitServiceStub(self._channel)
            return self._stub


__all__ = [
    "RateLimiterClient",
    "RateLimiterUnavailable",
    "proto_to_dict",
]
