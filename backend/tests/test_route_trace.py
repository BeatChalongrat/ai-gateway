"""P5-08: Route decision trace tests.

Verifies:
- to_trace_dict() serializes selected, fallbacks, rejected, reasons
- Trace is JSON-serializable
- Empty result produces valid trace
"""

import json

from app.domain.routing import (
    ModelCandidate,
    RejectedCandidate,
    RoutingResult,
)


class TestRouteTrace:
    def test_basic_trace(self):
        result = RoutingResult(
            selected=ModelCandidate(provider="openai", model="gpt-4o"),
            reasons=["Explicit model"],
        )
        trace = result.to_trace_dict()
        assert trace["selected"]["provider"] == "openai"
        assert trace["selected"]["model"] == "gpt-4o"
        assert trace["reasons"] == ["Explicit model"]

    def test_trace_with_fallbacks(self):
        result = RoutingResult(
            selected=ModelCandidate(provider="openai", model="gpt-4o"),
            fallbacks=[ModelCandidate(provider="anthropic", model="claude")],
        )
        trace = result.to_trace_dict()
        assert len(trace["fallbacks"]) == 1
        assert trace["fallbacks"][0]["provider"] == "anthropic"

    def test_trace_with_rejected(self):
        result = RoutingResult(
            selected=ModelCandidate(provider="openai", model="gpt-4o"),
            rejected=[
                RejectedCandidate(
                    candidate=ModelCandidate(provider="openrouter", model="x"),
                    reason="not allowed",
                )
            ],
        )
        trace = result.to_trace_dict()
        assert len(trace["rejected"]) == 1
        assert trace["rejected"][0]["reason"] == "not allowed"

    def test_trace_is_json_serializable(self):
        result = RoutingResult(
            selected=ModelCandidate(provider="openai", model="gpt-4o", cost_tier="budget"),
            fallbacks=[ModelCandidate(provider="anthropic", model="claude")],
            rejected=[
                RejectedCandidate(
                    candidate=ModelCandidate(provider="x", model="y"),
                    reason="filtered",
                )
            ],
            reasons=["test reason"],
            constrained_by_policy=True,
        )
        serialized = json.dumps(result.to_trace_dict())
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["constrained_by_policy"] is True

    def test_empty_trace(self):
        result = RoutingResult(
            selected=ModelCandidate(provider="p", model="m"),
        )
        trace = result.to_trace_dict()
        assert trace["fallbacks"] == []
        assert trace["rejected"] == []
        assert trace["reasons"] == []

    def test_trace_includes_cost_tier(self):
        result = RoutingResult(
            selected=ModelCandidate(
                provider="openai", model="gpt-4o-mini", cost_tier="budget"
            ),
        )
        trace = result.to_trace_dict()
        assert trace["selected"]["cost_tier"] == "budget"
