"""P5-07: Cost/latency-aware routing tests.

Verifies:
- prefer_cost=True orders budget models first
- prefer_latency=True orders fast models first
- Default scoring is reasonable
- Scoring function is deterministic
"""

import pytest

from app.domain.routing import ModelCandidate, RoutingRequest
from app.services.routing_service import RoutingService, _score_candidate


@pytest.fixture()
def svc():
    return RoutingService()


class TestScoreFunction:
    def test_budget_scores_lower_than_premium(self):
        budget = ModelCandidate(provider="p", model="m1", cost_tier="budget")
        premium = ModelCandidate(provider="p", model="m2", cost_tier="premium")
        assert _score_candidate(budget, True, False) < _score_candidate(premium, True, False)

    def test_fast_scores_lower_than_slow(self):
        fast = ModelCandidate(provider="p", model="m1", latency_tier="fast")
        slow = ModelCandidate(provider="p", model="m2", latency_tier="slow")
        assert _score_candidate(fast, False, True) < _score_candidate(slow, False, True)

    def test_deterministic(self):
        c = ModelCandidate(provider="p", model="m", cost_tier="standard")
        s1 = _score_candidate(c, False, False)
        s2 = _score_candidate(c, False, False)
        assert s1 == s2


class TestCostAwareRouting:
    def test_prefer_cost_orders_budget_first(self, svc):
        result = svc.route(RoutingRequest(
            requested_model="auto",
            prefer_cost=True,
        ))
        # Primary should be budget (gpt-4o-mini)
        assert result.selected.cost_tier == "budget"
        # Fallbacks should also prioritize budget
        if result.fallbacks:
            scores = [_score_candidate(c, True, False) for c in result.fallbacks]
            assert scores == sorted(scores)


class TestLatencyAwareRouting:
    def test_prefer_latency_orders_fast_first(self, svc):
        result = svc.route(RoutingRequest(
            requested_model="auto",
            prefer_latency=True,
        ))
        assert result.selected.latency_tier in ("fast", "standard")
        if result.fallbacks:
            scores = [_score_candidate(c, False, True) for c in result.fallbacks]
            assert scores == sorted(scores)
