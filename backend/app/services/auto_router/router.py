"""FIX35 Part C step 4 — the router itself (pure; no I/O).

    route_auto(...) -> RoutePlan(primary, fallbacks, task, tier, reason)

Inputs the orchestrator gathers (it owns every lookup):
    candidates      registry ModelCandidates ALREADY filtered to providers
                    that hold a usable key right now (the one thing OpenRouter
                    does not need and we must never drop)
    hints           RoutingHints from the caller (may be None)
    messages        for the classifier (masked; runs after DLP)
    ranking         TenantRanking (may be empty) — step 2
    ceiling         effective cost tier — step 3
    preferred       admin's auto_default_model / fallbacks (routing_settings)
    proven          providers whose key was tested valid (tie-break only)

Order of operations and the invariants each step keeps:

    a. hard capability filters — reasoning (explicit reasoning intent or a
       reasoning-preferring task) / vision / tools — each with graceful
       relaxation so a narrow need never empties the pool (smart_router's
       rule, kept)
    b. tier ceiling — drop bands above the ceiling; if that empties the
       pool, relax to the cheapest band that has anything (a budget-only
       deployment still answers; logged as ``tier_relaxed``)
    c. order — tenant ranking score for (task, model) when trusted, else
       the taxonomy's static default_rank position, else admin preference,
       else cheapest band, then proven-key, then first-party-before-
       aggregator, then id (deterministic)
    d. the ordered list IS the plan: [0] primary, [1:] fallbacks

"Never fail because of routing": every step is wrapped; any exception
collapses to ``default_plan`` (admin preference ∩ candidates, else cheapest
capable) with reason="router_error:<type>". Only an EMPTY candidate list
returns None — the orchestrator raises ProviderUnavailableError for that,
because there is genuinely nothing to route to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

from app.services.auto_router import taxonomy, tiers
from app.services.auto_router.ranking import TenantRanking

if TYPE_CHECKING:
    from app.domain.routing import ModelCandidate
    from app.schemas.public import RoutingHints


@dataclass
class RoutePlan:
    primary: "ModelCandidate"
    fallbacks: list["ModelCandidate"]
    task: str
    task_confidence: float
    tier: str
    reason: str
    candidates_count: int
    filtered_count: int
    used_ranking: bool = False
    tier_relaxed: bool = False
    signals: list[str] = field(default_factory=list)

    @property
    def ordered(self) -> list["ModelCandidate"]:
        return [self.primary, *self.fallbacks]


_AGGREGATOR_PROVIDERS = frozenset({"openrouter"})


def _relax(pool: list, narrowed: list) -> list:
    return narrowed if narrowed else pool


def _capability_filter(pool: list, *, needs_reasoning: bool, needs_vision: bool, needs_tools: bool) -> list:
    if needs_reasoning:
        pool = _relax(pool, [c for c in pool if getattr(c, "supports_reasoning", False)])
    if needs_vision:
        pool = _relax(pool, [c for c in pool if getattr(c, "supports_vision", False)])
    if needs_tools:
        pool = _relax(pool, [c for c in pool if getattr(c, "supports_tools", False)])
    return pool


def _apply_ceiling(pool: list, ceiling: str) -> tuple[list, bool]:
    kept = tiers.filter_by_ceiling(pool, ceiling)
    if kept:
        return kept, False
    # Relax to the cheapest band present so a budget-only or premium-only
    # deployment still answers; the caller logs tier_relaxed.
    cheapest = min(tiers.tier_rank(tiers.band_for_candidate(c)) for c in pool)
    return [c for c in pool if tiers.tier_rank(tiers.band_for_candidate(c)) == cheapest], True


def _order(
    pool: list,
    *,
    task: str,
    ranking: Optional[TenantRanking],
    preferred: Sequence[str],
    proven: frozenset[str],
) -> tuple[list, bool]:
    spec = taxonomy.task_spec(task)
    default_rank = {m: i for i, m in enumerate(spec.get("default_rank", []))}
    pref_rank = {m: i for i, m in enumerate(preferred)}
    used_ranking = False
    scored: dict[str, float] = {}
    if ranking is not None:
        for c in pool:
            s = ranking.score(task, c.model)
            if s is not None:
                scored[c.model] = s
                used_ranking = True

    def key(c):
        # Lower tuple sorts first. Tenant evidence first (higher score →
        # smaller key), then the taxonomy's static order, then the admin's
        # preference, then cost, proof, first-party, id.
        return (
            -scored.get(c.model, -1.0),
            default_rank.get(c.model, 10_000),
            pref_rank.get(c.model, 10_000),
            tiers.tier_rank(tiers.band_for_candidate(c)),
            0 if c.provider in proven else 1,
            1 if c.provider in _AGGREGATOR_PROVIDERS or "/" in c.model else 0,
            c.model,
        )

    return sorted(pool, key=key), used_ranking


def default_plan(candidates: Sequence["ModelCandidate"], preferred: Sequence[str], reason: str) -> Optional[RoutePlan]:
    """The default set: admin preference that is routable, else cheapest.
    Used when classification / ranking / anything else in the pipeline
    fails — a request never fails because of a routing problem."""
    if not candidates:
        return None
    by_id = {c.model: c for c in candidates}
    ordered = [by_id[m] for m in preferred if m in by_id]
    rest = tiers.cheapest_first(c for c in candidates if c.model not in {o.model for o in ordered})
    ordered.extend(rest)
    return RoutePlan(
        primary=ordered[0],
        fallbacks=ordered[1:],
        task=taxonomy.GENERAL,
        task_confidence=0.0,
        tier=tiers.band_for_candidate(ordered[0]),
        reason=reason,
        candidates_count=len(candidates),
        filtered_count=len(candidates),
    )


def route_auto(
    *,
    candidates: Sequence["ModelCandidate"],
    hints: "Optional[RoutingHints]",
    messages: Iterable[dict],
    ranking: Optional[TenantRanking],
    ceiling: str,
    preferred: Sequence[str] = (),
    proven: frozenset[str] = frozenset(),
    reasoning_requested: bool = False,
    task_override: Optional[str] = None,
) -> Optional[RoutePlan]:
    """The four steps. Returns None only for an empty candidate list.

    ``task_override`` lets a caller that already knows the task (the legacy
    ``metadata.use_case`` contract: "coding" → code_generate) skip the
    classifier; an unknown override is ignored.
    """
    if not candidates:
        return None
    try:
        # Step 1 — classify.
        has_images = bool(getattr(hints, "needs_vision", False)) if hints else False
        has_tools = bool(getattr(hints, "has_tools", False)) if hints else False
        if task_override and task_override in taxonomy.load_taxonomy()["tasks"]:
            tc = taxonomy.TaskClass(task=task_override, confidence=1.0, score=1.0, signals=["<use_case>"])
        else:
            tc = taxonomy.classify_task(messages, has_images=has_images, has_tools=has_tools)
        spec = taxonomy.task_spec(tc.task)
        needs = set(spec.get("needs", []))

        needs_reasoning = reasoning_requested or bool(getattr(hints, "needs_reasoning", False)) if hints else reasoning_requested
        # A reasoning-preferring task (math / logic / debugging) prefers a
        # thinking model but does not require one — it is a preference the
        # ceiling and the ranking may override; an explicit reasoning
        # request stays a hard need.
        prefer_reasoning = "reasoning_pref" in needs
        needs_vision = has_images or "vision" in needs
        needs_tools = has_tools or "tools" in needs

        # Step 4a — capability filters (relaxing).
        pool = _capability_filter(
            list(candidates),
            needs_reasoning=needs_reasoning,
            needs_vision=needs_vision,
            needs_tools=needs_tools,
        )
        if prefer_reasoning and not needs_reasoning:
            thinking = [c for c in pool if getattr(c, "supports_reasoning", False)]
            if thinking and len(thinking) >= 2:
                # keep both, but bias order below via a stable partition
                pool = thinking + [c for c in pool if c not in thinking]

        # Step 3 — tier ceiling. The task's own preferred band lowers the
        # start of the search but never raises the ceiling.
        pool, relaxed = _apply_ceiling(pool, ceiling)
        filtered = len(pool)

        # Step 2 + 4c — order.
        ordered, used_ranking = _order(pool, task=tc.task, ranking=ranking, preferred=preferred, proven=proven)

        bits = [f"task={tc.task}({tc.confidence:.2f})", f"tier<={ceiling}"]
        if needs_reasoning:
            bits.append("reasoning")
        if needs_vision:
            bits.append("vision")
        if needs_tools:
            bits.append("tools")
        bits.append("tenant-ranked" if used_ranking else "default-rank")
        if relaxed:
            bits.append("tier_relaxed")
        return RoutePlan(
            primary=ordered[0],
            fallbacks=ordered[1:],
            task=tc.task,
            task_confidence=tc.confidence,
            tier=ceiling,
            reason=", ".join(bits),
            candidates_count=len(candidates),
            filtered_count=filtered,
            used_ranking=used_ranking,
            tier_relaxed=relaxed,
            signals=list(tc.signals),
        )
    except Exception as exc:  # noqa: BLE001 — never fail because of routing
        return default_plan(candidates, preferred, reason=f"router_error:{type(exc).__name__}")
