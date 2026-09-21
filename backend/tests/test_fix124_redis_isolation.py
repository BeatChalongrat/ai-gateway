"""FIX124 — a test cannot reach a real Redis, by construction.

The lane's settings resolve to the running stack's Redis. Until now that is
where tests wrote: health markers the live gateway then honoured, strike
counters, a 15-minute marker on a real pair. These tests pin the isolation in
``tests/_redis_isolation.py``: a client pointed at the live server fails at
once and opens no socket; every client the app builds is an in-process fake.

The refusal is proved with PING — on a tree without the isolation that test
fails by succeeding, and a PING writes nothing.
"""

from __future__ import annotations

import socket

import pytest
import redis
import redis.asyncio as redis_asyncio
from fakeredis._server import FakeBaseConnectionMixin

from app.core.config import settings
from tests import _redis_isolation
from tests._redis_isolation import RealRedisInTests


@pytest.fixture
def no_sockets(monkeypatch):
    """Any attempt to open a TCP connection is recorded and refused."""
    opened: list = []

    def refuse(*args, **kwargs):
        opened.append(args)
        raise AssertionError(f"a socket was opened: {args}")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    return opened


def _is_fake(client) -> bool:
    return issubclass(client.connection_pool.connection_class, FakeBaseConnectionMixin)


def test_the_isolation_is_on_in_this_lane():
    assert _redis_isolation.install() is True
    assert not _redis_isolation.allowed()


def test_a_client_pointed_at_the_live_redis_fails_at_once_and_opens_no_socket(no_sockets):
    live = redis.Redis(
        host=settings.redis_host, port=settings.redis_port,
        password=settings.redis_password or None, socket_connect_timeout=1.0,
    )
    assert not _is_fake(live)
    with pytest.raises(RealRedisInTests) as ei:
        live.ping()
    assert f"{settings.redis_host}:{settings.redis_port}" in str(ei.value)
    assert no_sockets == []


@pytest.mark.asyncio
async def test_an_async_client_pointed_at_the_live_redis_fails_the_same_way(no_sockets):
    live = redis_asyncio.Redis(
        host=settings.redis_host, port=settings.redis_port,
        password=settings.redis_password or None, socket_connect_timeout=1.0,
    )
    with pytest.raises(RealRedisInTests):
        await live.ping()
    assert no_sockets == []


def test_a_hand_built_pool_is_refused_too(no_sockets):
    pool = redis.ConnectionPool(host=settings.redis_host, port=settings.redis_port)
    with pytest.raises(RealRedisInTests):
        redis.Redis(connection_pool=pool).ping()
    assert no_sockets == []


def test_every_client_the_app_builds_is_the_in_process_fake(no_sockets):
    from app.services._redis_pool import get_async_redis

    # the five sync call sites all go through ``from_url`` with the settings' URL
    for client in (redis.from_url(settings.redis_url), redis.Redis.from_url(settings.redis_url)):
        assert _is_fake(client)
    assert _is_fake(get_async_redis())
    assert no_sockets == []


@pytest.mark.asyncio
async def test_the_fake_is_one_server_for_sync_and_async_and_really_stores(no_sockets):
    from app.services._redis_pool import get_async_redis

    sync_client = redis.from_url(settings.redis_url, decode_responses=True)
    async_client = get_async_redis()
    assert _is_fake(sync_client) and _is_fake(async_client)  # checked BEFORE the write
    await async_client.set("fix124:isolation", "1", ex=30)
    assert sync_client.get("fix124:isolation") == "1"
    sync_client.delete("fix124:isolation")
    assert await async_client.exists("fix124:isolation") == 0
    assert no_sockets == []


@pytest.mark.asyncio
async def test_a_health_marker_lands_in_the_fake_and_is_gone_for_the_next_test(no_sockets):
    from app.services._redis_pool import get_async_redis
    from app.services.gateway import fallback_orchestrator as fo

    assert _is_fake(get_async_redis())  # checked BEFORE the write
    await fo.mark_provider_unhealthy("openai", "gpt-4o-mini", error_code="provider_timeout")
    assert await fo.is_provider_unhealthy("openai", "gpt-4o-mini")
    _redis_isolation.clear_health_keys()  # what conftest does around every test
    assert not await fo.is_provider_unhealthy("openai", "gpt-4o-mini")
    assert not await fo.is_provider_unhealthy("openai")
    assert no_sockets == []


def test_no_module_of_the_app_builds_a_client_the_redirect_cannot_see():
    """A client built with ``Redis(host=...)`` would not be redirected — it
    would be refused, and whatever relied on it would silently see an outage
    in every test. Pin that the app only ever builds clients from a URL."""
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    direct = re.compile(r"\b(?:redis(?:_lib|_asyncio)?|aioredis)\.(?:Strict)?Redis\(\s*(?!connection_pool)")
    offenders = [
        f"{path.relative_to(app_dir)}:{i}"
        for path in app_dir.rglob("*.py")
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if direct.search(line)
    ]
    assert offenders == []
