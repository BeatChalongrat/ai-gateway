"""Upstream error mapping and retry logic for provider adapters.

Maps httpx transport errors and provider HTTP error responses to the
gateway's domain exception hierarchy.
"""

from __future__ import annotations

import asyncio
import random

import httpx

from app.core.exceptions import (
    ContextLengthExceededError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitExceededError,
    UpstreamBadResponseError,
)

# HTTP status codes that are retryable. 504 (upstream LB/gateway
# timeout) is as transient as 502/503 — Batch A added it.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Ceiling for any single retry sleep — also caps an honored Retry-After
# so a provider saying "come back in 120s" can't stall a synchronous
# request past its own middleware/worker timeouts. Deliberately a
# constant: nobody tunes this per deployment.
_MAX_RETRY_SLEEP_SECONDS = 10.0


def _backoff_delay(
    base_delay: float, attempt: int, retry_after: float | None = None
) -> float:
    """Exponential backoff with EQUAL JITTER, honoring Retry-After.

    Equal jitter (half deterministic + half random) keeps a floor under
    the delay while desynchronizing retries — a burst that failed
    together must not retry together and re-flatten the same box
    (thundering herd). When the provider sent a parseable Retry-After
    (429/503 — the box saying "I'm full"), it acts as a FLOOR under the
    computed delay; the constant ceiling above always wins last.
    """
    delay = base_delay * (2 ** attempt)
    delay = delay / 2 + random.uniform(0.0, delay / 2)
    if retry_after is not None and retry_after > 0:
        delay = max(delay, retry_after)
    return min(delay, _MAX_RETRY_SLEEP_SECONDS)


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Seconds form of Retry-After only; HTTP-date form is ignored."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None

# Context-window-exceeded signatures, matched case-insensitively against a
# provider 400 body (and ONLY a 400 — a 5xx that mentions tokens is a server
# fault, not an oversized prompt). Each entry is a tuple of substrings that
# must ALL appear. Extend here when a new provider phrasing shows up.
CONTEXT_LENGTH_SIGNATURES: tuple[tuple[str, ...], ...] = (
    ("maximum context length",),                 # OpenAI-compatible
    ("context window", "token"),                 # OpenAI-compatible variants
    ("prompt is too long",),                     # Anthropic
    ("exceeds the maximum number of tokens",),   # Google / Gemini
    ("input token count", "exceeds"),            # Google / Gemini
)

# OpenAI-compatible providers also send a machine-readable error code.
_CONTEXT_LENGTH_ERROR_CODE = "context_length_exceeded"


def _is_context_length_error(body: dict | str) -> bool:
    """True when a provider 400 body carries a context-window signature."""
    if isinstance(body, dict):
        error = body.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, str) and code.lower() == _CONTEXT_LENGTH_ERROR_CODE:
            return True
    lowered = str(body).lower()
    return any(all(part in lowered for part in sig) for sig in CONTEXT_LENGTH_SIGNATURES)


def _provider_message(body: dict | str) -> str:
    """Extract the provider's human-readable error message from its body."""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:500]
    return str(body)[:500]


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

        # FIX49 Part C — 402 (payment / budget). OpenRouter answers 402
        # ``in_flight_budget_exhausted`` WITH ``Retry-After: 120`` when a
        # per-window budget is briefly exhausted — the provider itself
        # says "try again in 120 s". That used to fall into the generic
        # 4xx bucket (upstream_bad_response, retryable=false), so the
        # client hid the retry button on an error the provider promised
        # would clear. With Retry-After → the retryable rate-limit class
        # (FIX47 catalogue, retryable=true, carries retry_after).
        # Without Retry-After the account is simply out of credits —
        # still fallback-eligible (another provider can answer) but not
        # something a user retry fixes.
        if status == 402:
            retry_after = exc.response.headers.get("retry-after")
            if retry_after:
                return RateLimitExceededError(
                    f"Provider '{provider_name}' budget exhausted; provider "
                    f"says retry in {retry_after}s.",
                    details={
                        "provider": provider_name,
                        "status": status,
                        "retry_after": retry_after,
                        "body": body,
                    },
                )
            # FIX51 Part 4 — a bare 402 is an ACCOUNT-level failure, not a
            # generic provider hiccup: every model on this account is dead
            # until someone tops up. The distinct code lets the fallback
            # runner refuse to walk sibling models on the same account
            # (classify_failure_scope) and lets the health marker go
            # provider-wide, which is what actually ends the "both rescue
            # hops die together" pattern when one aggregator runs dry.
            return ProviderError(
                f"Provider '{provider_name}' reported insufficient credits (402).",
                code="provider_no_credit",
                details={"provider": provider_name, "status": status, "body": body},
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
            if status == 400 and _is_context_length_error(body):
                # Typed so clients can trim and retry instead of guessing
                # from a generic 502. NOT fallback-eligible by design —
                # a too-long prompt fails on every provider.
                return ContextLengthExceededError(
                    details={
                        "provider": provider_name,
                        "provider_message": _provider_message(body),
                    },
                )
            return UpstreamBadResponseError(
                f"Provider '{provider_name}' rejected request ({status}).",
                details={"provider": provider_name, "status": status, "body": body},
            )

    return ProviderError(
        f"Unexpected error from provider '{provider_name}': {exc}",
        details={"provider": provider_name, "error_type": type(exc).__name__},
    )


# FIX49 Part A — providers also fail INSIDE a 200 stream. OpenAI's
# Responses API answers 200 and then delivers e.g. insufficient_quota as
# ``type: "error"`` / ``response.failed`` events; OpenRouter forwards
# upstream failures as ``{"error": {...}}`` data frames on the
# chat-completions shape. Both parsers used to drop those lines as
# unknown types, so the stream ended cleanly with zero content and the
# gateway recorded SUCCESS — the 21/21 empty ``gpt-5.6-terra`` turns of
# the 2026-09-03 audit, measured live. This maps the in-stream payload
# to the same domain exceptions map_provider_error produces for HTTP
# statuses, so the route layer's fallback/error machinery takes over.
def map_stream_error_payload(err: dict, provider_name: str) -> Exception:
    """Map an in-stream provider error payload (no HTTP status) to a
    domain exception."""
    etype = str(err.get("type") or "")
    ecode = str(err.get("code") or "")
    message = str(err.get("message") or "")[:500]
    blob = f"{etype} {ecode} {message}".lower()
    details = {"provider": provider_name, "status": None, "body": {"error": err}}
    if any(m in blob for m in ("insufficient_quota", "credit", "rate limit", "rate_limit")):
        return RateLimitExceededError(
            f"Provider '{provider_name}' reported quota/rate exhaustion "
            f"mid-stream.",
            details=details,
        )
    if "overloaded" in blob or "unavailable" in blob:
        return ProviderUnavailableError(
            f"Provider '{provider_name}' reported unavailability mid-stream.",
            details=details,
        )
    return ProviderError(
        f"Provider '{provider_name}' reported an error mid-stream: "
        f"{message or etype or ecode or 'unknown'}",
        details=details,
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
                retry_after = (
                    _parse_retry_after(exc.response)
                    if exc.response.status_code in (429, 503)
                    else None
                )
                await asyncio.sleep(
                    _backoff_delay(base_delay, attempt, retry_after)
                )
                continue
            raise map_provider_error(exc, provider_name) from exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                await asyncio.sleep(_backoff_delay(base_delay, attempt))
                continue
            raise map_provider_error(exc, provider_name) from exc

    raise map_provider_error(last_exc, provider_name) from last_exc  # type: ignore[arg-type]


def _safe_json(response: httpx.Response) -> dict | str:
    try:
        return response.json()
    except Exception:
        return response.text[:500]
