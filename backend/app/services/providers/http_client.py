"""Shared, pooled HTTP clients for provider adapters.

Phase-1 B1 fix. A single ``httpx.AsyncClient`` is kept **per provider
base URL** and reused across requests, so the TCP+TLS connection pool
survives between calls instead of paying a fresh DNS+TCP+TLS handshake
on every request (the old ``create_provider_client`` + ``async with``
pattern closed the client — and its pool — after each call, adding
~50–200 ms to every TTFB).

Contract for callers:

* Get the client with ``await get_provider_client(base_url=...)``.
* Pass **auth and any per-request headers on the request call**
  (``client.post(..., headers=...)`` / ``client.stream(..., headers=...)``)
  — NEVER bake them into the shared client. The API key can differ per
  request (key rotation, per-tenant keys), so a client keyed only on
  ``base_url`` is safe to share across keys.
* Do NOT ``async with`` the returned client and do NOT ``aclose()`` it —
  it is process-shared. Closing the per-request *stream* response
  (``async with client.stream(...) as resp``) is still correct.

The pool is closed once on app shutdown via ``close_all_clients`` (wired
into the FastAPI lifespan).
"""

from __future__ import annotations

import asyncio

import httpx

_DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# Pool sizing. The gateway talks to <~10 distinct provider base URLs;
# each gets its own client/pool. Batch A: the limits moved to settings
# (env PROVIDER_POOL_MAX_CONNECTIONS / _MAX_KEEPALIVE_CONNECTIONS /
# _KEEPALIVE_EXPIRY_SECONDS, defaults identical to the old constants:
# 50 / 20 / 30s) plus a per-provider max_connections override map
# (PROVIDER_POOL_OVERRIDES JSON) so a small self-hosted box isn't
# granted 50 warm sockets it can't use.
def _pool_limits_for(provider_name: str | None) -> httpx.Limits:
    """Resolve pool limits: per-provider override → env default."""
    from app.core.config import settings

    max_connections = settings.provider_pool_max_connections
    if provider_name:
        max_connections = settings.provider_pool_overrides_map.get(
            provider_name, max_connections
        )
    # Keepalive can never usefully exceed the connection cap.
    max_keepalive = min(
        settings.provider_pool_max_keepalive_connections, max_connections
    )
    return httpx.Limits(
        max_keepalive_connections=max_keepalive,
        max_connections=max_connections,
        keepalive_expiry=settings.provider_pool_keepalive_expiry_seconds,
    )


# One client per base_url, lazily created and reused. Bound to the
# worker's single event loop (UvicornWorker = one loop per process).
_clients: dict[str, httpx.AsyncClient] = {}
_lock = asyncio.Lock()


def _build_async_client(
    base_url: str,
    timeout_connect: float,
    timeout_read: float,
    provider_name: str | None = None,
) -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=timeout_connect,
        read=timeout_read,
        write=5.0,
        pool=5.0,
    )
    return httpx.AsyncClient(
        base_url=base_url,
        headers=dict(_DEFAULT_HEADERS),
        timeout=timeout,
        limits=_pool_limits_for(provider_name),
    )


async def get_provider_client(
    *,
    base_url: str,
    timeout_connect: float = 5.0,
    timeout_read: float = 30.0,
    provider_name: str | None = None,
) -> httpx.AsyncClient:
    """Return the shared pooled client for ``base_url`` (lazy + cached).

    Auth / per-request headers go on the request call, never here. A
    client that has been closed (e.g. shutdown raced a late request) is
    transparently rebuilt so callers never get a broken client.

    The timeout AND pool limits are captured at the FIRST creation for
    a given base_url (all callers pass the same settings values, and
    base_url↔provider is 1:1 in practice, so this is uniform);
    per-request overrides remain possible via the request call's
    ``timeout=`` argument. ``provider_name`` only selects the pool-size
    override — omitting it falls back to the global env defaults.
    """
    client = _clients.get(base_url)
    if client is not None and not client.is_closed:
        return client
    async with _lock:
        client = _clients.get(base_url)
        if client is None or client.is_closed:
            client = _build_async_client(
                base_url, timeout_connect, timeout_read, provider_name
            )
            _clients[base_url] = client
        return client


async def evict_client(base_url: str) -> None:
    """Close and drop the pooled client for ``base_url``, if any.

    Called when an admin edits a provider's endpoint: without this the
    old URL's client (and its keepalive pool) would sit in ``_clients``
    until process shutdown. A next request for the URL would still get
    a fresh client (cache keyed by URL), so this is hygiene, not
    correctness.
    """
    async with _lock:
        client = _clients.pop(base_url, None)
    if client is not None:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


async def close_all_clients() -> None:
    """Close every pooled client. Wired into the app lifespan shutdown."""
    async with _lock:
        for client in _clients.values():
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        _clients.clear()


def create_provider_client(
    *,
    base_url: str,
    timeout_connect: float = 5.0,
    timeout_read: float = 30.0,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """DEPRECATED — builds a fresh, un-pooled client (new TLS handshake
    every call). Retained only for tests / legacy callers. Production
    adapters use :func:`get_provider_client` + per-request headers so
    the connection pool is reused.
    """
    default_headers = dict(_DEFAULT_HEADERS)
    if headers:
        default_headers.update(headers)

    timeout = httpx.Timeout(
        connect=timeout_connect,
        read=timeout_read,
        write=5.0,
        pool=5.0,
    )

    return httpx.AsyncClient(
        base_url=base_url,
        headers=default_headers,
        timeout=timeout,
    )
