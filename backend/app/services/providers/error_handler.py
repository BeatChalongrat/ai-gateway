"""Upstream error mapping and retry logic for provider adapters.

Maps httpx transport errors and provider HTTP error responses to the
gateway's domain exception hierarchy.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.exceptions import (
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitExceededError,
    UpstreamBadResponseError,
)

# HTTP status codes that are retryable
_RETRYABLE_STATUS = {429, 500, 502, 503}


def map_provider_error(exc: Exception, provider_name: str) -> Exception:
    """Map an httpx or HTTP error to a domain exception."""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(
            f"Provider '{provider_name}' timed out.",
            details={"provider": provider_name, "error_type": type(exc).__name__},
        )

    if isinstance(exc, httpx.ConnectError):
        return ProviderUnavailableError(
            f"Provider '{provider_name}' is unreachable.",
            details={"provider": provider_name},
        )

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = _safe_json(exc.response)

        if status == 429:
            retry_after = exc.response.headers.get("retry-after")
            return RateLimitExceededError(
                f"Provider '{provider_name}' rate limit exceeded.",
                details={
                    "provider": provider_name,
                    "status": status,
                    "retry_after": retry_after,
                    "body": body,
                },
            )

        if status in (502, 503):
            return ProviderUnavailableError(
                f"Provider '{provider_name}' returned {status}.",
                details={"provider": provider_name, "status": status, "body": body},
            )

        if status >= 500:
            return ProviderError(
                f"Provider '{provider_name}' returned {status}.",
                details={"provider": provider_name, "status": status, "body": body},
            )

        if status in (401, 403):
            # The provider rejected OUR credentials — a gateway-side
            # key problem, not a client error. Map to ProviderError
            # (code "provider_error", which IS fallback-eligible) so
            # the fallback chain can route around a bad/expired key to
            # a provider whose key works, instead of surfacing an empty
            # response. Distinct from a generic 4xx (bad request) below,
            # which is NOT retried elsewhere because it would fail on
            # every provider.
            return ProviderError(
                f"Provider '{provider_name}' rejected credentials ({status}).",
                details={"provider": provider_name, "status": status, "body": body},
            )

        if status == 404:
            # Model / endpoint not found at this provider (e.g. an
            # aggregator that doesn't actually serve the requested model
            # id). Fallback-eligible so routing can try another candidate
            # rather than dead-ending — this is a "wrong place" error, not
            # a malformed request.
            return ProviderError(
                f"Provider '{provider_name}' has no endpoint for the "
                f"requested model (404).",
                details={"provider": provider_name, "status": status, "body": body},
            )

        if status >= 400:
            return UpstreamBadResponseError(
                f"Provider '{provider_name}' rejected request ({status}).",
                details={"provider": provider_name, "status": status, "body": body},
            )

    return ProviderError(
        f"Unexpected error from provider '{provider_name}': {exc}",
        details={"provider": provider_name, "error_type": type(exc).__name__},
    )


async def call_with_retry(
    coro_factory,
    *,
    provider_name: str,
    max_retries: int = 2,
    base_delay: float = 0.5,
):
    """Call a coroutine factory with retry on transient failures.

    Parameters
    ----------
    coro_factory : callable
        A zero-arg callable that returns an awaitable (e.g. ``lambda: client.post(...)``).
    provider_name : str
        For error messages.
    max_retries : int
        Maximum number of retries after the initial attempt.
    base_delay : float
        Base delay in seconds for exponential backoff.
    """
    last_exc: Exception | None = None
    attempts = 1 + max_retries

    for attempt in range(attempts):
        try:
            resp = await coro_factory()
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code in _RETRYABLE_STATUS and attempt < attempts - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
            raise map_provider_error(exc, provider_name) from exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
            raise map_provider_error(exc, provider_name) from exc

    raise map_provider_error(last_exc, provider_name) from last_exc  # type: ignore[arg-type]


def _safe_json(response: httpx.Response) -> dict | str:
    try:
        return response.json()
    except Exception:
        return response.text[:500]
