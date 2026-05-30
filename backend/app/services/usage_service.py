"""Usage and cost accounting service."""

from __future__ import annotations

from app.domain.observability import UsageRecord
from app.domain.providers import ProviderResponse
from app.schemas.common import UsageInfo

# Legacy fallback table — flat $/token by model prefix. Consulted only
# when ``model_registry.get_candidate`` returns no ``cost_per_input_token``
# for the requested model. Kept intentionally so that requests for
# in-flight or yet-unregistered models still get a sensible bill.
_COST_PER_TOKEN: dict[str, float] = {
    "gpt-4o-mini": 0.00000015,
    "gpt-4o": 0.0000025,
    "gpt-4-turbo": 0.00001,
    "gpt-3.5-turbo": 0.0000005,
    "claude-3-5-sonnet": 0.000003,
    "claude-3-opus": 0.000015,
    "claude-3-haiku": 0.00000025,
}

_LEGACY_DEFAULT_RATE = 0.000001


def _legacy_flat_rate(model: str) -> float:
    """Pick the per-token rate for ``model`` from the legacy prefix table.

    Falls back to ``_LEGACY_DEFAULT_RATE`` for any model that isn't a
    prefix match. Returned in $/token (same as ``_COST_PER_TOKEN``).
    """
    for prefix, cost in _COST_PER_TOKEN.items():
        if model.startswith(prefix):
            return cost
    return _LEGACY_DEFAULT_RATE


def _estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Estimate request cost in USD.

    Uses per-token rates from ``model_registry.get_candidate`` when the
    candidate has at least one non-zero rate (input or output). Falls
    back to the legacy flat rate table for unknown models — multiplied
    by total tokens so existing prefix entries keep their historical
    numbers within float precision.
    """
    from app.services.providers.model_registry import get_candidate

    candidate = get_candidate(model)
    if candidate and (
        candidate.cost_per_input_token > 0.0
        or candidate.cost_per_output_token > 0.0
    ):
        return round(
            prompt_tokens * candidate.cost_per_input_token
            + completion_tokens * candidate.cost_per_output_token,
            8,
        )

    rate = _legacy_flat_rate(model)
    return round((prompt_tokens + completion_tokens) * rate, 8)


def _estimate_reasoning_cost(model: str, reasoning_tokens: int) -> float:
    """USD already-included-in-total cost attributable to reasoning (T3).

    NON-ADDITIVE attribution. Reasoning tokens bill at the model's
    *output* rate on every provider we route to — OpenAI o-series,
    Anthropic extended thinking, Gemini 2.5+ all fold reasoning into
    the provider's ``completion_tokens`` meter. ``estimated_cost_usd``
    is therefore the authoritative ALL-IN total (it already prices the
    reasoning tokens via the full ``completion_tokens`` count); this
    helper returns the *portion* of that total attributable to
    reasoning, so the admin dashboard can break spend down.

    CRITICAL: never add ``reasoning_cost`` on top of
    ``estimated_cost_usd`` — that double-counts. It is a sub-component,
    not an extra charge. Budget/rollup queries must sum
    ``estimated_cost_usd`` ALONE (see budget_service). To get the
    visible-only completion cost, subtract this from the completion
    cost. The caller guarantees ``reasoning_tokens <= completion_tokens``
    (capped at the estimate sites) so this attribution can never exceed
    the completion cost it is a part of.

    Falls back to the legacy flat rate when the candidate has no
    output rate set — same posture as ``_estimate_cost``.
    """
    if reasoning_tokens <= 0:
        return 0.0
    from app.services.providers.model_registry import get_candidate

    candidate = get_candidate(model)
    if candidate and candidate.cost_per_output_token > 0.0:
        return round(reasoning_tokens * candidate.cost_per_output_token, 8)

    rate = _legacy_flat_rate(model)
    return round(reasoning_tokens * rate, 8)


class UsageService:

    def estimate(self, payload, response_text: str, model_name: str) -> UsageInfo:
        """Heuristic token estimation when provider doesn't report usage."""
        from app.services.currency_service import currency_service

        prompt_tokens = sum(max(1, len(m.content) // 4) for m in payload.messages)
        completion_tokens = max(1, len(response_text) // 4)
        total_tokens = prompt_tokens + completion_tokens
        usd = _estimate_cost(model_name, prompt_tokens, completion_tokens)
        cost_info = currency_service.format_cost(usd)
        return UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=usd,
            cost_thb=cost_info["thb"],
            cost_display=cost_info["display"],
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
                estimated_cost_usd=_estimate_cost(
                    model, u.prompt_tokens, u.completion_tokens
                ),
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
