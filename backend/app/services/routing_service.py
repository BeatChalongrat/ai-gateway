"""Routing engine — centralized model/provider selection.

Consumes a RoutingRequest and produces a RoutingResult with ranked
candidates.  All "auto" resolution happens here — adapters should
never see "auto" as the model name.
"""

from __future__ import annotations

from app.domain.entities import RequestContext, RouteDecision
from app.domain.routing import (
    ModelCandidate,
    RejectedCandidate,
    RoutingRequest,
    RoutingResult,
)
from app.services.providers.model_registry import (
    filter_candidates,
    get_all_candidates,
    get_candidate,
    resolve_provider,
)

_COST_SCORE = {"budget": 1, "standard": 2, "premium": 3}
_LATENCY_SCORE = {"fast": 1, "standard": 2, "slow": 3}


def _score_candidate(c: ModelCandidate, prefer_cost: bool, prefer_latency: bool) -> int:
    """Lower score = better candidate."""
    score = 0
    if prefer_cost:
        score += _COST_SCORE.get(c.cost_tier, 2) * 2
    if prefer_latency:
        score += _LATENCY_SCORE.get(c.latency_tier, 2) * 2
    # Default: prefer standard
    score += _COST_SCORE.get(c.cost_tier, 2)
    score += _LATENCY_SCORE.get(c.latency_tier, 2)
    return score

def _build_auto_defaults() -> dict[str, ModelCandidate]:
    """Build auto-routing defaults from the registry when possible."""
    general = get_candidate("gpt-4o-mini") or ModelCandidate(
        provider="openai", model="gpt-4o-mini", cost_tier="budget", latency_tier="fast"
    )
    coding = get_candidate("claude-3-5-sonnet-20241022") or ModelCandidate(
        provider="anthropic", model="claude-3-5-sonnet-20241022", cost_tier="standard"
    )
    return {"general": general, "coding": coding, "reasoning": coding}


_AUTO_DEFAULTS = _build_auto_defaults()
_AUTO_FALLBACK = _AUTO_DEFAULTS["general"]


class RoutingService:

    def route(self, request: RoutingRequest) -> RoutingResult:
        """Route a request to a provider/model using deterministic rules.

        For explicit model requests, resolves provider via model registry.
        For "auto" requests, selects based on use_case.
        """
        if request.requested_model != "auto":
            return self._route_explicit(request)
        return self._route_auto(request)

    def _route_explicit(self, request: RoutingRequest) -> RoutingResult:
        provider = resolve_provider(request.requested_model)
        if provider is None:
            provider = "openai"
            reason = (
                f"Unknown model '{request.requested_model}' — "
                f"falling back to provider '{provider}'."
            )
        else:
            reason = f"Explicit model '{request.requested_model}' resolved to provider '{provider}'."

        # Check provider against allowed_providers constraint (from region/policy)
        if request.allowed_providers and provider not in request.allowed_providers:
            reason += f" BLOCKED by region/policy (provider '{provider}' not allowed)."
            # Fall back to first allowed provider
            fallback_provider = next(iter(request.allowed_providers), "openai")
            candidate = ModelCandidate(
                provider=fallback_provider, model=request.requested_model
            )
            return RoutingResult(
                selected=candidate,
                rejected=[RejectedCandidate(
                    candidate=ModelCandidate(provider=provider, model=request.requested_model),
                    reason=f"Provider '{provider}' blocked by region/policy constraint",
                )],
                reasons=[reason],
                constrained_by_policy=True,
            )

        candidate = ModelCandidate(
            provider=provider, model=request.requested_model
        )
        return RoutingResult(
            selected=candidate,
            reasons=[reason],
        )

    def _route_auto(self, request: RoutingRequest) -> RoutingResult:
        preferred = _AUTO_DEFAULTS.get(request.use_case, _AUTO_FALLBACK)
        rejected: list[RejectedCandidate] = []

        # Check if preferred candidate passes filters
        filtered = filter_candidates(
            [preferred],
            requires_streaming=request.stream,
            allowed_providers=request.allowed_providers,
            blocked_models=request.blocked_models,
        )

        if filtered:
            reason = (
                f"Auto-routing for use_case='{request.use_case}' selected "
                f"'{preferred.model}' on '{preferred.provider}'."
            )
            # Build fallback list from other candidates
            all_candidates = get_all_candidates()
            fallbacks = filter_candidates(
                [c for c in all_candidates if c != preferred],
                requires_streaming=request.stream,
                allowed_providers=request.allowed_providers,
                blocked_models=request.blocked_models,
            )
            fallbacks.sort(key=lambda c: _score_candidate(c, request.prefer_cost, request.prefer_latency))
            return RoutingResult(
                selected=preferred,
                fallbacks=fallbacks[:2],
                reasons=[reason],
                constrained_by_policy=request.allowed_providers is not None,
            )

        # Preferred was filtered out — find best alternative
        rejected.append(RejectedCandidate(
            candidate=preferred,
            reason="filtered by capability or policy constraints",
        ))

        all_candidates = get_all_candidates()
        alternatives = filter_candidates(
            all_candidates,
            requires_streaming=request.stream,
            allowed_providers=request.allowed_providers,
            blocked_models=request.blocked_models,
        )

        alternatives.sort(key=lambda c: _score_candidate(c, request.prefer_cost, request.prefer_latency))
        if alternatives:
            selected = alternatives[0]
            return RoutingResult(
                selected=selected,
                fallbacks=alternatives[1:3],
                rejected=rejected,
                reasons=[
                    f"Auto-routing preferred '{preferred.model}' was filtered out. "
                    f"Selected '{selected.model}' on '{selected.provider}' instead."
                ],
                constrained_by_policy=True,
            )

        # Nothing available — return preferred anyway with warning
        return RoutingResult(
            selected=preferred,
            rejected=rejected,
            reasons=[
                f"No candidates matched filters. Falling back to '{preferred.model}'."
            ],
        )

    # Legacy bridge — called by orchestrator until fully migrated
    def select_route(
        self, ctx: RequestContext, payload, policy_decision
    ) -> RouteDecision:
        """Bridge method: translates legacy call to new routing engine.

        Extracts routing constraints from the project/policy context.
        """
        from app.services.region_service import get_blocked_providers_for_region

        # Extract project-level routing constraints from metadata
        allowed_providers = None
        blocked_models = None
        preferred_provider = None

        if payload.metadata.get("allowed_providers"):
            allowed_providers = frozenset(payload.metadata["allowed_providers"])
        if payload.metadata.get("blocked_models"):
            blocked_models = frozenset(payload.metadata["blocked_models"])
        if payload.metadata.get("preferred_provider"):
            preferred_provider = payload.metadata["preferred_provider"]

        # Merge region-based provider blocks
        region = getattr(ctx, "region", "global")
        region_blocked = get_blocked_providers_for_region(region)
        if region_blocked:
            existing = blocked_models or frozenset()
            # Region blocks providers, not models — we filter in RoutingRequest
            # by merging into allowed_providers constraint
            if allowed_providers:
                allowed_providers = allowed_providers - region_blocked
            else:
                # Convert blocked providers to an explicit allowed set
                all_known = {"openai", "anthropic", "openrouter"}
                allowed_providers = frozenset(all_known - region_blocked)

        request = RoutingRequest(
            requested_model=payload.model,
            stream=getattr(payload, "stream", False),
            use_case=payload.metadata.get("use_case", "general"),
            allowed_providers=allowed_providers,
            blocked_models=blocked_models,
            preferred_provider=preferred_provider,
        )
        result = self.route(request)
        return RouteDecision(
            provider_name=result.provider,
            model_name=result.model,
            reasons=result.reasons,
            constrained_by_policy=result.constrained_by_policy,
        )
