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
# each gets its own client/pool. These limits are per-client (per
# base_url), sized for the Phase-1 target (low-hundreds RPS / worker)
# with headroom; tune via the constants if a provider needs more.
_MAX_KEEPALIVE_CONNECTIONS = 20
_MAX_CONNECTIONS = 50
_KEEPALIVE_EXPIRY = 30.0

# One client per base_url, lazily created and reused. Bound to the
# worker's single event loop (UvicornWorker = one loop per process).
_clients: dict[str, httpx.AsyncClient] = {}
_lock = asyncio.Lock()


def _build_async_client(
    base_url: str, timeout_connect: float, timeout_read: float
) -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=timeout_connect,
        read=timeout_read,
        write=5.0,
        pool=5.0,
    )
    limits = httpx.Limits(
        max_keepalive_connections=_MAX_KEEPALIVE_CONNECTIONS,
        max_connections=_MAX_CONNECTIONS,
        keepalive_expiry=_KEEPALIVE_EXPIRY,
    )
    return httpx.AsyncClient(
        base_url=base_url,
        headers=dict(_DEFAULT_HEADERS),
        timeout=timeout,
        limits=limits,
    )


async def get_provider_client(
    *,
    base_url: str,
    timeout_connect: float = 5.0,
    timeout_read: float = 30.0,
) -> httpx.AsyncClient:
    """Return the shared pooled client for ``base_url`` (lazy + cached).

    Auth / per-request headers go on the request call, never here. A
    client that has been closed (e.g. shutdown raced a late request) is
    transparently rebuilt so callers never get a broken client.

    The timeout is taken from the FIRST creation for a given base_url
    (all callers pass the same ``settings.provider_timeout_*`` values,
    so this is uniform); per-request overrides remain possible via the
    request call's ``timeout=`` argument.
    """
    client = _clients.get(base_url)
    if client is not None and not client.is_closed:
        return client
    async with _lock:
        client = _clients.get(base_url)
        if client is None or client.is_closed:
            client = _build_async_client(base_url, timeout_connect, timeout_read)
            _clients[base_url] = client
        return client


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
