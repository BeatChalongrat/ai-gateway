"""Shared HTTP client factory for provider adapters.

Each adapter creates its own client via this factory.  The caller owns
the lifecycle (use ``async with`` or explicit ``aclose()``).
"""

from __future__ import annotations

import httpx


def create_provider_client(
    *,
    base_url: str,
    timeout_connect: float = 5.0,
    timeout_read: float = 30.0,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """Build a configured ``httpx.AsyncClient`` for a provider.

    Parameters
    ----------
    base_url : str
        Provider API base URL (e.g. ``https://api.openai.com/v1``).
    timeout_connect : float
        TCP connect timeout in seconds.
    timeout_read : float
        Read/response timeout in seconds.
    headers : dict | None
        Extra headers merged with defaults.
    """
    default_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
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
