"""Provider-agnostic smart model selection for ``model="auto"`` requests.

The caller (chat-edge) sends ``RoutingHints`` describing what the turn
NEEDS (vision / reasoning / code / complexity / language) instead of
naming a model. This module picks the cheapest *capable* model from a
candidate pool the orchestrator has already narrowed to providers that
hold a valid API key right now.

Design properties (see AUTO_ROUTING_AUDIT.md, Fix Priority #1/#2):

  * **Provider-agnostic** — no provider or model is hardcoded as a
    default. The pool comes from ``model_registry.get_all_candidates()``
    (DB-backed, all 14 providers) filtered to key-valid providers, so
    adding a provider key makes its models eligible with NO code change.
  * **Capability-driven** — hard filters on the registry's
    ``supports_vision`` / ``supports_reasoning`` flags, with graceful
    relaxation: if a hard filter empties the pool we fall back to the
    unfiltered pool rather than failing.
  * **Cost-aware** — among capable models, the cheapest wins
    (``cost_tier`` budget < standard < premium), tie-broken by latency
    then model id for deterministic selection. A high-complexity or
    code turn prefers a flagship (non-budget) tier first.

This function is pure (no I/O) so it is unit-testable in isolation; the
orchestrator owns the key-validity lookup and feeds the filtered pool in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.domain.routing import ModelCandidate
    from app.schemas.public import RoutingHints


# Lower rank = preferred. Unknown tiers fall back to the neutral middle
# so a registry row with an unexpected tier string still sorts sanely.
# ``cheap`` is treated as the cheapest bucket (some discovery rows use it).
_COST_RANK = {"cheap": 0, "budget": 0, "standard": 1, "premium": 2}
_LAT_RANK = {"fast": 0, "standard": 1, "slow": 2}


@dataclass
class SmartSelection:
    """Result of a smart-routing decision."""

    candidate: "ModelCandidate"
    reason: str
    candidates_count: int   # size of the key-valid pool before hint filtering
    filtered_count: int     # size after capability filtering


def _cost_rank(tier: str) -> int:
    return _COST_RANK.get(tier, 1)


def _lat_rank(tier: str) -> int:
    return _LAT_RANK.get(tier, 1)


def select_model(
    hints: "Optional[RoutingHints]",
    candidates: "list[ModelCandidate]",
    proven_providers: "frozenset[str]" = frozenset(),
) -> "Optional[SmartSelection]":
    """Pick the best model for ``hints`` from ``candidates``.

    ``candidates`` MUST already be restricted to providers with a usable
    key (the orchestrator does this). ``proven_providers`` is the subset
    whose key has been *tested valid* — used only as a tie-break so a
    proven provider beats an untested one at the same cost/latency, which
    avoids electing a model on a key that will 401 at dispatch. Returns
    ``None`` only when ``candidates`` is empty — the caller treats that as
    "no usable provider" and raises a clear error rather than emitting an
    empty response.
    """
    total = len(candidates)
    if total == 0:
        return None

    pool = list(candidates)

    needs_reasoning = bool(getattr(hints, "needs_reasoning", False)) if hints else False
    needs_vision = bool(getattr(hints, "needs_vision", False)) if hints else False
    has_code = bool(getattr(hints, "has_code", False)) if hints else False
    complexity = (getattr(hints, "complexity", None) or "medium") if hints else "medium"

    # Step 3 — hard capability filters, each with graceful relaxation so a
    # narrow hint can never wedge the pool to empty.
    if needs_reasoning:
        narrowed = [c for c in pool if getattr(c, "supports_reasoning", False)]
        if narrowed:
            pool = narrowed
    if needs_vision:
        narrowed = [c for c in pool if getattr(c, "supports_vision", False)]
        if narrowed:
            pool = narrowed

    filtered = len(pool)

    # Step 4 — tier preference. A high-complexity or code-bearing turn
    # prefers a flagship (non-budget) model; relax to the whole pool when
    # none exist so a budget-only deployment still answers.
    want_flagship = complexity == "high" or has_code
    if want_flagship:
        non_budget = [c for c in pool if _cost_rank(c.cost_tier) >= 1]
        if non_budget:
            pool = non_budget

    # Step 5 — cheapest capable first, then deterministic, reliability-
    # ordered tie-breaks (all provider-agnostic — none names a provider):
    #   1. cost tier (cheapest)
    #   2. latency tier (fastest)
    #   3. key proven valid before untested — don't elect a model on a key
    #      that will 401 at dispatch when an equally-cheap proven one exists
    #   4. directly-addressed model id before an aggregator id (one with a
    #      "/", e.g. "anthropic/claude-3.7-sonnet" on OpenRouter) — the
    #      aggregator may not actually serve that id (404), so prefer the
    #      first-party form when cost/latency/proof are equal
    #   5. model id (stable alphabetical) so selection is deterministic
    def _sort_key(c):
        return (
            _cost_rank(c.cost_tier),
            _lat_rank(c.latency_tier),
            0 if c.provider in proven_providers else 1,
            1 if "/" in c.model else 0,
            c.model,
        )

    pool.sort(key=_sort_key)
    chosen = pool[0]

    bits = [f"{complexity} complexity"]
    if needs_reasoning:
        bits.append("reasoning")
    if needs_vision:
        bits.append("vision")
    if want_flagship:
        bits.append("flagship tier")
    bits.append("cheapest capable")
    reason = ", ".join(bits)

    return SmartSelection(
        candidate=chosen,
        reason=reason,
        candidates_count=total,
        filtered_count=filtered,
    )
