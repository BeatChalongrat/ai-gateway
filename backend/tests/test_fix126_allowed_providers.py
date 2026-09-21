"""FIX126 O4 — an allow-list that matches no provider is refused.

Before: ``metadata.allowed_providers`` (or a tenant's fallback-policy
``allowed_providers``) naming no provider that could serve the request
logged ``routing_no_compatible_provider`` and dispatched to the default
provider (openai) anyway — the request body left for a provider the caller
had excluded (FIX122 finding). Now: 400 ``no_allowed_provider`` and no
provider is called. An empty / absent list behaves as before.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import error_catalog
from app.core.exceptions import NoAllowedProviderError
from app.domain.entities import RequestContext, RouteDecision
from app.main import app
from app.schemas.public import ChatCompletionsRequest, Message
from app.services.provider_dispatcher import ProviderDispatcher
from app.services.providers.openai_adapter import OpenAIAdapter
from app.services.routing_service import RoutingService

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _body(model: str, allowed, *, stream: bool = False) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {} if allowed is None else {"allowed_providers": allowed},
        "stream": stream,
    }


@pytest.fixture
def dispatch_spy(monkeypatch):
    """Records every provider call (non-stream dispatch + openai stream);
    the real call still runs."""
    calls: list[str] = []
    # conftest's autouse stubs replace ``dispatch`` (which the stubbed
    # ``dispatch_with_fallback`` delegates to) and the adapters'
    # ``stream_chat``; wrap whatever is installed so the spy sees every
    # provider call on both paths.
    real_dispatch = ProviderDispatcher.dispatch

    async def _dispatch(self, route, *args, **kwargs):
        calls.append(route.provider_name)
        return await real_dispatch(self, route, *args, **kwargs)

    real_stream = OpenAIAdapter.stream_chat

    async def _stream(self, *a, **k):
        calls.append("openai:stream")
        async for chunk in real_stream(self, *a, **k):
            yield chunk

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _dispatch)
    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _stream)
    return calls


def _ctx() -> RequestContext:
    return RequestContext(tenant_id="t", project_id="p", project_name="P", region="global")


def _payload(model: str, allowed) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="hi")],
        metadata={} if allowed is None else {"allowed_providers": allowed},
    )


# ---------------------------------------------------------------------------
# Routing engine
# ---------------------------------------------------------------------------


def test_router_refuses_allow_list_matching_nothing():
    with pytest.raises(NoAllowedProviderError) as ei:
        RoutingService().select_route(_ctx(), _payload("gpt-4o-mini", ["nonexistent"]), None)
    assert ei.value.code == "no_allowed_provider"
    assert ei.value.details["allowed_providers"] == ["nonexistent"]


def test_router_refuses_model_no_allowed_provider_owns():
    with pytest.raises(NoAllowedProviderError):
        RoutingService().select_route(
            _ctx(), _payload("claude-3-5-sonnet-20241022", ["openai"]), None
        )


def test_router_keeps_a_matching_allow_list():
    route = RoutingService().select_route(_ctx(), _payload("deepseek-chat", ["deepseek"]), None)
    assert (route.provider_name, route.model_name) == ("deepseek", "deepseek-chat")


def test_router_without_allow_list_is_unchanged():
    for allowed in (None, []):
        route = RoutingService().select_route(_ctx(), _payload("gpt-4o-mini", allowed), None)
        assert route.provider_name == "openai"


@pytest.mark.asyncio
async def test_tenant_boundary_refuses_primary_outside_it(monkeypatch):
    from app.services.gateway import fallback_policy as fp

    async def _pol(tenant_id):
        return {"allowed_providers": ["sranai"]}

    monkeypatch.setattr(fp, "effective_policy_async", _pol)
    from app.services.routing_service import enforce_provider_boundary

    route = RoutingService().select_route(_ctx(), _payload("gpt-4o-mini", None), None)
    with pytest.raises(NoAllowedProviderError):
        await enforce_provider_boundary(route, _payload("gpt-4o-mini", None), "t")


@pytest.mark.asyncio
async def test_boundary_keeps_primary_inside_and_drops_hops_outside(monkeypatch):
    from app.services.gateway import fallback_policy as fp

    async def _pol(tenant_id):
        return {}

    monkeypatch.setattr(fp, "effective_policy_async", _pol)
    from app.services.routing_service import enforce_provider_boundary

    route = RouteDecision(
        provider_name="deepseek",
        model_name="deepseek-chat",
        fallbacks=[("openai", "gpt-4o-mini"), ("deepseek", "deepseek-coder")],
    )
    await enforce_provider_boundary(route, _payload("deepseek-chat", ["deepseek"]), "t")
    assert route.fallbacks == [("deepseek", "deepseek-coder")]


# ---------------------------------------------------------------------------
# HTTP: nothing is dispatched on a refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["gpt-4o-mini", "auto"])
def test_http_nonexistent_allow_list_is_400_and_not_dispatched(model, dispatch_spy):
    resp = client.post("/v1/chat/completions", headers=_HEADERS, json=_body(model, ["nonexistent"]))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "no_allowed_provider"
    assert dispatch_spy == []


def test_http_stream_nonexistent_allow_list_is_400_and_not_dispatched(dispatch_spy):
    resp = client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json=_body("gpt-4o-mini", ["nonexistent"], stream=True),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "no_allowed_provider"
    assert dispatch_spy == []


def test_http_matching_allow_list_is_still_served(dispatch_spy):
    resp = client.post("/v1/chat/completions", headers=_HEADERS, json=_body("gpt-4o-mini", ["openai"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["provider"] == "openai"
    assert dispatch_spy and set(dispatch_spy) == {"openai"}


def test_error_catalog_names_the_code_as_not_retryable():
    info = error_catalog.describe("no_allowed_provider")
    assert info.code == "no_allowed_provider"
    assert info.retryable is False
