"""P5-02: Routing domain contract tests.

Verifies:
- ModelCandidate construction and defaults
- RoutingRequest construction and defaults
- RoutingResult with selected, fallbacks, rejected
- RejectedCandidate tracks reason
- Frozen dataclasses where expected
"""

import pytest

from app.domain.routing import (
    ModelCandidate,
    RejectedCandidate,
    RoutingRequest,
    RoutingResult,
)


class TestModelCandidate:
    def test_required_fields(self):
        c = ModelCandidate(provider="openai", model="gpt-4o")
        assert c.provider == "openai"
        assert c.model == "gpt-4o"

    def test_defaults(self):
        c = ModelCandidate(provider="p", model="m")
        assert c.supports_streaming is True
        assert c.supports_tools is False
        assert c.max_context_tokens == 128000
        assert c.cost_tier == "standard"
        assert c.latency_tier == "standard"

    def test_frozen(self):
        c = ModelCandidate(provider="p", model="m")
        with pytest.raises(AttributeError):
            c.provider = "x"  # type: ignore[misc]


class TestRoutingRequest:
    def test_minimal(self):
        r = RoutingRequest(requested_model="auto")
        assert r.requested_model == "auto"
        assert r.stream is False
        assert r.use_case == "general"
        assert r.allowed_providers is None
        assert r.blocked_models is None

    def test_with_constraints(self):
        r = RoutingRequest(
            requested_model="auto",
            allowed_providers=frozenset({"openai"}),
            blocked_models=frozenset({"gpt-3.5-turbo"}),
            preferred_provider="openai",
        )
        assert "openai" in r.allowed_providers
        assert "gpt-3.5-turbo" in r.blocked_models

    def test_frozen(self):
        r = RoutingRequest(requested_model="auto")
        with pytest.raises(AttributeError):
            r.requested_model = "gpt-4o"  # type: ignore[misc]


class TestRoutingResult:
    def test_selected_accessible(self):
        c = ModelCandidate(provider="openai", model="gpt-4o")
        result = RoutingResult(selected=c)
        assert result.provider == "openai"
        assert result.model == "gpt-4o"

    def test_with_fallbacks(self):
        primary = ModelCandidate(provider="openai", model="gpt-4o")
        fallback = ModelCandidate(provider="anthropic", model="claude-3-5-sonnet-20241022")
        result = RoutingResult(selected=primary, fallbacks=[fallback])
        assert len(result.fallbacks) == 1

    def test_with_rejected(self):
        primary = ModelCandidate(provider="openai", model="gpt-4o")
        rejected = RejectedCandidate(
            candidate=ModelCandidate(provider="openrouter", model="x"),
            reason="provider not allowed",
        )
        result = RoutingResult(selected=primary, rejected=[rejected])
        assert result.rejected[0].reason == "provider not allowed"

    def test_reasons(self):
        c = ModelCandidate(provider="openai", model="gpt-4o")
        result = RoutingResult(
            selected=c, reasons=["Explicit model selected"]
        )
        assert "Explicit model" in result.reasons[0]

    def test_defaults(self):
        c = ModelCandidate(provider="p", model="m")
        result = RoutingResult(selected=c)
        assert result.fallbacks == []
        assert result.rejected == []
        assert result.reasons == []
        assert result.constrained_by_policy is False


class TestRejectedCandidate:
    def test_construction(self):
        c = ModelCandidate(provider="openrouter", model="x")
        r = RejectedCandidate(candidate=c, reason="blocked by policy")
        assert r.candidate.provider == "openrouter"
        assert r.reason == "blocked by policy"
