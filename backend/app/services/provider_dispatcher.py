"""Provider dispatcher with fallback support.

Dispatches to the primary adapter. If it fails with a fallback-eligible
provider error (timeout, 5xx, rate-limited, model-not-found), tries up
to ``max_hops`` Layer-1 fallback candidates from the routing result.

Layer-1 (here) handles same-tier alternatives ranked by the cost/latency
scorer in ``RoutingService._route_auto``. Layer-2 (the cross-provider
tier chain in ``gateway.fallback_orchestrator``) runs from the gateway
orchestrator AFTER this method exhausts.
"""

from __future__ import annotations

import time
import structlog
from typing import TYPE_CHECKING, Callable

from app.core.exceptions import GatewayError, ProviderError
from app.domain.providers import ProviderResponse
from app.domain.routing import ModelCandidate, RoutingResult
from app.services.providers.openai_adapter import OpenAIAdapter
from app.services.providers.anthropic_adapter import AnthropicAdapter
from app.services.providers.openrouter_adapter import OpenRouterAdapter
from app.services.providers.minimax_adapter import MinimaxAdapter
from app.services.providers.qwen_adapter import QwenAdapter
from app.services.providers.kimi_adapter import KIMIAdapter
from app.services.providers.zai_adapter import ZAIAdapter
from app.services.providers.google_adapter import GoogleAIAdapter
from app.services.providers.groq_adapter import GroqAdapter
from app.services.providers.deepseek_adapter import DeepSeekAdapter
from app.services.providers.xai_adapter import XAIAdapter
# Pending providers — adapter stubs at ``providers/_pending/`` raise
# ``ProviderNotImplementedError``. Kept on disk for future use but
# deliberately NOT imported here so the dispatcher cannot accidentally
# route a request to a non-functional provider. Re-add the imports +
# registrations below + the corresponding seed entries in
# ``model_registry._PENDING_PROVIDERS`` when each one is production-ready.

if TYPE_CHECKING:
    from app.schemas.common import Message

logger = structlog.get_logger(__name__)

# Errors that trigger fallback (subclasses of ProviderError). The
# ``is_fallback_eligible`` classifier below is the canonical gate —
# this tuple just filters the broad ``Exception`` catch down to the
# provider-error family before we even consult the classifier.
_FALLBACK_ERRORS = (ProviderError,)

# Max Layer-1 fallback hops after the primary attempt. 2 + primary = 3
# total attempts within Layer 1. Layer 2 (cross-provider tier chain in
# ``gateway.fallback_orchestrator``) runs separately if Layer 1
# exhausts with a fallback-eligible error.
_MAX_LAYER1_HOPS = 2


def _provider_status(exc: Exception) -> int | None:
    """Extract the upstream HTTP status from a mapped provider error.

    ``map_provider_error`` stuffs the provider's status code into
    ``exc.details['status']``. Returns it (e.g. 401/403/404/500) or None
    when the error carries no status (transport error, unmapped). Used to
    single out auth failures for the B3 key-quarantine retry.
    """
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        status = details.get("status")
        if isinstance(status, int):
            return status
    return None


class ProviderDispatcher:
    def __init__(self) -> None:
        self.adapters = {
            "openai": OpenAIAdapter(),
            "anthropic": AnthropicAdapter(),
            "openrouter": OpenRouterAdapter(),
            "minimax": MinimaxAdapter(),
            "qwen": QwenAdapter(),
            "kimi": KIMIAdapter(),
            "zai": ZAIAdapter(),
            "google": GoogleAIAdapter(),
            "groq": GroqAdapter(),
            "deepseek": DeepSeekAdapter(),
            "xai": XAIAdapter(),
            # mistral / cohere / perplexity intentionally absent — see
            # the import block comment above. A request that routes to
            # any of these will raise KeyError in ``dispatch`` and the
            # caller will get a clean ``ProviderUnavailableError``.
        }

    async def dispatch(self, route, payload, messages: list[Message], *, api_key: str | None = None) -> ProviderResponse:
        """Dispatch to primary adapter using route.provider_name / route.model_name."""
        adapter = self.adapters.get(route.provider_name)
        if adapter is None:
            # Unknown / disabled provider (e.g. mistral / cohere / perplexity
            # are deliberately not registered while their adapters are
            # pending). Raise a typed error instead of letting a bare
            # KeyError bubble up — the orchestrator's error mapping turns
            # this into a 503 with a clean message.
            from app.core.exceptions import ProviderUnavailableError
            raise ProviderUnavailableError(
                f"Provider '{route.provider_name}' is not registered "
                f"in the dispatcher (adapter pending or removed).",
                details={"provider": route.provider_name},
            )
        return await adapter.chat(
            payload=payload,
            messages=messages,
            resolved_model=route.model_name,
            api_key=api_key,
        )

    async def dispatch_with_fallback(
        self,
        route,
        payload,
        messages: list[Message],
        *,
        api_key: str | None = None,
        key_resolver: Callable[[str], str | None] | None = None,
        on_auth_failure: Callable[[str, str], str | None] | None = None,
        max_hops: int = _MAX_LAYER1_HOPS,
    ) -> tuple[ProviderResponse, str, str]:
        """Dispatch the primary route with up to ``max_hops`` Layer-1
        fallback hops on a fallback-eligible error.

        Tries ``route.provider_name`` / ``route.model_name`` first using
        ``api_key``. On a fallback-eligible failure, walks
        ``route.fallbacks`` (each entry is ``(provider, model)``) in
        order, resolving the per-provider API key via ``key_resolver``
        (typically ``provider_key_service.get_active_key``). Candidates
        without a registered adapter or without an API key are skipped
        and do NOT consume one of the ``max_hops`` slots — same rule
        the Layer-2 fallback orchestrator uses.

        Returns ``(response, used_provider, used_model)`` so the caller
        can rewrite the route record for downstream logging.

        Raises the last fallback-eligible error if every candidate
        fails. NON-eligible errors (4xx auth, invalid request, etc.)
        propagate immediately — no retry.

        ``on_auth_failure`` (B3): invoked as ``on_auth_failure(provider,
        failed_key)`` when a candidate's call returns 401/403. The caller
        uses it to quarantine the bad key (mark ``invalid``) and return the
        NEXT active key for that same provider, or ``None`` if no other key
        exists. When it returns a fresh key, the SAME provider/model is
        retried with it before walking to cross-provider fallbacks — so a
        single bad key in a multi-key pool self-heals within the request
        instead of surfacing an empty response.
        """
        # Local import to avoid a circular import at module load
        # (fallback_orchestrator → routing types → dispatcher).
        from app.services.gateway.fallback_orchestrator import (
            is_fallback_eligible,
        )

        attempts: list[tuple[str, str]] = [
            (route.provider_name, route.model_name)
        ] + [
            (p, m) for (p, m) in (getattr(route, "fallbacks", None) or [])
        ]

        last_error: Exception | None = None
        hop_index = 0  # 0 = primary, 1.. = Layer-1 hops

        for attempt_pos, (provider, model) in enumerate(attempts):
            if hop_index > max_hops:
                break

            adapter = self.adapters.get(provider)
            if adapter is None:
                # Skip and don't consume a hop — same operator-friendly
                # rule the Layer-2 chain uses for missing-adapter cases.
                logger.info(
                    "dispatch_candidate_skipped",
                    candidate_provider=provider,
                    candidate_model=model,
                    reason="adapter_not_registered",
                )
                continue

            # Key resolution: the primary attempt uses the caller-passed
            # ``api_key``; fallback hops resolve via ``key_resolver``
            # because the orchestrator only resolves the primary key
            # before calling this method.
            if attempt_pos == 0:
                attempt_key = api_key
            else:
                attempt_key = (
                    key_resolver(provider) if key_resolver else None
                )
                if not attempt_key:
                    logger.info(
                        "dispatch_candidate_skipped",
                        candidate_provider=provider,
                        candidate_model=model,
                        reason="no_api_key",
                    )
                    continue

            # Per-hop route. Built fresh each iteration so the primary's
            # ``fallbacks`` list isn't carried forward (the dispatcher's
            # own retry logic owns hop progression). Delegating through
            # ``self.dispatch`` (rather than calling ``adapter.chat``
            # inline) keeps every spy / monkeypatch on ``dispatch`` —
            # of which the integration tests have several — intact.
            hop_route = type(route)(provider_name=provider, model_name=model)
            # Keys already tried for THIS provider — guards the auth-retry
            # loop against re-trying a rotated-back key (round-robin can
            # return a key we just quarantined if the cache is warm).
            tried_keys: set[str] = {attempt_key} if attempt_key else set()
            advance_hop = False
            while True:
                t0 = time.perf_counter()
                try:
                    response = await self.dispatch(
                        route=hop_route,
                        payload=payload,
                        messages=messages,
                        api_key=attempt_key,
                    )
                    if hop_index > 0:
                        logger.info(
                            "dispatch_fallback_succeeded",
                            provider=provider,
                            model=model,
                            hop=hop_index,
                            latency_ms=int((time.perf_counter() - t0) * 1000),
                        )
                    return response, provider, model
                except _FALLBACK_ERRORS as exc:
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    last_error = exc
                    # B3 — auth failure: quarantine the bad key and, if the
                    # pool has another key for this provider, retry the same
                    # provider/model with it before falling back elsewhere.
                    if (
                        _provider_status(exc) in (401, 403)
                        and on_auth_failure is not None
                        and attempt_key
                    ):
                        next_key = on_auth_failure(provider, attempt_key)
                        if next_key and next_key not in tried_keys:
                            tried_keys.add(next_key)
                            attempt_key = next_key
                            logger.warning(
                                "dispatch_auth_retry_next_key",
                                provider=provider,
                                model=model,
                                hop=hop_index,
                            )
                            continue
                    eligible = (
                        isinstance(exc, GatewayError) and is_fallback_eligible(exc)
                    )
                    logger.warning(
                        "dispatch_attempt_failed",
                        provider=provider,
                        model=model,
                        hop=hop_index,
                        latency_ms=latency_ms,
                        error_code=getattr(exc, "code", None),
                        error=str(exc)[:200],
                        eligible_for_fallback=eligible,
                    )
                    if not eligible:
                        # Non-transient (bad request / etc.) — re-raise
                        # immediately; retrying would just burn quota on the
                        # next provider with the same bad input. The caller
                        # (orchestrator) can still hand off to Layer 2 if it
                        # wants a tier-based rescue.
                        raise
                    advance_hop = True
                    break
            if advance_hop:
                hop_index += 1
                continue

        if last_error is not None:
            raise last_error
        raise ProviderError("No provider candidates available.")
