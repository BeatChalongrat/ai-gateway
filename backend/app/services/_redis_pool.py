"""Shared ``redis.asyncio`` connection pool.

Every Redis-backed service (cache, rate limiter, conversation vault)
constructed its own ``redis.from_url`` client before the May 2026
scalability work. At 100 gateway pods that meant 100 × N independent
connection pools, and the sync client blocked the event loop on
every Redis round-trip.

This module owns ONE async-aware ConnectionPool per process. Services
build a ``redis.asyncio.Redis`` over it; the underlying TCP fan-out
to Redis is bounded by ``max_connections`` rather than by the count
of services × pods.

The sync-to-async bridge below is a transitional helper. Public
service methods that have not yet been awaited from their callers
use ``run_sync()`` to invoke the asyncio Redis client from a sync
context. Callers that are already inside ``async def`` can — and
should — switch to the matching ``_async`` method directly.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Awaitable, TypeVar

from app.core.config import settings

# These knobs are the audit fix's targets: 50 baseline / 100 burst.
# Match the connection cap on a typical Redis 7 deployment (default
# maxclients=10000) — even 100 pods × 100 connections = 10000.
_POOL_SIZE = 50
_MAX_CONNECTIONS = 100

_pool_lock = threading.Lock()
_pool: Any = None
_thread_executor: concurrent.futures.ThreadPoolExecutor | None = None

T = TypeVar("T")


def get_async_pool() -> Any:
    """Return the process-wide ``redis.asyncio`` ConnectionPool.

    Lazily created on first call. ``decode_responses=True`` makes
    every ``GET`` / ``HMGET`` return ``str`` (or ``None``) instead of
    bytes, matching the existing sync-client behaviour so service
    code doesn't need to decode.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            # Lazy import so the test suite doesn't pull redis.asyncio
            # into modules that monkeypatch redis at import time.
            from redis import asyncio as redis_asyncio

            _pool = redis_asyncio.ConnectionPool.from_url(
                settings.redis_url,
                max_connections=_MAX_CONNECTIONS,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
                health_check_interval=30,
            )
        return _pool


def get_async_redis() -> Any:
    """Return a ``redis.asyncio.Redis`` client backed by the shared pool.

    The Redis class is cheap — it's just a façade over the pool — so
    services hold one per service instance without contention.
    """
    from redis import asyncio as redis_asyncio

    return redis_asyncio.Redis(connection_pool=get_async_pool())


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the shared bridge executor (lazy)."""
    global _thread_executor
    if _thread_executor is None:
        # One thread is enough — the bridge work is I/O bound and
        # very short (one Redis round-trip). More threads invite
        # contention without buying anything.
        _thread_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="redis-sync-bridge"
        )
    return _thread_executor


def run_sync(coro: Awaitable[T]) -> T:
    """Execute *coro* from a sync caller, returning its result.

    Two code paths:

    * No running event loop (a normal sync function, e.g. a CLI
      script, a test fixture): call ``asyncio.run`` directly.
    * Running loop (a FastAPI sync handler dispatched onto the
      thread pool, or a sync utility called from inside ``await``):
      run the coroutine on a fresh loop in a worker thread.

    The second path is the unavoidable cost of preserving sync method
    signatures while the I/O itself is async. Callers that already
    have ``await`` available should use the matching ``_async`` method
    on the service instead.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None or not loop.is_running():
        return asyncio.run(_await_coro(coro))

    executor = _get_executor()
    return executor.submit(lambda: asyncio.run(_await_coro(coro))).result()


async def _await_coro(coro: Awaitable[T]) -> T:
    """Trivial wrapper so ``asyncio.run`` can accept a coroutine produced
    elsewhere (``asyncio.run`` requires a coroutine object — most
    factories already return one, but routing through this helper
    is robust against minor API drift)."""
    return await coro
