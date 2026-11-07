"""FIX124 — no test can reach a real Redis.

The lane's containers sit on the network of the running stack and the test
settings resolve to ITS Redis, so a test that failed a primary on purpose
wrote a health marker the live gateway then honoured (found the hard way:
strike counters and a 15-minute marker on a real pair). Lowering a TTL is
not isolation — the write must be impossible.

Two layers, installed by ``conftest.py`` before anything imports the app:

* **redirect** — every pool built from a URL (``redis.from_url``,
  ``Redis.from_url``, ``ConnectionPool.from_url``, sync and asyncio — which is
  how the app builds all of its clients) gets fakeredis' connection class, so
  it talks to an in-process server that starts empty with each pytest run;
* **tripwire** — a connection that is NOT a fake one refuses to connect, with
  :class:`RealRedisInTests`, before a socket is opened. A client built any
  other way (``redis.Redis(host=...)``) therefore fails at its first command
  instead of writing.

``SRAN_ALLOW_REAL_REDIS_IN_TESTS=1`` lifts both, for the ``integration``
modules that exist to exercise a live environment; the bare lane never sets it.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

ALLOW_ENV = "SRAN_ALLOW_REAL_REDIS_IN_TESTS"

#: Key families the fallback machinery keeps in Redis; cleared between tests
#: so a marker one test set on purpose never decides another test's route.
HEALTH_KEY_PATTERNS = ("provider:health:*", "provider:strikes:*", "provider:fails:*")

_installed = False


class RealRedisInTests(RuntimeError):
    """A test tried to open a connection to a real Redis server."""


def allowed() -> bool:
    return os.environ.get(ALLOW_ENV, "").strip() == "1"


def install() -> bool:
    """Idempotent. Returns whether the isolation is active."""
    global _installed
    if allowed():
        return False
    if _installed:
        return True

    import redis
    import redis.asyncio as redis_asyncio
    from fakeredis import _connection as fake_sync
    from fakeredis import aioredis as fake_async
    from fakeredis._server import FakeBaseConnectionMixin
    from redis.asyncio.connection import AbstractConnection as AsyncAbstract
    from redis.connection import AbstractConnection as SyncAbstract

    def _redirect(pool_cls, fake_connection_class):
        original = pool_cls.from_url.__func__

        def from_url(cls, url, **kwargs):
            kwargs["connection_class"] = fake_connection_class
            # The fake has no password configured and answers AUTH with an
            # error; host, port and db are kept (they name the fake server).
            parts = urlsplit(url)
            if "@" in parts.netloc:
                url = urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[1]))
            for credential in ("username", "password"):
                kwargs.pop(credential, None)
            # No wire, nothing to health-check — and the fake's reply to the
            # check's PING fails redis-py's comparison on a decoding pool.
            kwargs["health_check_interval"] = 0
            return original(cls, url, **kwargs)

        pool_cls.from_url = classmethod(from_url)

    _redirect(redis.ConnectionPool, fake_sync.FakeConnection)
    _redirect(redis_asyncio.ConnectionPool, fake_async.FakeConnection)

    def _refusal(conn) -> RealRedisInTests:
        where = f"{getattr(conn, 'host', '?')}:{getattr(conn, 'port', '?')}"
        return RealRedisInTests(
            f"a test tried to connect to a real Redis at {where}; tests run on the "
            f"in-process fake (tests/_redis_isolation.py). Only an `integration` run "
            f"may set {ALLOW_ENV}=1."
        )

    sync_connect = SyncAbstract.connect
    async_connect = AsyncAbstract.connect

    def connect(self, *args, **kwargs):
        if not isinstance(self, FakeBaseConnectionMixin):
            raise _refusal(self)
        return sync_connect(self, *args, **kwargs)

    async def aconnect(self, *args, **kwargs):
        if not isinstance(self, FakeBaseConnectionMixin):
            raise _refusal(self)
        return await async_connect(self, *args, **kwargs)

    SyncAbstract.connect = connect
    AsyncAbstract.connect = aconnect
    _installed = True
    return True


def clear_health_keys() -> None:
    """Drop the fallback machinery's keys from the fake server."""
    if not _installed:
        return
    import redis

    from app.core.config import settings

    client = redis.from_url(settings.redis_url)
    try:
        for pattern in HEALTH_KEY_PATTERNS:
            keys = list(client.scan_iter(match=pattern))
            if keys:
                client.delete(*keys)
    finally:
        client.close()
