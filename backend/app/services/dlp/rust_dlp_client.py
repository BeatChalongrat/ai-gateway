"""gRPC client for the Rust ``dlp-sidecar``.

Falls back to ``None`` on **any** failure (import error, RPC error,
timeout) so the orchestrator's ``_run_dlp_scan`` can route to the
Python registry without try/except wrappers at every call site. The
fallback contract is the safety net behind the engine switch — a
crashed sidecar must NEVER block traffic.

Single-process channel singleton: the channel is built lazily on
first call and reused. ``reset()`` exists for tests that mint a
fresh stub per test.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

import structlog

from app.core.config import settings
from app.domain.dlp import DetectionMatch, DetectionResult, SensitivityLevel

if TYPE_CHECKING:
    from app.schemas.common import Message

logger = structlog.get_logger(__name__)


class RustDlpUnavailable(RuntimeError):
    """Raised once when grpcio / generated stubs cannot be imported."""


def _import_stubs() -> tuple[Any, Any, Any]:
    try:
        import grpc as _grpc
        from . import dlp_pb2 as pb
        from . import dlp_pb2_grpc as pb_grpc

        return _grpc, pb, pb_grpc
    except Exception as exc:  # noqa: BLE001
        raise RustDlpUnavailable(
            f"rust dlp-sidecar stubs are not importable: {exc}. "
            "Set DLP_ENGINE=python or install grpcio."
        ) from exc


def _proto_to_domain(m: Any) -> DetectionMatch:
    """Translate a single proto ``DetectionMatch`` into the Python
    domain object the orchestrator already understands."""
    return DetectionMatch(
        detector_name=m.detector_name,
        entity_type=m.entity_type,
        category=m.category,
        start=int(m.start),
        end=int(m.end),
        matched_text=m.matched_text,
        severity=SensitivityLevel(int(m.severity)),
        confidence=float(m.confidence),
        metadata=None,
        priority=int(m.priority),
    )


class RustDlpClient:
    """Process-singleton gRPC client. One channel per process."""

    _instance_lock = threading.Lock()
    _instance: "RustDlpClient | None" = None

    def __init__(self, address: str, timeout_seconds: float) -> None:
        self.address = address
        self.timeout_seconds = timeout_seconds
        self._channel: Any | None = None
        self._stub: Any | None = None
        self._build_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "RustDlpClient":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(
                    address=settings.dlp_grpc_address,
                    timeout_seconds=settings.dlp_grpc_timeout_seconds,
                )
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None and cls._instance._channel is not None:
                try:
                    cls._instance._channel.close()
                except Exception:  # noqa: BLE001
                    pass
            cls._instance = None

    async def scan_messages(
        self,
        messages: list["Message"],
        *,
        direction: str = "input",
        scan_system_messages: bool = False,
        disabled_detectors: list[str] | None = None,
        ner_enabled: bool = True,
    ) -> DetectionResult | None:
        """Returns ``DetectionResult`` on success, ``None`` on any
        failure. Caller falls back to Python registry on None."""
        try:
            grpc, pb, pb_grpc = _import_stubs()
        except RustDlpUnavailable as exc:
            logger.info("dlp_rust_unavailable", reason=str(exc)[:200])
            return None

        stub = self._ensure_stub(grpc, pb_grpc)

        proto_messages = []
        for m in messages:
            role = getattr(m, "role", "user")
            content = getattr(m, "content", "")
            if not isinstance(content, str):
                # Vision-format messages with list-of-blocks — flatten
                # text blocks only, matching Python registry behaviour.
                if isinstance(content, list):
                    content = "\n".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    content = str(content)
            proto_messages.append(pb.Message(role=role, content=content))

        req = pb.MessagesRequest(
            messages=proto_messages,
            direction=direction,
            scan_system_messages=scan_system_messages,
            disabled_detectors=list(disabled_detectors or []),
            ner_enabled=ner_enabled,
        )

        try:
            resp = await asyncio.wait_for(
                stub.ScanMessages(req),
                timeout=self.timeout_seconds,
            )
        except grpc.RpcError as exc:  # type: ignore[union-attr]
            logger.warning(
                "dlp_rust_rpc_failed",
                code=getattr(exc, "code", lambda: None)(),
                details=str(exc)[:200],
            )
            return None
        except asyncio.TimeoutError:
            logger.warning("dlp_rust_rpc_timeout")
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("dlp_rust_rpc_unexpected", error=str(exc)[:200])
            return None

        matches = [_proto_to_domain(m) for m in resp.matches]
        return DetectionResult(
            matches=sorted(matches, key=lambda m: (m.start, m.end)),
            scanned_direction=direction,
            scan_target="messages",
        )

    async def scan_text(
        self,
        text: str,
        *,
        language: str = "en",
        disabled_detectors: list[str] | None = None,
        ner_enabled: bool = True,
    ) -> DetectionResult | None:
        try:
            grpc, pb, pb_grpc = _import_stubs()
        except RustDlpUnavailable as exc:
            logger.info("dlp_rust_unavailable", reason=str(exc)[:200])
            return None

        stub = self._ensure_stub(grpc, pb_grpc)
        req = pb.ScanRequest(
            text=text,
            language=language,
            disabled_detectors=list(disabled_detectors or []),
            ner_enabled=ner_enabled,
        )
        try:
            resp = await asyncio.wait_for(
                stub.ScanText(req),
                timeout=self.timeout_seconds,
            )
        except grpc.RpcError as exc:  # type: ignore[union-attr]
            logger.warning(
                "dlp_rust_rpc_failed",
                code=getattr(exc, "code", lambda: None)(),
                details=str(exc)[:200],
            )
            return None
        except asyncio.TimeoutError:
            logger.warning("dlp_rust_rpc_timeout")
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("dlp_rust_rpc_unexpected", error=str(exc)[:200])
            return None

        matches = [_proto_to_domain(m) for m in resp.matches]
        return DetectionResult(
            matches=sorted(matches, key=lambda m: (m.start, m.end)),
            scanned_direction=language,
            scan_target="text",
        )

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
            self._stub = pb_grpc.DlpServiceStub(self._channel)
            return self._stub


__all__ = [
    "RustDlpClient",
    "RustDlpUnavailable",
]
