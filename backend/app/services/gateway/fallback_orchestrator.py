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
    from app.schemas.public import ChatCompletionsRequest
    from app.services.provider_dispatcher import ProviderDispatcher
    from app.services.provider_key_service import ProviderKeyService

logger = structlog.get_logger(__name__)


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
        if not chain:
            log.info("fallback_no_chain")
            raise ProviderError(
                f"No fallback chain registered for model '{original_model}'.",
                details={
                    "original_provider": original_provider,
                    "original_model": original_model,
                },
            )

        attempts: list[FallbackAttempt] = []
        last_error: GatewayError | None = None
        tried = 0

        for candidate in chain:
            if tried >= self.max_candidates:
                break

            provider = candidate["provider"]
            model = candidate["model"]

            # Key check first — a missing key is recorded but does NOT
            # consume one of the ``max_candidates`` real attempt slots.
            # The reason is operator-friendliness: a tenant that has
            # only configured one provider should still be able to
            # benefit from a fallback to a provider whose key IS set,
            # even if it sits further down the chain.
            api_key: str | None = None
            if session is not None:
                api_key = self.key_service.get_api_key(session, provider)
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
                log.warning(
                    "fallback_candidate_failed",
                    candidate_provider=provider,
                    candidate_model=model,
                    error_code=getattr(exc, "code", "provider_error"),
                    latency_ms=int(latency_ms),
                )
                continue

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
