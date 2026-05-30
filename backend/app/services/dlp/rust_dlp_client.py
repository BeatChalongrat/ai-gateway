"""gRPC client for the Rust ``dlp-sidecar`` — with the Python
encoding-bypass preprocessing wrapper in front of it.

The Rust sidecar runs the regex/heuristic detectors but does NOT
implement the encoding-bypass preprocessing (zero-width strip +
offset remap, homoglyph mapping, base64 decode-and-scan). So this
client mirrors ``DetectorRegistry.scan_text`` exactly: it runs the
Python ``normalize_for_scan`` first, sends the *cleaned* text to Rust
for detection, then maps the returned offsets back to the ORIGINAL
text — and decodes base64 segments, scanning each via Rust and mapping
findings to the encoded span. Result: the Rust path catches the same
evasion vectors the Python path does, with offsets in original coords.

Falls back to ``None`` on **any** failure (import error, RPC error,
timeout, preprocessing error) so the orchestrator's ``_run_dlp_scan``
routes straight to the Python registry (which does its own
preprocessing) — the fail-open safety net behind the engine switch.

Single-process channel singleton: the channel is built lazily on
first call and reused. ``reset()`` exists for tests that mint a
fresh stub per test.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import structlog

from app.core.config import settings
from app.domain.dlp import DetectionMatch, DetectionResult, SensitivityLevel
from app.services.dlp.preprocessor import (
    normalize_for_scan,
    map_offset_to_original,
)

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


def _flatten_content(content: Any) -> str:
    """Flatten a message ``content`` to text (vision list-of-blocks →
    text blocks only), matching ``DetectorRegistry.scan_messages``."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return "" if content is None else str(content)


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

    # ------------------------------------------------------------------
    # Low-level RPC + preprocessing
    # ------------------------------------------------------------------

    async def _rust_scan_raw(
        self,
        stub: Any,
        pb: Any,
        text: str,
        *,
        language: str,
        disabled_detectors: list[str],
        ner_enabled: bool,
    ) -> list[DetectionMatch]:
        """Scan ONE already-cleaned text via the Rust ScanText RPC.

        Returns matches with offsets in *that text's* coordinates.
        Raises on RPC error / timeout (caller maps to fail-open None).
        """
        req = pb.ScanRequest(
            text=text,
            language=language,
            disabled_detectors=list(disabled_detectors),
            ner_enabled=ner_enabled,
        )
        resp = await asyncio.wait_for(
            stub.ScanText(req), timeout=self.timeout_seconds
        )
        return [_proto_to_domain(m) for m in resp.matches]

    async def _scan_text_preprocessed(
        self,
        stub: Any,
        pb: Any,
        text: str,
        *,
        language: str,
        disabled_detectors: list[str],
        ner_enabled: bool,
    ) -> list[DetectionMatch]:
        """Mirror ``DetectorRegistry.scan_text``: normalize → Rust detect
        on the cleaned text → remap offsets to the original text → decode
        + scan base64 segments and map findings to the encoded span."""
        normalized = normalize_for_scan(text)

        raw = await self._rust_scan_raw(
            stub,
            pb,
            normalized.normalized,
            language=language,
            disabled_detectors=disabled_detectors,
            ner_enabled=ner_enabled,
        )

        # Map offsets from cleaned-text coords back to the original text.
        matches: list[DetectionMatch] = []
        if normalized.offset_map is not None:
            for m in raw:
                orig_start = map_offset_to_original(m.start, normalized.offset_map)
                orig_end = map_offset_to_original(m.end, normalized.offset_map)
                matches.append(
                    replace(
                        m,
                        start=orig_start,
                        end=orig_end,
                        matched_text=text[orig_start:orig_end],
                    )
                )
        else:
            matches.extend(raw)

        # Base64 decode-and-scan: scan each decoded segment via Rust, map
        # findings back to the encoded span in the original text.
        for segment in normalized.base64_segments:
            seg_matches = await self._rust_scan_raw(
                stub,
                pb,
                segment.decoded_text,
                language=language,
                disabled_detectors=disabled_detectors,
                ner_enabled=ner_enabled,
            )
            for sm in seg_matches:
                matches.append(
                    replace(
                        sm,
                        start=segment.original_start,
                        end=segment.original_end,
                        matched_text=segment.encoded_text,
                        metadata={
                            "detected_via": "base64_decode",
                            "decoded_match": sm.matched_text,
                        },
                    )
                )

        return matches

    # ------------------------------------------------------------------
    # Public API (preprocessing wrapper in front of Rust detection)
    # ------------------------------------------------------------------

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
        failure. Caller falls back to the Python registry on None.

        Mirrors ``DetectorRegistry.scan_messages``: per-message flatten +
        preprocess + Rust-detect, aggregated and sorted by (start, end).
        """
        try:
            grpc, pb, pb_grpc = _import_stubs()
        except RustDlpUnavailable as exc:
            logger.info("dlp_rust_unavailable", reason=str(exc)[:200])
            return None

        stub = self._ensure_stub(grpc, pb_grpc)
        disabled = list(disabled_detectors or [])

        try:
            all_matches: list[DetectionMatch] = []
            for m in messages:
                role = getattr(m, "role", "user")
                if not scan_system_messages and role == "system":
                    continue
                content = _flatten_content(getattr(m, "content", ""))
                all_matches.extend(
                    await self._scan_text_preprocessed(
                        stub,
                        pb,
                        content,
                        language="en",
                        disabled_detectors=disabled,
                        ner_enabled=ner_enabled,
                    )
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

        return DetectionResult(
            matches=sorted(all_matches, key=lambda m: (m.start, m.end)),
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
        """Returns ``DetectionResult`` on success, ``None`` on any failure.
        Mirrors ``DetectorRegistry.scan_text`` (preprocess + Rust detect +
        offset remap + base64 decode-and-scan)."""
        try:
            grpc, pb, pb_grpc = _import_stubs()
        except RustDlpUnavailable as exc:
            logger.info("dlp_rust_unavailable", reason=str(exc)[:200])
            return None

        stub = self._ensure_stub(grpc, pb_grpc)

        try:
            matches = await self._scan_text_preprocessed(
                stub,
                pb,
                text,
                language=language,
                disabled_detectors=list(disabled_detectors or []),
                ner_enabled=ner_enabled,
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
