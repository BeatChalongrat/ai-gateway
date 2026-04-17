"""P5-03: Rule-based routing engine tests.

Verifies:
- Explicit model resolves to correct provider
- Unknown model falls back to openai
- Auto routing by use_case
- Legacy bridge works
- Routing always returns concrete model (never "auto")
"""

import pytest

from app.domain.routing import RoutingRequest, RoutingResult
from app.services.routing_service import RoutingService


@pytest.fixture()
def svc():
    return RoutingService()


class TestExplicitModelRouting:
    def test_openai_model(self, svc):
        result = svc.route(RoutingRequest(requested_model="gpt-4o"))
        assert result.provider == "openai"
        assert result.model == "gpt-4o"

    def test_anthropic_model(self, svc):
        result = svc.route(RoutingRequest(requested_model="claude-3-5-sonnet-20241022"))
        assert result.provider == "anthropic"

    def test_openrouter_prefix(self, svc):
        result = svc.route(RoutingRequest(requested_model="meta-llama/llama-3-70b"))
        assert result.provider == "openrouter"

    def test_unknown_model_fallback(self, svc):
        result = svc.route(RoutingRequest(requested_model="unknown-model-xyz"))
        assert result.provider == "openai"
        assert "Unknown" in result.reasons[0] or "falling back" in result.reasons[0]

    def test_explicit_model_never_returns_auto(self, svc):
        result = svc.route(RoutingRequest(requested_model="gpt-4o-mini"))
        assert result.model != "auto"


class TestAutoRouting:
    def test_general_use_case(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto", use_case="general"))
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"

    def test_coding_use_case(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto", use_case="coding"))
        assert result.provider == "anthropic"
        assert "claude" in result.model

    def test_reasoning_use_case(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto", use_case="reasoning"))
        assert result.provider == "anthropic"

    def test_unknown_use_case_defaults_to_general(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto", use_case="poetry"))
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"

    def test_auto_never_returns_auto_model(self, svc):
        for use_case in ("general", "coding", "reasoning", "unknown"):
            result = svc.route(RoutingRequest(requested_model="auto", use_case=use_case))
            assert result.model != "auto"


class TestRoutingResultShape:
    def test_has_reasons(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto"))
        assert len(result.reasons) > 0

    def test_is_routing_result(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto"))
        assert isinstance(result, RoutingResult)


class TestLegacyBridge:
    def test_select_route_works(self, svc):
        from app.domain.entities import RequestContext, RouteDecision
        from app.schemas.common import Message
        from app.schemas.public import ChatCompletionsRequest

        ctx = RequestContext(tenant_id="t", project_id="p", project_name="P")
        payload = ChatCompletionsRequest(
            model="gpt-4o-mini",
            messages=[Message(role="user", content="hi")],
        )
        route = svc.select_route(ctx, payload, None)
        assert isinstance(route, RouteDecision)
        assert route.provider_name == "openai"
        assert route.model_name == "gpt-4o-mini"
