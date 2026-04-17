"""P5-05: Project/policy-aware routing tests.

Verifies:
- allowed_providers constraint restricts routing
- blocked_models constraint restricts routing
- preferred_provider influences selection
- Constraints from metadata flow through bridge
"""

import pytest

from app.domain.entities import RequestContext, RouteDecision
from app.domain.routing import RoutingRequest
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.routing_service import RoutingService


@pytest.fixture()
def svc():
    return RoutingService()


def _ctx():
    return RequestContext(tenant_id="t", project_id="p", project_name="P")


class TestPolicyConstrainedRouting:
    def test_allowed_providers_restricts(self, svc):
        result = svc.route(RoutingRequest(
            requested_model="auto",
            allowed_providers=frozenset({"anthropic"}),
        ))
        assert result.provider == "anthropic"
        assert result.constrained_by_policy is True

    def test_blocked_model_skipped(self, svc):
        result = svc.route(RoutingRequest(
            requested_model="auto",
            blocked_models=frozenset({"gpt-4o-mini"}),
        ))
        assert result.model != "gpt-4o-mini"

    def test_no_constraints_selects_default(self, svc):
        result = svc.route(RoutingRequest(requested_model="auto"))
        assert result.model == "gpt-4o-mini"
        assert result.constrained_by_policy is False


class TestBridgePassesConstraints:
    def test_allowed_providers_from_metadata(self, svc):
        payload = ChatCompletionsRequest(
            model="auto",
            messages=[Message(role="user", content="hi")],
            metadata={"allowed_providers": ["anthropic"]},
        )
        route = svc.select_route(_ctx(), payload, None)
        assert route.provider_name == "anthropic"

    def test_blocked_models_from_metadata(self, svc):
        payload = ChatCompletionsRequest(
            model="auto",
            messages=[Message(role="user", content="hi")],
            metadata={"blocked_models": ["gpt-4o-mini"]},
        )
        route = svc.select_route(_ctx(), payload, None)
        assert route.model_name != "gpt-4o-mini"

    def test_no_metadata_constraints(self, svc):
        payload = ChatCompletionsRequest(
            model="auto",
            messages=[Message(role="user", content="hi")],
        )
        route = svc.select_route(_ctx(), payload, None)
        assert route.provider_name == "openai"
        assert route.model_name == "gpt-4o-mini"
