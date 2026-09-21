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

    a. capability filters — two kinds (FIX47 Part B):
         HARD  tools the request declares / images it carries. A model
               without the capability fails for certain, so an empty pool
               here is NoCapableModelError, never "the whole pool again"
               (that relaxation is how a tools turn landed on a tools-blind
               model and died with "try again").
         SOFT  reasoning (the answer arrives without thinking — the gateway
               sends a reasoning_notice) and a task's *preferred* vision /
               tools — relaxed when they would empty the pool, as before.
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
capable) with reason="router_error:<type>". Two outcomes are errors by
design: an EMPTY candidate list returns None (the orchestrator raises
ProviderUnavailableError — nothing to route to), and a hard need no
candidate can serve raises NoCapableModelError (routing to a model that
cannot do the job is not "answering").
"""

from __future__ import annotations

import math
import random
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
    explored: bool = False
    signals: list[str] = field(default_factory=list)

    @property
    def ordered(self) -> list["ModelCandidate"]:
        return [self.primary, *self.fallbacks]


_AGGREGATOR_PROVIDERS = frozenset({"openrouter"})


class NoCapableModelError(Exception):
    """No candidate serves a HARD need (tools declared / images attached).

    Pure-router exception; the orchestrator turns it into the gateway's
    ``no_capable_model`` error. Carries what was missing and how big the
    pool was, so the log says which capability the catalogue lacks.
    """

    def __init__(self, missing: list[str], pool_size: int, pool: list[str]):
        self.missing = list(missing)
        self.pool_size = pool_size
        self.pool = list(pool)
        super().__init__(f"no candidate supports {'+'.join(self.missing)} (pool={pool_size})")


def _relax(pool: list, narrowed: list) -> list:
    return narrowed if narrowed else pool


def _require(pool: list, attr: str, *, missing: list[str], name: str) -> list:
    """A HARD filter: keep the candidates with ``attr``; an empty result is
    recorded in ``missing`` (the caller raises once every hard need has
    been checked, so the error names all of them)."""
    kept = [c for c in pool if getattr(c, attr, False)]
    if not kept:
        missing.append(name)
        return pool
    return kept


def _context_hint(hints, name: str) -> int:
    """A FIX102 context hint as a non-negative int; 0 when absent."""
    if hints is None:
        return 0
    try:
        return max(int(getattr(hints, name, 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


def _window(candidate) -> int:
    return int(getattr(candidate, "max_context_tokens", 0) or 0)


def _require_window(pool: list, tokens: int, *, missing: list[str]) -> list:
    """FIX102 — the HARD context filter. A model whose catalogue window is
    smaller than the compiled request refuses it before inference, so it is
    no more a candidate than a tools-blind model is for a tools turn."""
    kept = [c for c in pool if _window(c) >= tokens]
    if not kept:
        missing.append("context")
        return pool
    return kept


def _capability_filter(
    pool: list,
    *,
    needs_reasoning: bool,
    needs_vision: bool,
    needs_tools: bool,
    require_vision: bool = False,
    require_tools: bool = False,
) -> list:
    """Soft needs relax; hard needs raise.

    ``needs_*`` are preferences (a task's declared needs, an explicit
    reasoning request): when no candidate has the capability the pool is
    returned whole. ``require_*`` are facts about the request (tools are
    in the payload, an image is in the messages): when no candidate has
    the capability there is no model to route to.
    """
    missing: list[str] = []
    if require_vision:
        pool = _require(pool, "supports_vision", missing=missing, name="vision")
    if require_tools:
        pool = _require(pool, "supports_tools", missing=missing, name="tools")
    if missing:
        raise NoCapableModelError(missing, len(pool), [c.model for c in pool])
    if needs_reasoning:
        pool = _relax(pool, [c for c in pool if getattr(c, "supports_reasoning", False)])
    if needs_vision and not require_vision:
        pool = _relax(pool, [c for c in pool if getattr(c, "supports_vision", False)])
    if needs_tools and not require_tools:
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


def _prior_score(prior_pos: dict[str, int], model: str) -> float:
    """Configured prior as a 0..1 score: first in the list is ~1, then an even
    decay; a model absent from the prior gets a low-but-nonzero floor so the
    tie-breakers below still order it."""
    if model not in prior_pos:
        return 0.15
    n = max(len(prior_pos), 1)
    return round(1.0 - prior_pos[model] / (n + 1), 4)


def _order(
    pool: list,
    *,
    task: str,
    ranking: Optional[TenantRanking],
    preferred: Sequence[str],
    proven: frozenset[str],
) -> tuple[list, bool]:
    """FIX36 A2 - one blended score per candidate instead of the FIX35
    "trusted-cell wins outright, else static list" cliff:

        blend = w * live + (1 - w) * prior,   w = n / (n + K)

    ``live`` is the tenant's 7-day score for (rank group, model); ``prior``
    is the configured per-group order from the asset; ``n`` is the group
    cell's sample count and K = ``prior_weight_samples``. With no data the
    prior decides; every request shifts weight smoothly toward the tenant's
    own evidence - there is no hard switch at K."""
    group = taxonomy.task_group(task)
    prior_pos = {m: i for i, m in enumerate(taxonomy.group_prior(group))}
    if not prior_pos:  # group missing from asset -> legacy per-task list
        prior_pos = {m: i for i, m in enumerate(taxonomy.task_spec(task).get("default_rank", []))}
    pref_rank = {m: i for i, m in enumerate(preferred)}
    k_samples = taxonomy.prior_weight_samples()
    used_ranking = False
    blended: dict[str, float] = {}
    for c in pool:
        live, n = (None, 0)
        if ranking is not None:
            live, n = ranking.score_with_samples(group, c.model)
        prior = _prior_score(prior_pos, c.model)
        if live is None:
            blended[c.model] = prior
        else:
            w = n / (n + k_samples)
            blended[c.model] = round(w * live + (1.0 - w) * prior, 4)
            used_ranking = True

    def key(c):
        # Lower tuple sorts first. Blended evidence first (higher score ->
        # smaller key), then the admin's preference, then cost, proof,
        # first-party, id.
        return (
            -blended.get(c.model, 0.0),
            pref_rank.get(c.model, 10_000),
            tiers.tier_rank(tiers.band_for_candidate(c)),
            0 if c.provider in proven else 1,
            1 if c.provider in _AGGREGATOR_PROVIDERS or "/" in c.model else 0,
            c.model,
        )

    return sorted(pool, key=key), used_ranking


def _explore(
    ordered: list,
    *,
    ranking: Optional[TenantRanking],
    group: str,
    rng: "random.Random | None" = None,
) -> tuple[list, bool]:
    """FIX36 A3 - with probability ``exploration_rate`` promote ONE
    under-sampled candidate (fewer than min_samples requests in this rank
    group) to primary so the ranking table keeps filling. The rest of the
    order is untouched; the displaced primary becomes first fallback, so a
    failed exploration still lands on the best-known model immediately.
    Never applied to an explicit model - the orchestrator only calls the
    router for meta ids ("auto"/empty), and route_auto also skips
    exploration under task_override (a caller that asserted the task asked
    for determinism)."""
    rate = taxonomy.exploration_rate()
    if rate <= 0.0 or len(ordered) < 2:
        return ordered, False
    r = (rng or random).random()
    if r >= rate:
        return ordered, False
    min_n = ranking.min_samples if ranking is not None else 20
    under: list[int] = []
    for i, c in enumerate(ordered[1:], start=1):
        n = 0
        if ranking is not None:
            _s, n = ranking.score_with_samples(group, c.model)
        if n < min_n:
            under.append(i)
    if not under:
        return ordered, False
    pick = (rng or random).choice(under)
    reordered = [ordered[pick], *ordered[:pick], *ordered[pick + 1:]]
    return reordered, True


def provider_diverse(ordered: Sequence["ModelCandidate"], limit: int) -> list["ModelCandidate"]:
    """FIX124 — the alternatives handed to the fallback walk, at most ``limit``.

    The first five of the ranking were five models of ONE provider whose
    account was rate-limited, so the walk had nowhere to go. The ranking is
    kept, but each provider's best model comes first and the rest fill the
    remaining places — a provider-wide or account-wide failure still leaves
    a candidate."""
    firsts: list = []
    rest: list = []
    seen: set[str] = set()
    for c in ordered:
        if c.provider in seen:
            rest.append(c)
        else:
            seen.add(c.provider)
            firsts.append(c)
    return (firsts + rest)[: max(0, limit)]


_LATENCY_RANK = {"fast": 0, "standard": 1, "slow": 2}


def closest_to_requested(
    ordered: Sequence["ModelCandidate"],
    requested: Optional["ModelCandidate"],
    *,
    unknown_band: str = "low",
) -> list["ModelCandidate"]:
    """FIX124 A1 — ``ordered`` (the router's ranking, already capable of the
    request) re-ordered by closeness to the model the person picked.

    A rescue is charged at the price of the model that answers, so a person
    who picked the free in-house model must not be answered by an opus-class
    one while something cheap works. Closeness, in order: the distance
    between price bands (``tiers``), the distance between prices inside it
    (log ratio, so $1 vs $2 is as far as $2 vs $4), the faster
    ``latency_tier``, then the router's own position — which already carries
    the tenant's 7-day success rates.

    What the catalogue cannot say: a price of 0 is "free" for the in-house
    box and "never entered" for a dozen paid models, with nothing to tell
    them apart. A CANDIDATE without a price keeps the catalogue's convention
    (band from ``cost_tier``, else medium) and loses every price comparison;
    a REQUESTED model without one is placed by a ``cost_tier`` that says
    something (budget / premium), else in ``unknown_band`` (policy:
    ``rescue_order.requested_price_unknown_band``) with the cheapest
    candidate nearest — when the price is not known, err to the cheap side.
    ``requested=None`` (not in the catalogue) keeps the router's ranking.
    """
    if requested is None:
        return list(ordered)
    req_price = float(getattr(requested, "cost_per_input_token", 0.0) or 0.0)
    legacy = (getattr(requested, "cost_tier", "") or "").lower()
    if req_price > 0 or legacy not in ("", "standard"):
        req_band = tiers.band_for_candidate(requested)
    else:
        req_band = unknown_band
    req_rank = tiers.tier_rank(req_band)

    def _key(item: tuple[int, "ModelCandidate"]):
        index, cand = item
        price = float(getattr(cand, "cost_per_input_token", 0.0) or 0.0)
        if price <= 0:
            price_gap = math.inf
        elif req_price > 0:
            price_gap = abs(math.log(price / req_price))
        else:
            price_gap = price
        return (
            abs(tiers.tier_rank(tiers.band_for_candidate(cand)) - req_rank),
            price_gap,
            _LATENCY_RANK.get(getattr(cand, "latency_tier", "standard"), 1),
            index,
        )

    return [cand for _, cand in sorted(enumerate(ordered), key=_key)]


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
    rng: "random.Random | None" = None,
    require_tools: bool = False,
    require_vision: bool = False,
) -> Optional[RoutePlan]:
    """The four steps. Returns None only for an empty candidate list.

    ``task_override`` lets a caller that already knows the task (the legacy
    ``metadata.use_case`` contract: "coding" → code_generate) skip the
    classifier; an unknown override is ignored.

    ``require_tools`` / ``require_vision`` are the request's HARD needs
    (tool definitions in the payload / an image in the messages), decided
    by the orchestrator from the payload itself, not from a hint. When no
    candidate serves one, ``NoCapableModelError`` is raised — this is the
    one exception ``route_auto`` lets through.
    """
    if not candidates:
        return None
    # The hard needs are checked BEFORE the never-fail wrapper, on the raw
    # pool: ``default_plan`` (the collapse for any router error) must never
    # see a candidate that cannot serve the request either.
    missing: list[str] = []
    hard_pool = list(candidates)
    if require_vision:
        hard_pool = _require(hard_pool, "supports_vision", missing=missing, name="vision")
    if require_tools:
        hard_pool = _require(hard_pool, "supports_tools", missing=missing, name="tools")
    min_context = _context_hint(hints, "min_context_tokens")
    if min_context:
        hard_pool = _require_window(hard_pool, min_context, missing=missing)
    if missing:
        raise NoCapableModelError(missing, len(hard_pool), [c.model for c in hard_pool])
    candidates = hard_pool
    try:
        # Step 1 — classify.
        has_images = require_vision or (bool(getattr(hints, "needs_vision", False)) if hints else False)
        has_tools = require_tools or (bool(getattr(hints, "has_tools", False)) if hints else False)
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
        # A hint alone stays a preference (an older caller may set it
        # loosely); the payload's own tools / image blocks are the facts.
        needs_vision = has_images or "vision" in needs
        needs_tools = has_tools or "tools" in needs

        # Step 4a — capability filters: hard needs raise, soft needs relax.
        pool = _capability_filter(
            list(candidates),
            needs_reasoning=needs_reasoning,
            needs_vision=needs_vision,
            needs_tools=needs_tools,
            require_vision=require_vision,
            require_tools=require_tools,
        )
        if prefer_reasoning and not needs_reasoning:
            thinking = [c for c in pool if getattr(c, "supports_reasoning", False)]
            if thinking and len(thinking) >= 2:
                # keep both, but bias order below via a stable partition
                pool = thinking + [c for c in pool if c not in thinking]

        # Step 3 — tier ceiling. The task's own preferred band lowers the
        # start of the search but never raises the ceiling.
        pool, relaxed = _apply_ceiling(pool, ceiling)
        # FIX102 — SOFT: keep an auto conversation on a model at least as
        # large as the window it is budgeted against, when the ceiling left
        # one. Otherwise one turn on a small model shrinks the next turn's
        # budget from ~90 turns of room to ~4. Never overrides the ceiling.
        preferred_window = _context_hint(hints, "preferred_context_tokens")
        window_kept = False
        if preferred_window:
            roomy = [c for c in pool if _window(c) >= preferred_window]
            if roomy:
                pool = roomy
                window_kept = True
        filtered = len(pool)

        # Step 2 + 4c - order (blended prior/live), then optional exploration.
        ordered, used_ranking = _order(pool, task=tc.task, ranking=ranking, preferred=preferred, proven=proven)
        explored = False
        if not task_override:
            ordered, explored = _explore(ordered, ranking=ranking, group=taxonomy.task_group(tc.task), rng=rng)

        bits = [f"task={tc.task}({tc.confidence:.2f})", f"tier<={ceiling}"]
        if needs_reasoning:
            bits.append("reasoning")
        if needs_vision:
            bits.append("vision")
        if needs_tools:
            bits.append("tools")
        bits.append("tenant-ranked" if used_ranking else "prior-rank")
        if min_context:
            bits.append(f"context>={min_context}")
        if window_kept:
            bits.append(f"window>={preferred_window}")
        if relaxed:
            bits.append("tier_relaxed")
        if explored:
            bits.append("explore")
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
            explored=explored,
            signals=list(tc.signals),
        )
    except NoCapableModelError:
        # The one routing outcome that IS an error: nothing here can serve
        # the request. default_plan would pick a model that fails for sure.
        raise
    except Exception as exc:  # noqa: BLE001 — never fail because of routing
        return default_plan(candidates, preferred, reason=f"router_error:{type(exc).__name__}")
