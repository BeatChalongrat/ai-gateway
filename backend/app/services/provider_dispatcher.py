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

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        """True when ``exc`` is a provider auth rejection (401/403).

        The adapter error mapper turns a 401/403 into a ``ProviderError``
        with ``details['status']`` set, so the dispatcher can tell an auth
        failure (rotate the key) apart from a generic transient error
        (just fall back).
        """
        if not isinstance(exc, ProviderError):
            return False
        status = (getattr(exc, "details", None) or {}).get("status")
        return status in (401, 403)

    async def _dispatch_one(
        self,
        route,
        provider: str,
        model: str,
        payload,
        messages: list[Message],
        first_key: str | None,
        key_resolver: Callable[[str], str | None] | None,
        invalidate_key: Callable[[str, str], None] | None,
    ) -> ProviderResponse:
        """Dispatch to a single (provider, model), rotating keys on 401/403.

        Group A / A2 — auto-invalidate on 401 + retry next key. When the
        provider rejects the credential, ``invalidate_key`` marks that row
        invalid and we re-resolve via ``key_resolver`` to get the next key
        in the pool, retrying within the same request. Bounded by the
        number of DISTINCT keys (a re-resolve that returns an already-tried
        or empty key ends the loop), so an all-401 pool can't spin.
        Non-auth errors propagate unchanged for the fallback chain.
        """
        tried: set[str] = set()
        key = first_key
        while True:
            hop_route = type(route)(provider_name=provider, model_name=model)
            try:
                return await self.dispatch(
                    route=hop_route,
                    payload=payload,
                    messages=messages,
                    api_key=key,
                )
            except Exception as exc:  # noqa: BLE001
                if not (
                    self._is_auth_error(exc)
                    and invalidate_key is not None
                    and key is not None
                ):
                    raise
                tried.add(key)
                invalidate_key(provider, key)
                next_key = key_resolver(provider) if key_resolver else None
                if not next_key or next_key in tried:
                    logger.warning(
                        "dispatch_auth_pool_exhausted",
                        provider=provider,
                        model=model,
                        keys_tried=len(tried),
                    )
                    raise
                logger.warning(
                    "dispatch_auth_key_rotated",
                    provider=provider,
                    model=model,
                    keys_tried=len(tried),
                )
                key = next_key

    async def dispatch_with_fallback(
        self,
        route,
        payload,
        messages: list[Message],
        *,
        api_key: str | None = None,
        key_resolver: Callable[[str], str | None] | None = None,
        invalidate_key: Callable[[str, str], None] | None = None,
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

            t0 = time.perf_counter()
            # Per-hop dispatch with same-provider key rotation on 401/403
            # (A2). ``_dispatch_one`` delegates through ``self.dispatch``
            # (rather than calling ``adapter.chat`` inline) so every spy /
            # monkeypatch on ``dispatch`` — of which the integration tests
            # have several — stays intact. A fresh per-hop route is built
            # inside so the primary's ``fallbacks`` list isn't carried
            # forward (the dispatcher's own retry logic owns hop progression).
            try:
                response = await self._dispatch_one(
                    route=route,
                    provider=provider,
                    model=model,
                    payload=payload,
                    messages=messages,
                    first_key=attempt_key,
                    key_resolver=key_resolver,
                    invalidate_key=invalidate_key,
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
                    # Non-transient (auth / bad request / etc.) —
                    # re-raise immediately; retrying would just burn
                    # quota on the next provider with the same bad
                    # input. The caller (orchestrator) can still hand
                    # off to Layer 2 if it wants a tier-based rescue.
                    raise
                hop_index += 1
                continue

        if last_error is not None:
            raise last_error
        raise ProviderError("No provider candidates available.")
