"""Usage and cost accounting service."""

from __future__ import annotations

from app.domain.observability import UsageRecord
from app.domain.providers import ProviderResponse
from app.schemas.common import UsageInfo

# Simple cost-per-token estimates by model prefix (USD per token)
_COST_PER_TOKEN: dict[str, float] = {
    "gpt-4o-mini": 0.00000015,
    "gpt-4o": 0.0000025,
    "gpt-4-turbo": 0.00001,
    "gpt-3.5-turbo": 0.0000005,
    "claude-3-5-sonnet": 0.000003,
    "claude-3-opus": 0.000015,
    "claude-3-haiku": 0.00000025,
}


def _estimate_cost(model: str, total_tokens: int) -> float:
    for prefix, cost in _COST_PER_TOKEN.items():
        if model.startswith(prefix):
            return round(total_tokens * cost, 8)
    return round(total_tokens * 0.000001, 8)


class UsageService:

    def estimate(self, payload, response_text: str, model_name: str) -> UsageInfo:
        """Heuristic token estimation when provider doesn't report usage."""
        prompt_tokens = sum(max(1, len(m.content) // 4) for m in payload.messages)
        completion_tokens = max(1, len(response_text) // 4)
        total_tokens = prompt_tokens + completion_tokens
        return UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=_estimate_cost(model_name, total_tokens),
        )

    def build_usage_record(
        self,
        *,
        request_id: str,
        tenant_id: str,
        project_id: str,
        provider: str,
        model: str,
        provider_response: ProviderResponse | None = None,
        usage_info: UsageInfo | None = None,
    ) -> UsageRecord:
        """Build a UsageRecord from provider response or fallback UsageInfo."""
        if provider_response and provider_response.usage:
            u = provider_response.usage
            return UsageRecord(
                request_id=request_id,
                tenant_id=tenant_id,
                project_id=project_id,
                provider=provider,
                model=model,
                prompt_tokens=u.prompt_tokens,
                completion_tokens=u.completion_tokens,
                total_tokens=u.total_tokens,
                estimated_cost_usd=_estimate_cost(model, u.total_tokens),
                is_estimate=False,
            )

        if usage_info:
            return UsageRecord(
                request_id=request_id,
                tenant_id=tenant_id,
                project_id=project_id,
                provider=provider,
                model=model,
                prompt_tokens=usage_info.prompt_tokens,
                completion_tokens=usage_info.completion_tokens,
                total_tokens=usage_info.total_tokens,
                estimated_cost_usd=usage_info.estimated_cost_usd,
                is_estimate=True,
            )

        return UsageRecord(
            request_id=request_id,
            tenant_id=tenant_id,
            project_id=project_id,
            provider=provider,
            model=model,
            is_estimate=True,
        )
