"""P5-04: Capability-based filtering tests.

Verifies:
- Streaming filter excludes non-streaming models
- Provider filter excludes disallowed providers
- Blocked model filter excludes specific models
- Auto-routing respects capability filters
- Fallback when preferred is filtered out
"""

import pytest

from app.domain.routing import ModelCandidate, RoutingRequest
from app.services.providers.model_registry import filter_candidates, get_all_candidates
from app.services.routing_service import RoutingService


@pytest.fixture()
def svc():
    return RoutingService()


class TestFilterCandidates:
    def test_streaming_filter(self):
        candidates = [
            ModelCandidate(provider="openai", model="gpt-4o", supports_streaming=True),
            ModelCandidate(provider="anthropic", model="claude", supports_streaming=False),
        ]
        result = filter_candidates(candidates, requires_streaming=True)
        assert len(result) == 1
        assert result[0].provider == "openai"

    def test_provider_filter(self):
        candidates = [
            ModelCandidate(provider="openai", model="gpt-4o"),
            ModelCandidate(provider="anthropic", model="claude"),
        ]
        result = filter_candidates(candidates, allowed_providers=frozenset({"openai"}))
        assert all(c.provider == "openai" for c in result)

    def test_blocked_model_filter(self):
        candidates = [
            ModelCandidate(provider="openai", model="gpt-4o"),
            ModelCandidate(provider="openai", model="gpt-3.5-turbo"),
        ]
        result = filter_candidates(candidates, blocked_models=frozenset({"gpt-3.5-turbo"}))
        assert len(result) == 1
        assert result[0].model == "gpt-4o"

    def test_no_filters_returns_all(self):
        candidates = get_all_candidates()
        result = filter_candidates(candidates)
        assert len(result) == len(candidates)

    def test_combined_filters(self):
        candidates = get_all_candidates()
        result = filter_candidates(
            candidates,
            requires_streaming=True,
            allowed_providers=frozenset({"openai"}),
        )
        assert all(c.provider == "openai" and c.supports_streaming for c in result)


class TestAutoRoutingWithFilters:
    def test_streaming_use_case_picks_streaming_capable_model(self, svc):
        """coding use_case with stream=True must select a streaming model.

        Previously this test was named ``test_streaming_filters_anthropic_default``
        and asserted Anthropic was rejected — but Anthropic gained
        streaming support via anthropic_adapter._process_sse_stream, so
        the filter no longer rejects it. The core invariant the test was
        protecting (routing honours the stream=True requirement) is still
        meaningful and is all we assert now.
        """
        result = svc.route(RoutingRequest(
            requested_model="auto",
            use_case="coding",
            stream=True,
        ))
        assert result.selected.supports_streaming is True

    def test_provider_constraint_respected(self, svc):
        result = svc.route(RoutingRequest(
            requested_model="auto",
            allowed_providers=frozenset({"anthropic"}),
        ))
        assert result.provider == "anthropic"
        assert result.constrained_by_policy is True

    def test_blocked_model_respected(self, svc):
        result = svc.route(RoutingRequest(
            requested_model="auto",
            blocked_models=frozenset({"gpt-4o-mini"}),
        ))
        assert result.model != "gpt-4o-mini"

    def test_no_constraints_uses_default(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto"))
        assert result.model == "gpt-4o-mini"

    def test_fallbacks_populated(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto"))
        assert len(result.fallbacks) > 0
