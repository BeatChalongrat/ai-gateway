"""Provider dispatcher with fallback support.

Dispatches to the primary adapter. If it fails with a retryable provider
error, tries fallback candidates from the routing result.
"""

from __future__ import annotations

import structlog
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from app.schemas.common import Message

logger = structlog.get_logger(__name__)

# Errors that trigger fallback (subclasses of ProviderError)
_FALLBACK_ERRORS = (ProviderError,)


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
        }

    async def dispatch(self, route, payload, messages: list[Message], *, api_key: str | None = None) -> ProviderResponse:
        """Dispatch to primary adapter using route.provider_name / route.model_name."""
        adapter = self.adapters[route.provider_name]
        return await adapter.chat(
            payload=payload,
            messages=messages,
            resolved_model=route.model_name,
            api_key=api_key,
        )

    async def dispatch_with_fallback(
        self,
        routing_result: RoutingResult,
        payload,
        messages: list[Message],
    ) -> tuple[ProviderResponse, ModelCandidate]:
        """Dispatch with fallback on provider failure.

        Tries the selected candidate first, then each fallback in order.
        Returns (response, candidate_that_succeeded).

        Raises the last provider error if all candidates fail.
        """
        candidates = [routing_result.selected] + list(routing_result.fallbacks)
        last_error: Exception | None = None

        for candidate in candidates:
            adapter = self.adapters.get(candidate.provider)
            if adapter is None:
                continue

            try:
                response = await adapter.chat(
                    payload=payload,
                    messages=messages,
                    resolved_model=candidate.model,
                )
                return response, candidate
            except _FALLBACK_ERRORS as exc:
                last_error = exc
                logger.warning(
                    "provider_fallback",
                    failed_provider=candidate.provider,
                    failed_model=candidate.model,
                    error=str(exc),
                    remaining_fallbacks=len(candidates) - candidates.index(candidate) - 1,
                )
                continue

        # All candidates failed
        if last_error is not None:
            raise last_error
        raise ProviderError("No provider candidates available.")
