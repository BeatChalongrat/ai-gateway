"""Routing engine — centralized model/provider selection.

Consumes a RoutingRequest and produces a RoutingResult with ranked
candidates.  All "auto" resolution happens here — adapters should
never see "auto" as the model name.
"""

from __future__ import annotations

import structlog

from app.core.exceptions import NoAllowedProviderError
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
    get_all_providers,
    get_candidate,
    resolve_provider,
)

logger = structlog.get_logger(__name__)

# Tier-fallback scores (used when the candidate has no real $/token).
_COST_SCORE = {"budget": 1, "standard": 2, "premium": 3}
_LATENCY_SCORE = {"fast": 1, "standard": 2, "slow": 3}

# Soft bias applied to a candidate's score when it lives on the
# request's preferred_provider. Lower score = better, so subtracting
# pulls preferred candidates to the front of the sort. Sized larger
# than the max possible cost+latency contribution so the preference
# always wins ties within the registry but stays a *soft* preference:
# when no candidate from the preferred provider survives filtering,
# the next-best alternative is still returned normally.
_PREFERRED_PROVIDER_BIAS = 100

# When a candidate has no real $/token (cost_per_input_token == 0.0)
# *and* no derived cost_tier match, use this neutral penalty — same
# numeric weight as the "standard" tier so unknown models neither
# win nor lose against the average bucket.
_COST_UNKNOWN_PENALTY = 2

# Scale factor applied to ``cost_per_input_token`` ($/token) when
# converting to the integer score space shared with the tier buckets.
# Sized so that:
#   gpt-4o-mini  (0.00000015 $/tok) → 1     (≈ budget tier)
#   gpt-4o       (0.0000025  $/tok) → 25    (clearly above budget)
#   claude-opus  (0.000015   $/tok) → 150   (clearly premium)
# i.e. real-cost candidates spread across a range that dwarfs the
# 1..3 tier bucket but stays well below the preferred-provider bias
# of 100 for moderately-priced models so preference still wins ties.
_COST_REAL_SCALE = 10_000_000


def _score_candidate(
    c: ModelCandidate,
    prefer_cost: bool,
    prefer_latency: bool,
    preferred_provider: str | None = None,
) -> int:
    """Lower score = better candidate.

    Cost component prefers real per-token pricing
    (``cost_per_input_token``) when available, falling back to the
    coarse ``cost_tier`` bucket for models with unknown pricing
    (``cost_per_input_token == 0.0``).
    """
    score = 0

    # --- Cost component ---
    if c.cost_per_input_token > 0.0:
        # Real pricing available. Input cost only — output token count
        # varies by response length and is unknown at routing time.
        cost_score = int(c.cost_per_input_token * _COST_REAL_SCALE)
    else:
        # Unknown pricing: fall back to the tier bucket.
        cost_score = _COST_SCORE.get(c.cost_tier, _COST_UNKNOWN_PENALTY)

    if prefer_cost:
        score += cost_score * 2
    score += cost_score

    # --- Latency component ---
    latency_score = _LATENCY_SCORE.get(c.latency_tier, 2)
    if prefer_latency:
        score += latency_score * 2
    score += latency_score

    # --- Preferred provider soft bias ---
    if preferred_provider and c.provider == preferred_provider:
        score -= _PREFERRED_PROVIDER_BIAS

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

    def _find_compatible_provider(
        self,
        model_name: str,
        allowed_providers: frozenset[str],
        preferred_provider: str | None = None,
    ) -> tuple[str, str]:
        """Pick a provider from ``allowed_providers`` that owns ``model_name``.

        Replaces the legacy ``next(iter(allowed_providers), 'openai')``
        fallback, which was both non-deterministic (frozenset iteration
        order varies across Python runs) and capability-blind (would
        happily route ``claude-3-5-sonnet`` to openai → 404 at the
        adapter). The new contract:

        1. If ``preferred_provider`` is in the allowed set *and* owns
           the model per the registry, it wins immediately.
        2. Otherwise iterate ``sorted(allowed_providers)`` (lexical,
           deterministic) and return the first provider whose registry
           entry matches ``model_name``.
        3. If no provider in the allowed set owns the model, log a
           ``routing_no_compatible_provider`` warning and raise
           :class:`NoAllowedProviderError` (FIX126 O4, fail-closed). It
           used to fall back to ``"openai"`` — a provider the caller had
           excluded — so the request body left for it anyway.

        Returns ``(provider, reason)`` so callers can fold the
        explanation into the ``RoutingResult.reasons`` trace.
        """
        canonical = resolve_provider(model_name)

        if (
            preferred_provider
            and preferred_provider in allowed_providers
            and canonical == preferred_provider
        ):
            return preferred_provider, (
                f"preferred_provider '{preferred_provider}' is "
                f"compatible with '{model_name}' — selected as swap target."
            )

        for provider in sorted(allowed_providers):
            if canonical == provider:
                return provider, (
                    f"Selected '{provider}' from sorted(allowed_providers) "
                    f"as the compatible owner of '{model_name}'."
                )

        logger.warning(
            "routing_no_compatible_provider",
            model=model_name,
            allowed=sorted(allowed_providers),
            preferred=preferred_provider,
            canonical=canonical,
            action="rejected",
        )
        raise NoAllowedProviderError(
            f"No provider in allowed set {sorted(allowed_providers)} "
            f"owns model '{model_name}'.",
            details={
                "model": model_name,
                "allowed_providers": sorted(allowed_providers),
                "model_provider": canonical,
            },
        )

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
            # Deterministic, model-aware swap: prefer the project's
            # preferred_provider if it owns the model; otherwise pick
            # the lexically-first allowed provider that owns it; only
            # if none match do we fall back to 'openai' with a loud
            # warning (the legacy ``next(iter(frozenset))`` behaviour
            # was both non-deterministic and capability-blind).
            fallback_provider, fallback_reason = self._find_compatible_provider(
                request.requested_model,
                request.allowed_providers,
                request.preferred_provider,
            )
            candidate = ModelCandidate(
                provider=fallback_provider, model=request.requested_model
            )
            return RoutingResult(
                selected=candidate,
                rejected=[RejectedCandidate(
                    candidate=ModelCandidate(provider=provider, model=request.requested_model),
                    reason=f"Provider '{provider}' blocked by region/policy constraint",
                )],
                reasons=[reason, fallback_reason],
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
        preferred_provider_reason: str | None = None

        # use_case='reasoning' (T1): restrict the candidate pool to
        # ``supports_reasoning=True`` rows. ``_AUTO_DEFAULTS["reasoning"]``
        # is a back-compat alias for the coding default which usually
        # is NOT reasoning-capable — so we re-elect ``preferred`` from
        # the reasoning-capable pool here. If the pool is empty (no
        # reasoning models registered, or all blocked by region /
        # policy), fall through to the existing non-reasoning behaviour
        # rather than 503'ing the request.
        if request.use_case == "reasoning":
            reasoning_pool = [
                c for c in get_all_candidates() if c.supports_reasoning
            ]
            reasoning_pool = filter_candidates(
                reasoning_pool,
                requires_streaming=request.stream,
                allowed_providers=request.allowed_providers,
                blocked_models=request.blocked_models,
                requires_vision=request.requires_vision,
                requires_tools=request.requires_tools,
            )
            # Prefer reasoning models on a provider that actually has a
            # callable key (P0). Without this the pool's cost sort can elect
            # a cheap reasoning model whose provider has no key (e.g. Groq
            # R1-distill) as the primary, and the request dead-ends on the
            # provider-key check with no fallback. Soft filter: only applied
            # when it leaves a non-empty pool, so deployments that never
            # validate keys (empty ``configured_providers``) keep best-effort
            # behaviour and the downgrade signal still fires if nothing fits.
            if reasoning_pool and request.configured_providers:
                keyed = [
                    c for c in reasoning_pool
                    if c.provider in request.configured_providers
                ]
                if keyed:
                    reasoning_pool = keyed
            if reasoning_pool:
                reasoning_pool.sort(
                    key=lambda c: _score_candidate(
                        c,
                        request.prefer_cost,
                        request.prefer_latency,
                        preferred_provider=request.preferred_provider,
                    )
                )
                preferred = reasoning_pool[0]

        # Soft preference: when the project has set ``preferred_provider``
        # and the registry has at least one candidate on that provider
        # that survives capability/policy filtering, override the auto
        # default. Falls back silently to the hardcoded default when no
        # such candidate exists — this is preference, not constraint.
        if request.preferred_provider:
            preferred_pool = [
                c for c in get_all_candidates()
                if c.provider == request.preferred_provider
            ]
            preferred_pool = filter_candidates(
                preferred_pool,
                requires_streaming=request.stream,
                allowed_providers=request.allowed_providers,
                blocked_models=request.blocked_models,
                requires_vision=request.requires_vision,
                requires_tools=request.requires_tools,
            )
            if preferred_pool:
                preferred_pool.sort(
                    key=lambda c: _score_candidate(
                        c,
                        request.prefer_cost,
                        request.prefer_latency,
                        preferred_provider=request.preferred_provider,
                    )
                )
                preferred = preferred_pool[0]
                preferred_provider_reason = (
                    f"preferred_provider='{request.preferred_provider}' "
                    f"override selected '{preferred.model}'."
                )

        # Check if preferred candidate passes filters
        filtered = filter_candidates(
            [preferred],
            requires_streaming=request.stream,
            allowed_providers=request.allowed_providers,
            blocked_models=request.blocked_models,
            requires_vision=request.requires_vision,
            requires_tools=request.requires_tools,
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
                requires_vision=request.requires_vision,
                requires_tools=request.requires_tools,
            )
            fallbacks.sort(
                key=lambda c: _score_candidate(
                    c,
                    request.prefer_cost,
                    request.prefer_latency,
                    preferred_provider=request.preferred_provider,
                )
            )
            reasons = [reason]
            if preferred_provider_reason:
                reasons.append(preferred_provider_reason)
            return RoutingResult(
                selected=preferred,
                fallbacks=fallbacks[:2],
                reasons=reasons,
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
            requires_vision=request.requires_vision,
            requires_tools=request.requires_tools,
        )

        alternatives.sort(
            key=lambda c: _score_candidate(
                c,
                request.prefer_cost,
                request.prefer_latency,
                preferred_provider=request.preferred_provider,
            )
        )
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

        # FIX126 O4 — nothing survived AND the preferred default lies
        # outside a non-empty allow-list: returning it would send the
        # request to a provider the caller excluded. Refuse instead.
        if request.allowed_providers is not None and (
            preferred.provider not in request.allowed_providers
        ):
            logger.warning(
                "routing_no_compatible_provider",
                model=preferred.model,
                allowed=sorted(request.allowed_providers),
                use_case=request.use_case,
                action="rejected",
            )
            raise NoAllowedProviderError(
                f"No model on the allowed providers "
                f"{sorted(request.allowed_providers)} can serve this request.",
                details={
                    "model": request.requested_model,
                    "allowed_providers": sorted(request.allowed_providers),
                },
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
                # Dynamic allowlist: every provider with an active model
                # in the registry (i.e. every adapter wired into
                # ``provider_dispatcher.py``) minus the region-blocked
                # set. Replaces the legacy hardcoded
                # ``{"openai", "anthropic", "openrouter"}`` which silently
                # excluded Google / Groq / xAI / DeepSeek / Qwen / Kimi /
                # Z.AI / Minimax from region-constrained auto-routing
                # (May 2026 routing audit).
                all_known = get_all_providers()
                allowed_providers = all_known - region_blocked

        # Hard capabilities the request needs (vision/tools). Threaded
        # into routing so neither the auto-selected primary nor its
        # fallbacks can land on a model that would silently drop the
        # image / tool call. Reasoning is best-effort and excluded.
        from app.services.capability_detection import detect_required_capabilities

        caps = detect_required_capabilities(payload)

        # Reasoning-aware key filter (P0). Populated by the orchestrator's
        # router chain only when reasoning is requested; absent otherwise so
        # general / mode=off routing is byte-for-byte unchanged.
        _cfg_providers = payload.metadata.get("configured_providers")
        configured_providers = (
            frozenset(_cfg_providers) if _cfg_providers else None
        )

        request = RoutingRequest(
            requested_model=payload.model,
            stream=getattr(payload, "stream", False),
            use_case=payload.metadata.get("use_case", "general"),
            allowed_providers=allowed_providers,
            blocked_models=blocked_models,
            preferred_provider=preferred_provider,
            configured_providers=configured_providers,
            requires_vision=caps.vision,
            requires_tools=caps.tools,
        )
        result = self.route(request)
        return RouteDecision(
            provider_name=result.provider,
            model_name=result.model,
            reasons=result.reasons,
            constrained_by_policy=result.constrained_by_policy,
            # Layer-1 fallbacks for ``dispatch_with_fallback``. RoutingResult
            # already caps these at 2 entries (see ``_route_auto``); the
            # dispatcher applies its own ``max_hops`` cap as a belt-and-braces.
            fallbacks=[(c.provider, c.model) for c in result.fallbacks],
        )


async def effective_provider_boundary(payload, tenant_id: str | None) -> frozenset[str] | None:
    """FIX126 O4 — the providers this request may reach at all: the
    request's ``metadata.allowed_providers`` (the project's, injected into
    metadata) intersected with the tenant override's ``allowed_providers``
    (FIX120 fallback policy). ``None`` = no boundary (unchanged behaviour).
    A tenant policy that cannot be read counts as "no tenant boundary" —
    the request's own list still applies."""
    from app.services.gateway import fallback_policy as _fb_policy_mod

    meta = getattr(payload, "metadata", None)
    request_allowed = meta.get("allowed_providers") if isinstance(meta, dict) else None
    try:
        policy = await _fb_policy_mod.effective_policy_async(tenant_id)
    except Exception as exc:  # noqa: BLE001 — an unreadable policy never opens the request's own list
        logger.warning("provider_boundary_policy_unreadable", error=str(exc)[:200])
        policy = None
    return _fb_policy_mod.allowed_set(policy, request_allowed)


async def enforce_provider_boundary(route: RouteDecision, payload, tenant_id: str | None) -> None:
    """FIX126 O4 — refuse a route whose PRIMARY provider lies outside the
    request's boundary, and drop Layer-1 hops outside it.

    The routing engine already refuses an allow-list that matches nothing
    (``_find_compatible_provider``); this is the last check before any
    provider is called, and the one place the tenant-level boundary meets
    the primary route. Raises :class:`NoAllowedProviderError`; nothing is
    dispatched.
    """
    # A caller that named its recipients (metadata.allowed_recipients): the
    # primary is refused here, before a stream begins, and a second route it
    # may not reach is dropped (services/providers/recipients.py — the same
    # check is made again where the adapter is called).
    from app.services.providers import recipients as _recipients

    _meta = getattr(payload, "metadata", None)
    _recipients.require(route.provider_name, route.model_name, _meta)
    if route.fallbacks:
        route.fallbacks = [(p, m) for (p, m) in route.fallbacks if _recipients.allowed(p, m, _meta)]

    allowed = await effective_provider_boundary(payload, tenant_id)
    if allowed is None:
        return
    if route.provider_name not in allowed:
        logger.warning(
            "routing_no_compatible_provider",
            model=route.model_name,
            provider=route.provider_name,
            allowed=sorted(allowed),
            stage="boundary",
            action="rejected",
        )
        raise NoAllowedProviderError(
            f"No provider in allowed set {sorted(allowed)} can serve "
            f"model '{route.model_name}'.",
            details={
                "model": route.model_name,
                "allowed_providers": sorted(allowed),
                "model_provider": route.provider_name,
            },
        )
    if route.fallbacks:
        route.fallbacks = [(p, m) for (p, m) in route.fallbacks if p in allowed]
