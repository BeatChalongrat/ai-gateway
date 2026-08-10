"""Per-provider concurrency bulkhead (Batch A).

Bounds the number of concurrent in-flight upstream calls PER PROVIDER
so a small self-hosted box can't be flattened by a burst that a cloud
provider would absorb. Uncapped providers (the default — the caps map
is empty unless ``PROVIDER_CONCURRENCY_CAPS`` is set) pass through with
zero overhead.

Shedding, not queuing: a request that can't get a slot within
``provider_concurrency_acquire_timeout_seconds`` raises
``ProviderAtCapacityError`` (503 + Retry-After on the non-stream path;
fallback-eligible so streaming chat routes to the next candidate).
Waiting longer would only build an invisible queue in front of a
saturated box.

SCOPE — read this before sizing the cap:
    The semaphore lives in process memory, one per gunicorn worker
    (UvicornWorker = one event loop per process). The ceiling a
    provider actually sees is therefore

        cap × GUNICORN_WORKERS (× pods when horizontally scaled)

    e.g. cap=4 behind the default 4 workers = up to 16 concurrent.
    A global cross-worker/pod cap is deliberately NOT built here — that
    needs shared state and belongs in the Redis-backed Rust rate-limiter
    (see docs/DEFERRED_WORK.md). Per the configurability audit, don't
    reach for that until a per-process cap is shown to be insufficient.

Both egress shapes are covered:
    - non-stream: ``provider_slot`` context manager wraps
      ``ProviderDispatcher.dispatch`` (every non-stream path funnels
      through it — Layer-1 hops, Layer-2 chain, health probes).
    - streaming: ``guarded_stream`` wraps the adapter's chunk iterator
      at the single ``public_chat`` call site; the slot is held for the
      stream's whole lifetime (that IS the in-flight window) and
      released on completion, error, or client disconnect
      (GeneratorExit → ``async with`` __aexit__).
"""

from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager

import structlog

from app.core.config import settings
from app.core.exceptions import ProviderAtCapacityError

logger = structlog.get_logger(__name__)

# provider_name → semaphore. Created lazily on first capped call; keyed
# per worker process (one event loop per UvicornWorker, same pattern as
# http_client._clients). Tests that spin fresh event loops must call
# ``reset_provider_semaphores()`` between loops.
_semaphores: dict[str, asyncio.Semaphore] = {}


def reset_provider_semaphores() -> None:
    """Drop all semaphores (tests only — prod never needs this)."""
    _semaphores.clear()


def _semaphore_for(provider_name: str) -> asyncio.Semaphore | None:
    cap = settings.provider_concurrency_caps_map.get(provider_name)
    if not cap:
        return None
    sem = _semaphores.get(provider_name)
    if sem is None:
        sem = asyncio.Semaphore(cap)
        _semaphores[provider_name] = sem
    return sem


def _at_capacity_error(provider_name: str) -> ProviderAtCapacityError:
    timeout = settings.provider_concurrency_acquire_timeout_seconds
    retry_after = max(1, math.ceil(timeout))
    return ProviderAtCapacityError(
        f"Provider '{provider_name}' is at its configured concurrency "
        f"limit — request shed after waiting {timeout:.1f}s for a slot.",
        details={
            "provider": provider_name,
            "cap": settings.provider_concurrency_caps_map.get(provider_name),
            "acquire_timeout_seconds": timeout,
        },
        headers={"Retry-After": str(retry_after)},
    )


@asynccontextmanager
async def provider_slot(provider_name: str):
    """Hold one in-flight slot for ``provider_name`` (no-op if uncapped)."""
    sem = _semaphore_for(provider_name)
    if sem is None:
        yield
        return

    try:
        await asyncio.wait_for(
            sem.acquire(),
            timeout=settings.provider_concurrency_acquire_timeout_seconds,
        )
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            "provider_slot_shed",
            provider=provider_name,
            cap=settings.provider_concurrency_caps_map.get(provider_name),
        )
        raise _at_capacity_error(provider_name) from None

    try:
        yield
    finally:
        sem.release()


async def guarded_stream(provider_name: str, aiter_factory):
    """Wrap a streaming adapter call in a provider slot.

    ``aiter_factory`` is a zero-arg callable returning the async
    iterator (``lambda: adapter.stream_chat(...)``) so the upstream
    connection is only opened once the slot is held. The ``async with``
    inside a generator guarantees release on normal exhaustion, on an
    exception from the upstream iterator, and on consumer abandonment
    (client disconnect → GeneratorExit → aclose → __aexit__).
    """
    async with provider_slot(provider_name):
        async for chunk in aiter_factory():
            yield chunk
