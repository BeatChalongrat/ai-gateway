"""Unit tests for the Phase-3b Rust rate-limiter integration.

Covered:

* engine=rust dispatches through the gRPC stub
* engine=python (default) skips the gRPC path entirely
* gRPC errors / timeouts fall back to the Python pipeline
* proto → dict mapping preserves remaining/reset_at/limit/scope
* a denied response sets ``scope`` to the offending label
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.services.rate_limit_service import RateLimitService
from app.services.rate_limiter_client import (
    RateLimiterClient,
    RateLimiterUnavailable,
    proto_to_dict,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reset_client():
    RateLimiterClient.reset()
    yield
    RateLimiterClient.reset()


def _proto_response(allowed: bool, denied: str = "", scopes=None):
    """Build a CheckAllResponse-like object for mocks."""
    from app.services.rate_limiter_client import ratelimit_pb2 as pb

    scopes = scopes or [
        pb.ScopeResult(allowed=allowed, remaining=10, reset_at=999, limit=60, scope="api_key"),
        pb.ScopeResult(allowed=True, remaining=100, reset_at=999, limit=300, scope="project"),
        pb.ScopeResult(allowed=True, remaining=500, reset_at=999, limit=1000, scope="tenant"),
    ]
    return pb.CheckAllResponse(
        allowed=allowed,
        results=scopes,
        latency_ms=1.23,
        denied_scope=denied,
    )


# ---------------------------------------------------------------------------
# proto_to_dict
# ---------------------------------------------------------------------------

def test_proto_to_dict_allowed_maps_api_key_scope():
    resp = _proto_response(allowed=True)
    out = proto_to_dict(resp)
    assert out["allowed"] is True
    assert out["scope"] is None
    assert out["remaining"] == 10
    assert out["limit"] == 60
    assert out["reset_at"] == 999


def test_proto_to_dict_denied_picks_denied_scope():
    """A deny on tenant returns scope='tenant' + tenant's counters."""
    from app.services.rate_limiter_client import ratelimit_pb2 as pb

    scopes = [
        pb.ScopeResult(allowed=True, remaining=10, reset_at=999, limit=60, scope="api_key"),
        pb.ScopeResult(allowed=True, remaining=100, reset_at=999, limit=300, scope="project"),
        pb.ScopeResult(allowed=False, remaining=0, reset_at=2026, limit=1000, scope="tenant"),
    ]
    resp = _proto_response(allowed=False, denied="tenant", scopes=scopes)
    out = proto_to_dict(resp)
    assert out["allowed"] is False
    assert out["scope"] == "tenant"
    assert out["remaining"] == 0
    assert out["limit"] == 1000
    assert out["reset_at"] == 2026


# ---------------------------------------------------------------------------
# Engine switch
# ---------------------------------------------------------------------------

def test_engine_python_skips_grpc(reset_client, monkeypatch):
    """Default engine never instantiates the client."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limiter_engine", "python")

    sentinel = {"called": False}

    class _ShouldNotBeCalled:
        @classmethod
        def instance(cls):
            sentinel["called"] = True
            raise AssertionError("client.instance must NOT be called when engine=python")

    monkeypatch.setattr(
        "app.services.rate_limit_service.RateLimitService._check_all_python",
        lambda self, k, p, t: _async_ok(),
    )
    # Patch the import target — service does ``from app.services.rate_limiter_client import RateLimiterClient``.
    monkeypatch.setattr(
        "app.services.rate_limiter_client.RateLimiterClient", _ShouldNotBeCalled
    )

    async def _run():
        service = RateLimitService()
        result = await service.check_all_async("k", "p", "t")
        assert result["allowed"] is True

    asyncio.run(_run())
    assert sentinel["called"] is False


async def _async_ok():
    return {"allowed": True, "scope": None, "remaining": 60, "reset_at": 0, "limit": 60}


def test_engine_rust_calls_grpc(reset_client, monkeypatch):
    """engine=rust dispatches one CheckAll and returns the mapped dict."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limiter_engine", "rust")

    captured = {"calls": 0}

    class _FakeClient:
        async def check_all(self, **kwargs):
            captured["calls"] += 1
            captured["kwargs"] = kwargs
            return {
                "allowed": True,
                "scope": None,
                "remaining": 42,
                "reset_at": 123,
                "limit": 60,
            }

    fake = _FakeClient()
    monkeypatch.setattr(
        RateLimiterClient, "instance", classmethod(lambda cls: fake)
    )

    async def _run():
        service = RateLimitService()
        result = await service.check_all_async("kid", "pid", "tid")
        assert result["remaining"] == 42
        assert result["allowed"] is True

    asyncio.run(_run())
    assert captured["calls"] == 1
    assert captured["kwargs"]["key_id"] == "kid"
    assert captured["kwargs"]["project_id"] == "pid"
    assert captured["kwargs"]["tenant_id"] == "tid"


def test_engine_rust_falls_back_on_grpc_error(reset_client, monkeypatch):
    """RPC error → falls back to Python pipeline, no exception leaked."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limiter_engine", "rust")

    class _BoomClient:
        async def check_all(self, **_kw):
            raise RuntimeError("simulated rpc error")

    monkeypatch.setattr(
        RateLimiterClient, "instance", classmethod(lambda cls: _BoomClient())
    )

    py_called = {"calls": 0}

    async def _py_fallback(self, k, p, t):
        py_called["calls"] += 1
        return {
            "allowed": True,
            "scope": None,
            "remaining": 1,
            "reset_at": 0,
            "limit": 60,
        }

    monkeypatch.setattr(RateLimitService, "_check_all_python", _py_fallback)

    async def _run():
        service = RateLimitService()
        result = await service.check_all_async("k", "p", "t")
        assert result["allowed"] is True
        assert result["remaining"] == 1

    asyncio.run(_run())
    assert py_called["calls"] == 1


def test_engine_rust_falls_back_on_grpc_timeout(reset_client, monkeypatch):
    """A TimeoutError surfaced from the stub → fallback to Python."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limiter_engine", "rust")

    class _SlowClient:
        async def check_all(self, **_kw):
            raise asyncio.TimeoutError("simulated timeout")

    monkeypatch.setattr(
        RateLimiterClient, "instance", classmethod(lambda cls: _SlowClient())
    )

    fallback_called = {"n": 0}

    async def _py_fallback(self, k, p, t):
        fallback_called["n"] += 1
        return {"allowed": True, "scope": None, "remaining": 60, "reset_at": 0, "limit": 60}

    monkeypatch.setattr(RateLimitService, "_check_all_python", _py_fallback)

    async def _run():
        service = RateLimitService()
        result = await service.check_all_async("k", "p", "t")
        assert result["allowed"] is True

    asyncio.run(_run())
    assert fallback_called["n"] == 1


def test_engine_rust_falls_back_when_grpcio_missing(reset_client, monkeypatch):
    """RateLimiterUnavailable at instance() → Python fallback runs."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limiter_engine", "rust")

    def _raise(cls):  # noqa: ARG001
        raise RateLimiterUnavailable("stubs missing")

    monkeypatch.setattr(RateLimiterClient, "instance", classmethod(_raise))

    fallback_called = {"n": 0}

    async def _py_fallback(self, k, p, t):
        fallback_called["n"] += 1
        return {"allowed": True, "scope": None, "remaining": 60, "reset_at": 0, "limit": 60}

    monkeypatch.setattr(RateLimitService, "_check_all_python", _py_fallback)

    async def _run():
        service = RateLimitService()
        result = await service.check_all_async("k", "p", "t")
        assert result["allowed"] is True

    asyncio.run(_run())
    assert fallback_called["n"] == 1


def test_response_mapping_denied_preserves_remaining_and_scope():
    """Spec-pinning: a denied response surfaces (scope='api_key', remaining=0)."""
    from app.services.rate_limiter_client import ratelimit_pb2 as pb

    scopes = [
        pb.ScopeResult(allowed=False, remaining=0, reset_at=42, limit=60, scope="api_key"),
        pb.ScopeResult(allowed=True, remaining=100, reset_at=42, limit=300, scope="project"),
        pb.ScopeResult(allowed=True, remaining=500, reset_at=42, limit=1000, scope="tenant"),
    ]
    resp = _proto_response(allowed=False, denied="api_key", scopes=scopes)
    out = proto_to_dict(resp)
    assert out == {
        "allowed": False,
        "scope": "api_key",
        "remaining": 0,
        "reset_at": 42,
        "limit": 60,
    }
