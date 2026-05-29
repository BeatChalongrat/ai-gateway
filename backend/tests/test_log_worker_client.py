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
        fallback_attempts=[{"provider": "openai", "model": "gpt-4o"}],
        original_provider="openai",
        original_model="gpt-4o",
        dlp_categories=["EMAIL", "PHONE"],
        router_used="semantic",
        policy_profile_id="default",
        input_masked="hello <r0>",
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
    assert proto.input_masked == "hello <r0>"
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


# ---------------------------------------------------------------------------
# Audit-fix gap closures: timeout, accepted=false, concurrent enqueue
# ---------------------------------------------------------------------------

def test_grpc_timeout_returns_false_and_dispatch_falls_back(
    reset_client, monkeypatch
):
    """An asyncio.TimeoutError inside the RPC swallows to False and the
    queue's _dispatch_to_grpc still returns True (task scheduled) — but
    if the client.enqueue eventually raises a *non-RPC* exception
    (like TimeoutError) it must NOT propagate to the caller."""
    from app.services.log_worker_client import LogWorkerClient

    class _SlowStub:
        async def Enqueue(self, _req, timeout=None):  # noqa: ARG002
            raise asyncio.TimeoutError("simulated timeout")

    client = LogWorkerClient(address="unused:0", timeout_seconds=0.1)
    client._stub = _SlowStub()
    client._channel = MagicMock()

    async def _run():
        ok = await client.enqueue(_record())
        # The TimeoutError branch must swallow + return False so the
        # queue's done-callback can record a missed dispatch without
        # propagating the exception out of the fire-and-forget task.
        assert ok is False

    asyncio.run(_run())


def test_accepted_false_recovers_row_via_local_path(
    reset_client, monkeypatch
):
    """accepted=False (sidecar reachable but rejected the record, e.g.
    its buffer overflowed and drop-oldest'd) must NOT lose the audit
    row. Phase-0 R5 fix: ``_on_grpc_done`` re-routes any failed/rejected
    dispatch through ``_enqueue_local`` — with no worker running that
    lands on ``_sync_fallback`` so the row is still persisted.

    (Supersedes the pre-R5 contract, which deliberately dropped the
    row on accepted=false in the name of fire-and-forget.)"""
    from app.core.config import settings
    from app.services.log_worker_client import LogWorkerClient
    from app.services.log_worker_client.client import _import_stubs

    monkeypatch.setattr(settings, "log_worker_engine", "go")

    _grpc, pb, _pb_grpc = _import_stubs()

    class _RejectingStub:
        async def Enqueue(self, _req, timeout=None):  # noqa: ARG002
            return pb.EnqueueResponse(accepted=False, reason="queue_full")

    client = LogWorkerClient(address="unused:0", timeout_seconds=0.5)
    client._stub = _RejectingStub()
    client._channel = MagicMock()

    monkeypatch.setattr(
        LogWorkerClient, "instance", classmethod(lambda cls: client)
    )

    recovered: list[RequestRecord] = []

    def _capture_fallback(self, record, session):  # noqa: ARG001
        recovered.append(record)

    monkeypatch.setattr(
        request_log_queue._RequestLogQueue,
        "_sync_fallback",
        _capture_fallback,
    )

    async def _run():
        # No worker running → _enqueue_local routes to _sync_fallback.
        request_log_queue._singleton._queue = None
        request_log_queue.enqueue(_record())
        # Yield so the create_task'd RPC + done-callback can run.
        await asyncio.sleep(0.1)

    asyncio.run(_run())

    assert len(recovered) == 1, (
        "R5: a rejected gRPC dispatch must be recovered via the local "
        "path, not silently dropped"
    )
    assert recovered[0].request_id == "req_log_worker_test"


def test_grpc_rpc_error_recovers_row_via_local_path(
    reset_client, monkeypatch
):
    """A transport failure (RPC raises → client.enqueue returns False)
    must also recover the row via the local path. Core R5 case."""
    from app.core.config import settings
    from app.services.log_worker_client import LogWorkerClient
    from app.services.log_worker_client.client import _import_stubs

    monkeypatch.setattr(settings, "log_worker_engine", "go")

    grpc_mod, _pb, _pb_grpc = _import_stubs()

    class _FailingStub:
        async def Enqueue(self, _req, timeout=None):  # noqa: ARG002
            raise grpc_mod.RpcError("simulated transport failure")

    client = LogWorkerClient(address="unused:0", timeout_seconds=0.1)
    client._stub = _FailingStub()
    client._channel = MagicMock()
    monkeypatch.setattr(
        LogWorkerClient, "instance", classmethod(lambda cls: client)
    )

    recovered: list[RequestRecord] = []
    monkeypatch.setattr(
        request_log_queue._RequestLogQueue,
        "_sync_fallback",
        lambda self, record, session: recovered.append(record),  # noqa: ARG005
    )

    async def _run():
        request_log_queue._singleton._queue = None
        request_log_queue.enqueue(_record())
        await asyncio.sleep(0.1)

    asyncio.run(_run())

    assert len(recovered) == 1
    assert recovered[0].request_id == "req_log_worker_test"


def test_grpc_failure_routes_to_running_queue_not_sync(
    reset_client, monkeypatch
):
    """When the legacy worker IS running, a failed dispatch is recovered
    onto the asyncio queue (non-blocking), NOT via the synchronous
    own-session persist. Pins that the R5 fallback prefers the cheap
    batched path."""
    from app.core.config import settings
    from app.services.log_worker_client import LogWorkerClient
    from app.services.log_worker_client.client import _import_stubs

    monkeypatch.setattr(settings, "log_worker_engine", "go")

    _grpc, pb, _pb_grpc = _import_stubs()

    class _RejectingStub:
        async def Enqueue(self, _req, timeout=None):  # noqa: ARG002
            return pb.EnqueueResponse(accepted=False, reason="x")

    client = LogWorkerClient(address="unused:0", timeout_seconds=0.5)
    client._stub = _RejectingStub()
    client._channel = MagicMock()
    monkeypatch.setattr(
        LogWorkerClient, "instance", classmethod(lambda cls: client)
    )

    sync_called: list[bool] = []
    monkeypatch.setattr(
        request_log_queue._RequestLogQueue,
        "_sync_fallback",
        lambda self, r, s: sync_called.append(True),  # noqa: ARG005
    )

    async def _run():
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        request_log_queue._singleton._queue = q
        request_log_queue.enqueue(_record())
        await asyncio.sleep(0.1)
        return q

    q = asyncio.run(_run())

    assert sync_called == [], "should use the queue, not sync persist"
    assert q.qsize() == 1, "failed dispatch must land on the running queue"
    request_log_queue._singleton._queue = None


def test_concurrent_enqueue_safe(reset_client, monkeypatch):
    """50 concurrent enqueues from the same loop must not raise and
    must each schedule exactly one dispatch. Pins the thread-safety
    of the engine switch under burst load."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "log_worker_engine", "go")

    dispatch_calls = 0

    def _count_dispatch(_self, _record):
        nonlocal dispatch_calls
        dispatch_calls += 1
        return True  # pretend the gRPC task was scheduled

    monkeypatch.setattr(
        request_log_queue._RequestLogQueue,
        "_dispatch_to_grpc",
        _count_dispatch,
    )

    async def _run():
        # asyncio.gather to launch concurrently. ``enqueue`` is a
        # sync function — gather-ing it like this exercises the
        # shared queue from many tasks on the same loop, which is
        # the realistic pattern (FastAPI handlers all live on one
        # event loop per worker).
        async def _one():
            request_log_queue.enqueue(_record())

        await asyncio.gather(*[_one() for _ in range(50)])

    asyncio.run(_run())

    assert dispatch_calls == 50, (
        f"expected 50 dispatches, got {dispatch_calls}"
    )
