"""P2-08: Model registry and routing with context tests.

Verifies:
- Exact model name resolution
- Prefix-based resolution
- Unknown model returns None
- Routing service uses registry for explicit models
- Auto-routing still works
- OpenRouter prefix models resolve correctly
"""

import pytest

from app.domain.entities import RequestContext, RouteDecision
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers.model_registry import list_models, resolve_provider
from app.services.routing_service import RoutingService


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


class TestResolveProvider:
    def test_exact_openai_model(self):
        assert resolve_provider("gpt-4o") == "openai"
        assert resolve_provider("gpt-4o-mini") == "openai"

    def test_exact_anthropic_model(self):
        assert resolve_provider("claude-3-5-sonnet-20241022") == "anthropic"
        assert resolve_provider("claude-3-opus-20240229") == "anthropic"

    def test_prefix_openai(self):
        assert resolve_provider("gpt-4-turbo-2024-04-09") == "openai"

    def test_prefix_anthropic(self):
        assert resolve_provider("claude-3-5-haiku-20250107") == "anthropic"

    def test_prefix_openrouter(self):
        assert resolve_provider("openai/gpt-4o") == "openrouter"
        assert resolve_provider("anthropic/claude-3.5-sonnet") == "openrouter"
        assert resolve_provider("meta-llama/llama-3-70b") == "openrouter"

    def test_unknown_model(self):
        assert resolve_provider("some-random-model") is None

    def test_list_models_returns_list(self):
        models = list_models()
        assert isinstance(models, list)
        assert len(models) > 0
        assert all("model" in m and "provider" in m for m in models)


# ---------------------------------------------------------------------------
# Routing service with registry
# ---------------------------------------------------------------------------


def _ctx():
    return RequestContext(
        tenant_id="t1", project_id="p1", project_name="P"
    )


def _payload(model="auto", **metadata):
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="test")],
        metadata=metadata,
    )


class TestRoutingWithRegistry:
    def test_explicit_openai_model(self):
        svc = RoutingService()
        route = svc.select_route(_ctx(), _payload("gpt-4o-mini"), None)
        assert route.provider_name == "openai"
        assert route.model_name == "gpt-4o-mini"

    def test_explicit_anthropic_model(self):
        svc = RoutingService()
        route = svc.select_route(
            _ctx(), _payload("claude-3-5-sonnet-20241022"), None
        )
        assert route.provider_name == "anthropic"

    def test_explicit_openrouter_model(self):
        svc = RoutingService()
        route = svc.select_route(
            _ctx(), _payload("meta-llama/llama-3-70b"), None
        )
        assert route.provider_name == "openrouter"

    def test_unknown_model_falls_back_to_openai(self):
        svc = RoutingService()
        route = svc.select_route(_ctx(), _payload("some-unknown-model"), None)
        assert route.provider_name == "openai"

    def test_auto_general_routes_to_openai(self):
        svc = RoutingService()
        route = svc.select_route(_ctx(), _payload("auto"), None)
        assert route.provider_name == "openai"
        assert route.model_name == "gpt-4o-mini"

    def test_auto_coding_routes_to_anthropic(self):
        svc = RoutingService()
        route = svc.select_route(
            _ctx(), _payload("auto", use_case="coding"), None
        )
        assert route.provider_name == "anthropic"
