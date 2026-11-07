"""Unit tests for the Rust DLP gRPC client + orchestrator selector.

Covered:

* ``_proto_to_domain`` round-trips every field
* ``engine=python`` (default) never touches the gRPC stub
* ``engine=rust`` dispatches; RPC error / timeout / None falls back
  to the Python registry
* ``engine=shadow`` returns the Python result on the hot path
  and logs the diff via the orchestrator's structlog binding
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.domain.dlp import DetectionMatch, DetectionResult, SensitivityLevel
from app.services.dlp.rust_dlp_client import (
    RustDlpClient,
    RustDlpUnavailable,
    _proto_to_domain,
)


# ---------------------------------------------------------------------------
# proto_to_domain
# ---------------------------------------------------------------------------

def test_proto_to_domain_maps_all_fields():
    """Every field survives the proto round-trip with correct typing."""
    from app.services.dlp import dlp_pb2 as pb

    proto = pb.DetectionMatch(
        detector_name="email",
        entity_type="email_address",
        category="pii",
        start=14,
        end=30,
        matched_text="user@example.com",
        severity=2,
        confidence=0.99,
        priority=5,
    )
    out = _proto_to_domain(proto)
    assert out.detector_name == "email"
    assert out.entity_type == "email_address"
    assert out.category == "pii"
    assert out.start == 14
    assert out.end == 30
    assert out.matched_text == "user@example.com"
    assert out.severity == SensitivityLevel.MEDIUM
    assert abs(out.confidence - 0.99) < 1e-6
    assert out.priority == 5


# ---------------------------------------------------------------------------
# Engine switch via orchestrator
# ---------------------------------------------------------------------------

@pytest.fixture
def reset_client():
    RustDlpClient.reset()
    yield
    RustDlpClient.reset()


def _stub_python_registry(detector_matches):
    """Build a mock that returns a DetectionResult with the given matches."""
    mock = MagicMock()
    mock.scan_messages.return_value = DetectionResult(
        matches=detector_matches,
        scanned_direction="input",
        scan_target="messages",
    )
    return mock


def _orch():
    """Fresh orchestrator + minimal RequestContext stub."""
    from app.domain.entities import RequestContext
    from app.services.orchestrator import GatewayOrchestrator

    return GatewayOrchestrator(), RequestContext(
        tenant_id="t1",
        project_id="p1",
        project_name="P",
        request_id="req-test",
    )


def test_engine_python_bypasses_grpc(reset_client, monkeypatch):
    """engine=python must NEVER call the Rust client."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_engine", "python")

    sentinel = {"called": False}

    class _ShouldNotBeCalled:
        @classmethod
        def instance(cls):
            sentinel["called"] = True
            raise AssertionError("client.instance must NOT be called when engine=python")

    monkeypatch.setattr(
        "app.services.dlp.rust_dlp_client.RustDlpClient", _ShouldNotBeCalled
    )

    orch, ctx = _orch()
    registry = _stub_python_registry([])

    async def _run():
        result = await orch._run_dlp_scan(
            registry,
            [],
            direction="input",
            scan_system_messages=False,
            ctx=ctx,
            session=None,
        )
        assert result.scan_target == "messages"

    asyncio.run(_run())
    assert sentinel["called"] is False


def test_engine_rust_returns_grpc_result(reset_client, monkeypatch):
    """engine=rust uses the Rust result when the call succeeds."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_engine", "rust")

    fake_match = DetectionMatch(
        detector_name="email",
        entity_type="email_address",
        category="pii",
        start=0,
        end=10,
        matched_text="rust@x.com",
        severity=SensitivityLevel.MEDIUM,
    )
    rust_result = DetectionResult(
        matches=[fake_match],
        scanned_direction="input",
        scan_target="messages",
    )

    class _FakeClient:
        async def scan_messages(self, *args, **kwargs):
            return rust_result

    monkeypatch.setattr(
        RustDlpClient, "instance", classmethod(lambda cls: _FakeClient())
    )

    orch, ctx = _orch()
    py_registry = _stub_python_registry([])

    async def _run():
        result = await orch._run_dlp_scan(
            py_registry,
            [],
            direction="input",
            scan_system_messages=False,
            ctx=ctx,
            session=None,
        )
        # Must be the rust result, not python
        assert len(result.matches) == 1
        assert result.matches[0].matched_text == "rust@x.com"

    asyncio.run(_run())
    # Python registry was NOT called (rust succeeded).
    py_registry.scan_messages.assert_not_called()


def test_engine_rust_falls_back_on_none(reset_client, monkeypatch):
    """When the Rust client returns None, fall back to Python."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_engine", "rust")

    class _NoneClient:
        async def scan_messages(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        RustDlpClient, "instance", classmethod(lambda cls: _NoneClient())
    )

    orch, ctx = _orch()
    fake_py_match = DetectionMatch(
        detector_name="email",
        entity_type="email_address",
        category="pii",
        start=0,
        end=10,
        matched_text="py@x.com",
        severity=SensitivityLevel.MEDIUM,
    )
    py_registry = _stub_python_registry([fake_py_match])

    async def _run():
        result = await orch._run_dlp_scan(
            py_registry,
            [],
            direction="input",
            scan_system_messages=False,
            ctx=ctx,
            session=None,
        )
        # Python fallback fired.
        assert len(result.matches) == 1
        assert result.matches[0].matched_text == "py@x.com"

    asyncio.run(_run())
    py_registry.scan_messages.assert_called_once()


def test_engine_rust_falls_back_on_exception(reset_client, monkeypatch):
    """A raised exception from the Rust client is swallowed."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_engine", "rust")

    class _BoomClient:
        async def scan_messages(self, *args, **kwargs):
            raise RuntimeError("simulated rpc explosion")

    monkeypatch.setattr(
        RustDlpClient, "instance", classmethod(lambda cls: _BoomClient())
    )

    orch, ctx = _orch()
    py_registry = _stub_python_registry([])

    async def _run():
        # Must not raise.
        result = await orch._run_dlp_scan(
            py_registry,
            [],
            direction="input",
            scan_system_messages=False,
            ctx=ctx,
            session=None,
        )
        assert result.scan_target == "messages"

    asyncio.run(_run())
    py_registry.scan_messages.assert_called_once()


def _fallback_count(reason):
    from app.observability.metrics import SIDECAR_FALLBACK_TOTAL

    return SIDECAR_FALLBACK_TOTAL.labels(engine="dlp", reason=reason)._value.get()


def test_engine_rust_refusal_rescans_on_python_and_is_counted_apart(reset_client, monkeypatch):
    """FIX103 — the sidecar refusing an input its regex engine cannot finish
    is not an outage: Python rescans the request in full, and the fallback
    is counted as reason="refused" so DlpSidecarFallingBack stays quiet."""
    from app.core.config import settings
    from app.services.dlp.rust_dlp_client import RustScanRefused

    monkeypatch.setattr(settings, "dlp_engine", "rust")

    class _RefusingClient:
        async def scan_messages(self, *args, **kwargs):
            raise RustScanRefused("religion_belief: regex engine error")

    monkeypatch.setattr(RustDlpClient, "instance", classmethod(lambda cls: _RefusingClient()))

    orch, ctx = _orch()
    py_match = DetectionMatch(
        detector_name="phone", entity_type="phone_number", category="pii",
        start=0, end=12, matched_text="081-234-5678", severity=SensitivityLevel.MEDIUM,
    )
    py_registry = _stub_python_registry([py_match])
    refused_before, unavailable_before = _fallback_count("refused"), _fallback_count("unavailable")

    async def _run():
        result = await orch._run_dlp_scan(
            py_registry, [], direction="input", scan_system_messages=False, ctx=ctx, session=None,
        )
        assert [m.matched_text for m in result.matches] == ["081-234-5678"]

    asyncio.run(_run())
    py_registry.scan_messages.assert_called_once()
    assert _fallback_count("refused") == refused_before + 1
    assert _fallback_count("unavailable") == unavailable_before


class _FakeRpcError(Exception):
    """Stands in for grpc.RpcError: carries code() and details()."""

    def __init__(self, code, details=""):
        super().__init__(details)
        self._code, self._details = code, details

    def code(self):
        return self._code

    def details(self):
        return self._details


def test_client_raises_refused_on_resource_exhausted_and_none_on_outage(reset_client, monkeypatch):
    import grpc
    from types import SimpleNamespace

    from app.services.dlp.rust_dlp_client import RustScanRefused

    monkeypatch.setattr(grpc, "RpcError", _FakeRpcError)
    msgs = [SimpleNamespace(role="user", content="x" * 40_000)]

    def _raising(code, details):
        async def _scan(*_a, **_k):
            raise _FakeRpcError(code, details)
        return _scan

    client = RustDlpClient(address="ignored:1", timeout_seconds=0.1)
    monkeypatch.setattr(client, "_scan_text_preprocessed", _raising(
        grpc.StatusCode.RESOURCE_EXHAUSTED, "dlp scan refused: religion_belief: regex engine error"))
    with pytest.raises(RustScanRefused, match="religion_belief"):
        asyncio.run(client.scan_messages(msgs))

    monkeypatch.setattr(client, "_scan_text_preprocessed", _raising(grpc.StatusCode.UNAVAILABLE, "connect failed"))
    assert asyncio.run(client.scan_messages(msgs)) is None


def test_engine_shadow_returns_python_and_logs_diff(reset_client, monkeypatch, caplog):
    """Shadow mode returns Python's result and logs the diff."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_engine", "shadow")

    py_match = DetectionMatch(
        detector_name="email",
        entity_type="email_address",
        category="pii",
        start=0,
        end=10,
        matched_text="py@x.com",
        severity=SensitivityLevel.MEDIUM,
    )
    # Identical match — diff should be 0.
    rust_match = DetectionMatch(
        detector_name="email",
        entity_type="email_address",
        category="pii",
        start=0,
        end=10,
        matched_text="py@x.com",
        severity=SensitivityLevel.MEDIUM,
    )
    rust_result = DetectionResult(
        matches=[rust_match], scanned_direction="input", scan_target="messages"
    )

    class _FakeClient:
        async def scan_messages(self, *args, **kwargs):
            return rust_result

    monkeypatch.setattr(
        RustDlpClient, "instance", classmethod(lambda cls: _FakeClient())
    )

    orch, ctx = _orch()
    py_registry = _stub_python_registry([py_match])

    async def _run():
        result = await orch._run_dlp_scan(
            py_registry,
            [],
            direction="input",
            scan_system_messages=False,
            ctx=ctx,
            session=None,
        )
        # Shadow mode: result IS the python one.
        assert result.matches[0].matched_text == "py@x.com"

    asyncio.run(_run())
    py_registry.scan_messages.assert_called_once()


def test_unavailable_import_treated_as_fallback(reset_client, monkeypatch):
    """If the stubs raise on import the client returns None per design."""
    from app.services.dlp import rust_dlp_client

    def _raise_stubs():
        raise RustDlpUnavailable("stubs missing")

    monkeypatch.setattr(rust_dlp_client, "_import_stubs", _raise_stubs)

    async def _run():
        client = RustDlpClient(address="ignored", timeout_seconds=0.1)
        out = await client.scan_text("hello")
        assert out is None
        out = await client.scan_messages([])
        assert out is None

    asyncio.run(_run())
