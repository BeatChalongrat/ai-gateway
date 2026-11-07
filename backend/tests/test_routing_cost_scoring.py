"""Real-cost-aware routing scorer tests.

Verifies the new ``_score_candidate`` semantics:
- Real ``cost_per_input_token`` beats the coarse tier bucket when set.
- Tier fallback still works for candidates with unknown pricing.
- End-to-end ``RoutingService`` picks the cheapest real-cost model
  under ``prefer_cost``.
- ``preferred_provider`` bias still overrides cost.
- ``_derive_cost_tier`` thresholds.
- Every registry candidate with a real input price has the matching
  derived tier (no manual cost_tier drift).
"""

from __future__ import annotations

from app.domain.routing import ModelCandidate, RoutingRequest
from app.services.providers.model_registry import (
    _CANDIDATES,
    _derive_cost_tier,
)
from app.services.routing_service import (
    RoutingService,
    _COST_SCORE,
    _score_candidate,
)


class TestRealCostScoring:
    def test_real_cost_beats_tier_for_budget_model(self):
        # Real ultra-cheap model (qwen-turbo): cost score == int(0.00000005 * 1e7) == 0.
        # Tier-only budget model: cost score == _COST_SCORE["budget"] == 1.
        # With prefer_cost=True, A should score strictly lower.
        a = ModelCandidate(
            provider="qwen", model="qwen-turbo",
            cost_per_input_token=0.00000005, cost_per_output_token=0.0000002,
            cost_tier="budget",
        )
        b = ModelCandidate(
            provider="other", model="unknown-budget",
            cost_per_input_token=0.0, cost_per_output_token=0.0,
            cost_tier="budget",
        )
        assert _score_candidate(a, True, False) < _score_candidate(b, True, False)

    def test_premium_tier_fallback_when_no_real_cost(self):
        # cost_per_input_token == 0.0 → fall back to tier bucket.
        # premium → _COST_SCORE["premium"] == 3.
        # prefer_cost=True doubles the cost component, so:
        #   cost portion = 3 * 2 + 3 = 9.
        # latency default "standard" → 2; prefer_latency=False:
        #   latency portion = 2.
        # total = 11.
        c = ModelCandidate(
            provider="p", model="m",
            cost_per_input_token=0.0, cost_per_output_token=0.0,
            cost_tier="premium",
        )
        assert _score_candidate(c, True, False) == 11

    def test_routing_selects_cheapest_real_cost_model(self):
        # Auto-routing with prefer_cost=True should pick gpt-4o-mini —
        # cheapest real-cost model among the auto defaults pool.
        svc = RoutingService()
        result = svc.route(RoutingRequest(
            requested_model="auto",
            prefer_cost=True,
        ))
        assert result.selected.model == "gpt-4o-mini"

    def test_preferred_provider_bias_still_overrides_cost(self):
        # Even though provider A is cheaper, preferred_provider=B should win.
        a = ModelCandidate(
            provider="openai", model="gpt-4o-mini",
            cost_per_input_token=0.00000015, cost_per_output_token=0.0000006,
            cost_tier="budget",
        )
        b = ModelCandidate(
            provider="anthropic", model="claude-3-5-sonnet-20241022",
            cost_per_input_token=0.000003, cost_per_output_token=0.000015,
            cost_tier="standard",
        )
        score_a = _score_candidate(a, True, False, preferred_provider="anthropic")
        score_b = _score_candidate(b, True, False, preferred_provider="anthropic")
        assert score_b < score_a

    def test_derive_cost_tier_thresholds(self):
        assert _derive_cost_tier(0.00000005) == "budget"
        assert _derive_cost_tier(0.000001) == "standard"
        assert _derive_cost_tier(0.000005) == "premium"
        assert _derive_cost_tier(0.0) == "standard"

    def test_cost_tier_derived_not_hardcoded(self):
        # Every registry candidate with a real input price must carry
        # the cost_tier that ``_derive_cost_tier`` produces for that
        # price — otherwise the two systems can drift silently when a
        # price is updated without re-running the helper.
        priced = [c for c in _CANDIDATES if c.cost_per_input_token > 0.0]
        assert priced, "expected at least one priced candidate in the registry"
        for c in priced:
            expected = _derive_cost_tier(c.cost_per_input_token)
            assert c.cost_tier == expected, (
                f"{c.model}: cost_tier={c.cost_tier!r} but "
                f"_derive_cost_tier({c.cost_per_input_token})={expected!r}"
            )
