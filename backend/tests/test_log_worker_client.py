"""Unit tests for the Phase-3 Go log-worker integration.

Covered:

* ``record_to_proto`` preserves every field in the round-trip
* The queue's ``engine=python`` (default) path is unchanged
* ``engine=go`` dispatches to the gRPC client and skips the legacy
  queue + sync persist
* When the gRPC client fails to import (LogWorkerUnavailable), the
  queue falls through to the legacy path
* When the gRPC call returns False (sidecar rejected), the queue
  treats the dispatch as scheduled and does NOT also call the
  legacy persist (fire-and-forget semantics)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.domain.observability import RequestRecord, RequestStatus
from app.services import request_log_queue
from app.services.log_worker_client import (
    LogWorkerUnavailable,
    record_to_proto,
)
from app.services.log_worker_client import client as client_module


def _record() -> RequestRecord:
    return RequestRecord(
        request_id="req_log_worker_test",
        tenant_id="tenant-1",
        project_id="proj-1",
        provider="anthropic",
        model="claude-sonnet-4",
        status=RequestStatus.SUCCESS,
        policy_action="allow",
        latency_ms=42,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        estimated_cost_usd=0.0123,
        fallback_used=True,
        fallback_attempts='[{"provider":"openai","model":"gpt-4o"}]',
        original_provider="openai",
        original_model="gpt-4o",
        dlp_categories=["EMAIL", "PHONE"],
        router_used="semantic",
        policy_profile_id="default",
        input_masked="hello [EMAIL_0]",
        output_final="bye",
        input_raw_encrypted="b64ciphertext",
    )


# ---------------------------------------------------------------------------
# record_to_proto: round-trip every field
# ---------------------------------------------------------------------------

def test_record_to_proto_maps_all_scalar_fields():
    proto = record_to_proto(_record())
    assert proto.request_id == "req_log_worker_test"
    assert proto.tenant_id == "tenant-1"
    assert proto.provider == "anthropic"
    assert proto.model == "claude-sonnet-4"
    assert proto.status == "success"
    assert proto.policy_action == "allow"
    assert proto.latency_ms == 42.0
    assert proto.prompt_tokens == 10
    assert proto.completion_tokens == 20
    assert proto.total_tokens == 30
    assert abs(proto.estimated_cost_usd - 0.0123) < 1e-9
    assert proto.fallback_used is True
    assert proto.original_provider == "openai"
    assert proto.original_model == "gpt-4o"
    assert proto.policy_profile_id == "default"
    assert proto.input_masked == "hello [EMAIL_0]"
    assert proto.output_final == "bye"
    assert proto.input_raw_encrypted == "b64ciphertext"


def test_record_to_proto_joins_dlp_categories():
    """Categories are comma-joined to match the SQLite-friendly storage."""
    proto = record_to_proto(_record())
    assert proto.dlp_categories == "EMAIL,PHONE"


def test_record_to_proto_handles_empty_optionals():
    """None / missing optional fields become empty strings on the wire.

    The Go inserter maps empty → SQL NULL via ``emptyToNil``, so this
    test pins the contract that drives null-correctness.
    """
    r = RequestRecord(
        request_id="empty-r",
        tenant_id="t",
        project_id="p",
    )
    proto = record_to_proto(r)
    assert proto.error_code == ""
    assert proto.router_used == ""
    assert proto.fallback_attempts_json == ""
    assert proto.original_provider == ""
    assert proto.dlp_categories == ""
    assert proto.input_raw == ""


# ---------------------------------------------------------------------------
# Queue engine switch
# ---------------------------------------------------------------------------

@pytest.fixture
def reset_client(monkeypatch):
    """Drop the cached LogWorkerClient between tests."""
    from app.services.log_worker_client import LogWorkerClient

    LogWorkerClient.reset()
    yield
    LogWorkerClient.reset()


def test_engine_python_uses_legacy_path(reset_client, monkeypatch):
    """Default engine keeps using the asyncio queue / sync fallback."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "log_worker_engine", "python")

    fake_session = MagicMock()
    fake_session.add = MagicMock()
    fake_session.flush = MagicMock()

    # No async task should ever be created when engine=python.
    with patch.object(
        request_log_queue._singleton,
        "_dispatch_to_grpc",
    ) as grpc_dispatch:
        request_log_queue.enqueue(_record(), fallback_session=fake_session)
        grpc_dispatch.assert_not_called()


def test_engine_go_dispatches_to_grpc(reset_client, monkeypatch):
    """engine=go schedules a gRPC task and skips the legacy queue."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "log_worker_engine", "go")

    fake_session = MagicMock()

    # Drive enqueue from inside a running loop so create_task is valid.
    async def _run():
        with patch.object(
            request_log_queue._singleton,
            "_dispatch_to_grpc",
            return_value=True,
        ) as grpc_dispatch:
            request_log_queue.enqueue(_record(), fallback_session=fake_session)
            grpc_dispatch.assert_called_once()
            # Legacy sync_fallback must NOT run when grpc dispatch
            # succeeded — the queue must be fire-and-forget.
            fake_session.add.assert_not_called()

    asyncio.run(_run())


def test_engine_go_falls_back_when_grpc_dispatch_fails(
    reset_client, monkeypatch
):
    """If _dispatch_to_grpc returns False, the legacy path still runs."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "log_worker_engine", "go")

    captured: list[RequestRecord] = []

    def _capture(self, record, session):  # noqa: ARG001
        captured.append(record)

    monkeypatch.setattr(
        request_log_queue._RequestLogQueue, "_sync_fallback", _capture
    )

    async def _run():
        with patch.object(
            request_log_queue._singleton,
            "_dispatch_to_grpc",
            return_value=False,  # dispatch refused
        ):
            request_log_queue.enqueue(_record())

    asyncio.run(_run())

    assert len(captured) == 1
    assert captured[0].request_id == "req_log_worker_test"


def test_grpc_dispatch_swallows_unavailable_import_error(
    reset_client, monkeypatch
):
    """LogWorkerUnavailable at instance() falls through cleanly."""
    from app.services.log_worker_client import LogWorkerClient

    def _raise(*_a, **_k):
        raise LogWorkerUnavailable("stubs missing")

    monkeypatch.setattr(LogWorkerClient, "instance", classmethod(_raise))

    async def _run():
        # Calling _dispatch_to_grpc directly is enough — the public
        # enqueue contract is tested above.
        ok = request_log_queue._singleton._dispatch_to_grpc(_record())
        # Must return False so the caller falls through, never True.
        assert ok is False

    asyncio.run(_run())


def test_grpc_client_enqueue_returns_false_on_rpc_error(
    reset_client, monkeypatch
):
    """An RpcError during Enqueue returns False rather than raising."""
    from app.services.log_worker_client import LogWorkerClient
    from app.services.log_worker_client.client import _import_stubs

    grpc_mod, pb_mod, pb_grpc_mod = _import_stubs()

    class _FailingStub:
        async def Enqueue(self, _req, timeout=None):  # noqa: ARG002
            raise grpc_mod.RpcError("simulated failure")

    client = LogWorkerClient(
        address="unused:0", timeout_seconds=0.1
    )

    # Bypass the channel-build path — install a fake stub directly.
    client._stub = _FailingStub()
    client._channel = MagicMock()

    async def _run():
        ok = await client.enqueue(_record())
        assert ok is False

    asyncio.run(_run())
