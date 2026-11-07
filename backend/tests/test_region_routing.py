"""PR-04: Region enforcement in routing tests."""

import pytest

from app.domain.entities import RequestContext, RouteDecision
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.routing_service import RoutingService


def _ctx(region="global"):
    return RequestContext(
        tenant_id="t1", project_id="p1", project_name="P", region=region
    )


def _payload(model="auto"):
    return ChatCompletionsRequest(
        model=model, messages=[Message(role="user", content="hi")]
    )


class TestRegionEnforcement:
    def test_global_region_no_restrictions(self):
        svc = RoutingService()
        route = svc.select_route(_ctx("global"), _payload(), None)
        assert route.provider_name in {"openai", "anthropic", "openrouter"}

    def test_eu_region_blocks_openrouter(self):
        svc = RoutingService()
        route = svc.select_route(_ctx("eu"), _payload(), None)
        assert route.provider_name != "openrouter"

    def test_eu_region_explicit_openrouter_model_still_blocked(self):
        # OpenRouter model — but EU blocks the openrouter provider, and no
        # provider the region allows owns the model. FIX126 O4: refused
        # (fail-closed). It used to "fall back" to openai, which does not
        # serve this model — the request left for a 404, and the region
        # boundary held only by accident.
        from app.core.exceptions import NoAllowedProviderError

        svc = RoutingService()
        with pytest.raises(NoAllowedProviderError) as ei:
            svc.select_route(_ctx("eu"), _payload("meta-llama/llama-3-70b"), None)
        assert "openrouter" not in ei.value.details["allowed_providers"]

    def test_us_region_no_restrictions(self):
        svc = RoutingService()
        route = svc.select_route(_ctx("us"), _payload(), None)
        assert route.provider_name in {"openai", "anthropic", "openrouter"}

    def test_region_in_request_context(self):
        ctx = _ctx("eu")
        assert ctx.region == "eu"
