"""Cross-provider fallback for failed primary dispatches.

When ``ProviderDispatcher.dispatch`` fails with a fallback-eligible
``GatewayError`` (timeout / 5xx / 429 / 404 / unavailable), the gateway
orchestrator hands control to :class:`FallbackOrchestrator`. The
fallback runner:

1. Looks up the model's tier and the ordered cross-provider chain
   via :mod:`app.services.providers.model_tiers`.
2. Walks at most ``max_candidates`` candidates (default 2 → 3 total
   attempts including the original).
3. For each candidate, resolves an API key via
   :class:`ProviderKeyService`. Missing keys are recorded as a
   no-op attempt and skipped — they are not counted toward the
   per-attempt budget so a misconfigured tenant still gets the
   benefit of two real fallback tries.
4. Dispatches with a 10s timeout. Success returns immediately
   wrapped in :class:`FallbackResult`. Failure records a
   :class:`FallbackAttempt` and continues.
5. If every candidate fails, the *last* GatewayError is re-raised so
   the original orchestrator's error path still maps it to a 502 /
   504 / 429 response.

The orchestrator is intentionally narrow: it does not run DLP, does
not touch the request_log queue, and does not build a public response
shape. Those concerns stay in :mod:`app.services.orchestrator`, which
wraps the returned ``ProviderResponse`` exactly like the success path.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

from app.core.exceptions import GatewayError, ProviderError
from app.domain.entities import RouteDecision
from app.services.providers.model_tiers import get_fallback_chain

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.domain.entities import RequestContext
    from app.domain.providers import ProviderResponse
    from app.schemas.common import Message
    from app.services.capability_detection import RequiredCapabilities
    from app.schemas.public import ChatCompletionsRequest
    from app.services.provider_dispatcher import ProviderDispatcher
    from app.services.provider_key_service import ProviderKeyService

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Redis-backed provider health markers.
#
# The streaming branch in ``public_chat.py`` cannot run the same
# retry-and-fall-back loop the non-streaming orchestrator uses — once
# the first SSE chunk is on the wire, headers and content type are
# already committed and a mid-stream provider swap would corrupt the
# response. Instead we use a short-TTL Redis key as a *health hint*:
#
#   * Every fallback-eligible failure SETs ``provider:health:{name}``
#     with a 60-second TTL.
#   * Every success DELs the same key.
#
# Before a streaming request invokes ``adapter.stream_chat()`` the
# route's primary provider is checked against this marker; if the
# key is present, the streaming branch swaps to the first viable
# fallback candidate BEFORE any bytes are written. Stale markers
# expire on their own.
#
# Failures here are silently ignored — the gateway must keep serving
# traffic during a Redis outage, and the worst case is a missed
# health hint (which is recovered by the in-generator try/except
# anyway).
# ---------------------------------------------------------------------------

_HEALTH_KEY_PREFIX = "provider:health:"
_HEALTH_TTL_SECONDS = 60


async def mark_provider_unhealthy(provider: str) -> None:
    """Set ``provider:health:{provider}`` with a 60s TTL.

    Called by the orchestrator + this module whenever a provider
    returns a fallback-eligible error. Idempotent — repeated calls
    refresh the TTL.
    """
    if not provider:
        return
    try:
        from app.services._redis_pool import get_async_redis

        client = get_async_redis()
        await client.set(
            f"{_HEALTH_KEY_PREFIX}{provider}",
            "unhealthy",
            ex=_HEALTH_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        # Health hint is best-effort — Redis outage must not break
        # the request path.
        pass


async def clear_provider_health(provider: str) -> None:
    """Delete the health marker for ``provider``.

    Called after every successful dispatch so a recovered provider
    doesn't keep getting skipped for the rest of the TTL window.
    """
    if not provider:
        return
    try:
        from app.services._redis_pool import get_async_redis

        client = get_async_redis()
        await client.delete(f"{_HEALTH_KEY_PREFIX}{provider}")
    except Exception:  # noqa: BLE001
        pass


async def is_provider_unhealthy(provider: str) -> bool:
    """Return True if the health marker for ``provider`` is set.

    Fail-open: any Redis error is treated as "healthy" so a Redis
    outage degrades to no-pre-check, not request-blocking.
    """
    if not provider:
        return False
    try:
        from app.services._redis_pool import get_async_redis

        client = get_async_redis()
        exists = await client.exists(f"{_HEALTH_KEY_PREFIX}{provider}")
        return bool(exists)
    except Exception:  # noqa: BLE001
        return False


# Per-attempt timeout. The primary dispatch has its own ~30s upstream
# timeout; the fallback shrinks the budget so a slow second vendor
# doesn't extend the user's wait past the original SLA.
FALLBACK_ATTEMPT_TIMEOUT_S = 10.0

# Maximum number of fallback candidates to try after the primary.
# Spec calls for 2 (so up to 3 total attempts including the original).
MAX_FALLBACK_CANDIDATES = 2


@dataclass(frozen=True)
class FallbackAttempt:
    """Record of a single fallback try that did NOT succeed.

    Persisted as one element of ``request_logs.fallback_attempts``
    (JSON-encoded). Kept flat + primitive-typed so the JSON round-trip
    is lossless and the admin dashboard can render without further
    transformation.
    """

    provider: str
    model: str
    error_code: str
    error_message: str
    latency_ms: float
    attempted_at: datetime

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "latency_ms": round(self.latency_ms, 2),
            "attempted_at": self.attempted_at.isoformat(),
        }


@dataclass
class FallbackResult:
    """Outcome of a successful fallback run.

    ``response`` is the canonical :class:`ProviderResponse` from the
    successful candidate; the caller (gateway orchestrator) wraps it
    into a ``ChatCompletionsResponse`` exactly like the success path.
    ``attempts`` enumerates every prior failed attempt; an empty list
    means the *first* fallback succeeded (attempt_number = 2).
    """

    response: "ProviderResponse"
    provider_used: str
    model_used: str
    attempt_number: int
    attempts: list[FallbackAttempt] = field(default_factory=list)


def merge_auto_candidates(
    auto_models: list,
    chain: list[dict[str, str]],
    *,
    original_provider: str,
    original_model: str,
    resolve,
) -> list[dict[str, str]]:
    """FIX35 Part C step 4 — turn the Auto router's ordered model list into
    the fallback chain, ahead of the static tier chain.

    The primary just FAILED on ``original_provider``; most failures are
    provider-level (no credits, rate limit, outage), so candidates on a
    DIFFERENT provider go first and same-provider ones after — otherwise
    the ``MAX_FALLBACK_CANDIDATES`` budget is spent re-hitting the provider
    that is down (seen live: gpt-5.6-terra → gpt-4o → gpt-4o-mini, all
    openai, all 429, while a working OpenRouter route sat third). Pure;
    ``resolve`` maps a model id to its provider (model_registry).
    """
    cross: list[dict[str, str]] = []
    same: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in auto_models or []:
        m = str(m)
        p = resolve(m) or ""
        if not p or m == original_model or (p, m) in seen:
            continue
        seen.add((p, m))
        (same if p == original_provider else cross).append({"provider": p, "model": m})
    merged = cross + same
    for e in chain or []:
        k = (e["provider"], e["model"])
        if k in seen:
            continue
        seen.add(k)
        merged.append(e)
    return merged


class FallbackOrchestrator:
    """Cross-provider fallback runner. Stateless, safe to share."""

    def __init__(
        self,
        dispatcher: "ProviderDispatcher",
        key_service: "ProviderKeyService",
        *,
        max_candidates: int = MAX_FALLBACK_CANDIDATES,
        attempt_timeout_s: float = FALLBACK_ATTEMPT_TIMEOUT_S,
    ) -> None:
        self.dispatcher = dispatcher
        self.key_service = key_service
        self.max_candidates = max_candidates
        self.attempt_timeout_s = attempt_timeout_s

    async def run(
        self,
        *,
        original_provider: str,
        original_model: str,
        request: "ChatCompletionsRequest",
        messages: list["Message"],
        session: "Session | None",
        ctx: "RequestContext",
        original_error: Exception | None = None,
    ) -> FallbackResult:
        """Run the fallback chain. Raises the last error if every try fails.

        ``messages`` is the DLP-masked sequence the primary dispatch
        used — fallback must NOT re-scan or re-mask the request, that
        already happened upstream. The same masked payload is sent to
        every candidate so the per-vendor responses share the same
        token-space for the gateway's outbound DLP pass.
        """
        log = logger.bind(
            tenant_id=ctx.tenant_id,
            project_id=ctx.project_id,
            request_id=ctx.request_id,
            original_provider=original_provider,
            original_model=original_model,
        )

        chain = get_fallback_chain(original_model, original_provider)
        # FIX35 Part C step 4 — an auto-routed request carries the router's
        # own ORDERED list (already key-valid, tier-capped, task-ranked).
        # Those come first; the static cross-provider tier chain follows.
        # An explicit request never has this metadata, so its behaviour is
        # unchanged.
        _auto = (getattr(request, "metadata", None) or {}).get("auto_candidates") or []
        if _auto:
            from app.services.providers.model_registry import resolve_provider

            merged = merge_auto_candidates(
                _auto, chain, original_provider=original_provider,
                original_model=original_model, resolve=resolve_provider,
            )
            if merged:
                log.info("fallback_auto_candidates", candidates=[f"{e['provider']}:{e['model']}" for e in merged[:5]])
                chain = merged
        if not chain:
            log.info("fallback_no_chain")
            # Batch A: a shed request (bulkhead full) must keep its
            # 503 + Retry-After semantics for the client. Wrapping it
            # in the generic no-chain 502 here would tell the caller
            # "provider broken" when the truthful signal is "provider
            # busy, retry shortly" — found live on a chain-less
            # self-hosted model.
            if (
                original_error is not None
                and getattr(original_error, "code", None) == "provider_at_capacity"
            ):
                raise original_error
            raise ProviderError(
                f"No fallback chain registered for model '{original_model}'.",
                details={
                    "original_provider": original_provider,
                    "original_model": original_model,
                },
            )

        from app.services.capability_detection import (
            detect_required_capabilities,
            model_meets,
        )

        # Hard capabilities (vision/tools) the request needs. A fallback
        # that can't serve them would silently produce a wrong answer.
        required_caps = detect_required_capabilities(request)

        attempts: list[FallbackAttempt] = []
        last_error: GatewayError | None = None
        tried = 0

        for candidate in chain:
            if tried >= self.max_candidates:
                break

            provider = candidate["provider"]
            model = candidate["model"]

            # Capability gate — skip a candidate that lacks a required
            # capability. Does NOT consume an attempt slot (mirrors the
            # missing-key skip) so the runner keeps looking for a model
            # that CAN serve the request.
            if not model_meets(required_caps, model):
                log.info(
                    "fallback_candidate_skipped",
                    candidate_provider=provider,
                    candidate_model=model,
                    reason="capability_mismatch",
                )
                continue

            # Key check first — a missing key is recorded but does NOT
            # consume one of the ``max_candidates`` real attempt slots.
            # The reason is operator-friendliness: a tenant that has
            # only configured one provider should still be able to
            # benefit from a fallback to a provider whose key IS set,
            # even if it sits further down the chain.
            api_key: str | None = None
            if session is not None:
                api_key = await self.key_service.get_api_key_async(session, provider)
            if not api_key:
                attempts.append(
                    FallbackAttempt(
                        provider=provider,
                        model=model,
                        error_code="no_api_key",
                        error_message=(
                            f"No API key configured for provider '{provider}'."
                        ),
                        latency_ms=0.0,
                        attempted_at=datetime.now(timezone.utc),
                    )
                )
                log.info(
                    "fallback_candidate_skipped",
                    candidate_provider=provider,
                    candidate_model=model,
                    reason="no_api_key",
                )
                continue

            tried += 1
            t0 = time.perf_counter()
            route = RouteDecision(provider_name=provider, model_name=model)
            try:
                response = await asyncio.wait_for(
                    self.dispatcher.dispatch(
                        route=route,
                        payload=request,
                        messages=messages,
                        api_key=api_key,
                    ),
                    timeout=self.attempt_timeout_s,
                )
            except asyncio.TimeoutError as exc:
                latency_ms = (time.perf_counter() - t0) * 1000
                last_error = ProviderError(
                    f"Fallback to '{provider}' timed out.",
                    code="provider_timeout",
                    details={"provider": provider, "model": model},
                )
                attempts.append(
                    FallbackAttempt(
                        provider=provider,
                        model=model,
                        error_code="provider_timeout",
                        error_message=str(exc)[:200] or "timeout",
                        latency_ms=latency_ms,
                        attempted_at=datetime.now(timezone.utc),
                    )
                )
                # Mark the failed candidate so the next streaming
                # request skips it via the Redis health pre-check.
                await mark_provider_unhealthy(provider)
                log.warning(
                    "fallback_candidate_failed",
                    candidate_provider=provider,
                    candidate_model=model,
                    error_code="provider_timeout",
                    latency_ms=int(latency_ms),
                )
                continue
            except GatewayError as exc:
                latency_ms = (time.perf_counter() - t0) * 1000
                last_error = exc
                attempts.append(
                    FallbackAttempt(
                        provider=provider,
                        model=model,
                        error_code=getattr(exc, "code", "provider_error"),
                        error_message=str(exc)[:200],
                        latency_ms=latency_ms,
                        attempted_at=datetime.now(timezone.utc),
                    )
                )
                if is_fallback_eligible(exc):
                    await mark_provider_unhealthy(provider)
                log.warning(
                    "fallback_candidate_failed",
                    candidate_provider=provider,
                    candidate_model=model,
                    error_code=getattr(exc, "code", "provider_error"),
                    latency_ms=int(latency_ms),
                )
                continue

            # Success — clear any stale health marker on this provider
            # so the streaming pre-check doesn't keep skipping it.
            await clear_provider_health(provider)

            # Success — attempt_number counts the primary as 1, so the
            # first fallback that succeeds is 2.
            attempt_number = 1 + len(attempts) + 1
            log.info(
                "fallback_candidate_success",
                candidate_provider=provider,
                candidate_model=model,
                attempt_number=attempt_number,
                prior_attempts=len(attempts),
            )
            return FallbackResult(
                response=response,
                provider_used=provider,
                model_used=model,
                attempt_number=attempt_number,
                attempts=attempts,
            )

        # All candidates exhausted. Re-raise the last error so the
        # caller's error path runs unchanged. attempts is dropped on the
        # raise — the orchestrator can still populate fallback_attempts
        # on the failed request_log row by inspecting ``attempts`` if it
        # caught a reference earlier; in practice the failure surface
        # uses last_error.code.
        log.warning(
            "fallback_exhausted",
            attempts=len(attempts),
            tried=tried,
        )
        if last_error is not None:
            raise last_error
        raise ProviderError(
            "Fallback exhausted with no candidates attempted.",
            details={
                "original_provider": original_provider,
                "original_model": original_model,
            },
        )


    def select_streaming_candidate(
        self,
        *,
        original_provider: str,
        original_model: str,
        session: "Session | None",
        skip_providers: set[str] | None = None,
        required: "RequiredCapabilities | None" = None,
    ) -> dict | None:
        """Pick the first viable streaming fallback for a primary.

        Streaming can't reuse :meth:`run` because that method completes
        a non-streaming dispatch. The streaming branch needs the
        chain-walk + key-check logic without actually firing the
        request. Returns ``{"provider", "model", "api_key"}`` on the
        first candidate that:

          1. Has an API key configured for the tenant (skipped
             without consuming a slot when missing).
          2. Has an adapter registered with the dispatcher AND
             ``supports_streaming=True`` (a non-streaming adapter
             would force the rare and lossy mid-stream swap).
          3. Is NOT in ``skip_providers`` (used by the in-generator
             error path to avoid re-trying a candidate that just
             failed this same request).

        Returns ``None`` when the chain is exhausted — callers should
        treat that as "stay with the primary and let the existing
        error path fire".
        """
        if session is None:
            return None

        skip = skip_providers or set()
        chain = get_fallback_chain(original_model, original_provider)
        tried = 0

        for candidate in chain:
            if tried >= self.max_candidates:
                break
            provider = candidate["provider"]
            model = candidate["model"]
            if provider in skip:
                continue

            # Capability gate — don't stream a vision/tools request from
            # a model that can't serve it.
            if required is not None:
                from app.services.capability_detection import model_meets

                if not model_meets(required, model):
                    continue

            api_key = self.key_service.get_api_key(session, provider)
            if not api_key:
                # No key → skip without consuming a real attempt slot,
                # mirroring the run() loop's defensive contract.
                continue

            adapter = self.dispatcher.adapters.get(provider)
            if adapter is None or not getattr(adapter, "supports_streaming", False):
                # Adapter exists but can't stream — keep walking the
                # chain so we don't fall back from streaming to
                # buffered + a header lie.
                continue

            tried += 1
            return {
                "provider": provider,
                "model": model,
                "api_key": api_key,
            }
        return None

    async def select_streaming_candidate_async(
        self,
        *,
        original_provider: str,
        original_model: str,
        session: "AsyncSession | None",
        skip_providers: set[str] | None = None,
        required: "RequiredCapabilities | None" = None,
    ) -> dict | None:
        """Async twin of :meth:`select_streaming_candidate`.

        The streaming **pre-check** runs while the request's AsyncSession is
        still open and on the event loop, so it must resolve the candidate
        key via ``get_api_key_async`` (awaited) — passing that async session
        to the sync :meth:`select_streaming_candidate` made
        ``AsyncSession.execute()`` return an un-awaited coroutine and blew up
        with ``'coroutine' object has no attribute 'scalars'`` whenever a
        provider was marked unhealthy. The in-generator error path keeps
        using the SYNC variant because its session is already closed and it
        mints a short-lived sync one instead.
        """
        if session is None:
            return None

        skip = skip_providers or set()
        chain = get_fallback_chain(original_model, original_provider)
        tried = 0

        for candidate in chain:
            if tried >= self.max_candidates:
                break
            provider = candidate["provider"]
            model = candidate["model"]
            if provider in skip:
                continue

            if required is not None:
                from app.services.capability_detection import model_meets

                if not model_meets(required, model):
                    continue

            api_key = await self.key_service.get_api_key_async(session, provider)
            if not api_key:
                continue

            adapter = self.dispatcher.adapters.get(provider)
            if adapter is None or not getattr(adapter, "supports_streaming", False):
                continue

            tried += 1
            return {
                "provider": provider,
                "model": model,
                "api_key": api_key,
            }
        return None


# ---------------------------------------------------------------------------
# Eligibility classifier
# ---------------------------------------------------------------------------

# Error codes that the gateway orchestrator treats as fallback-eligible.
# Note: ``rate_limit_exceeded`` is the upstream provider's 429 mapped
# by ``error_handler.map_provider_error``. The gateway's own per-key
# rate-limit (raised earlier in the pipeline) shares the class but
# never reaches the dispatch block, so this set is safe in context.
FALLBACK_ELIGIBLE_ERRORS: set[str] = {
    "provider_timeout",
    "provider_5xx",
    "provider_error",
    "provider_rate_limited",
    # Bulkhead full (per-provider concurrency cap) — shed to the next
    # candidate instead of queuing on a saturated box.
    "provider_at_capacity",
    "rate_limit_exceeded",
    "provider_model_not_found",
    "provider_unavailable",
}


def is_fallback_eligible(exc: GatewayError) -> bool:
    """Return True if ``exc`` should trigger the fallback chain."""
    code = getattr(exc, "code", None)
    if not code:
        return False
    return code in FALLBACK_ELIGIBLE_ERRORS
