"""Masking follows where the data goes — the dispatcher's own check (``in_house_only``).

Text that was not masked because it was going to an in-house model can be handed to a provider in exactly
one place: ``ProviderDispatcher.dispatch_with_fallback``. So the rule is held there and not only by the
caller's decision: a primary outside the organisation is refused before anything is sent, and a hop to an
outside provider is never a candidate. (The real dispatcher — the conftest's stand-in is switched off for
this file, as tests/test_fix120_orchestrator.py does.)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.exceptions import ProviderUnavailableError
from app.domain.entities import RouteDecision
from app.domain.providers import ProviderResponse
from app.services.gateway import fallback_policy as fp
from app.services.provider_dispatcher import ProviderDispatcher


@pytest.fixture(autouse=True)
def _mock_provider_dispatch():
    yield


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    def boom():
        raise ConnectionError("no redis in unit tests")

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", boom)
    monkeypatch.setattr(fp, "_read_setting", lambda key: None)
    fp.clear_cache()
    yield
    fp.clear_cache()


def _payload():
    p = MagicMock()
    p.model, p.metadata, p.messages, p.tools = "m", {}, [], None
    return p


class _Adapter:
    def __init__(self, exc=None):
        self.exc, self.calls = exc, 0

    async def chat(self, *a, **k):
        self.calls += 1
        if self.exc:
            raise self.exc
        return ProviderResponse(content="ok", model="m", finish_reason="stop")


@pytest.fixture
def dispatcher(monkeypatch):
    sent: list[str] = []

    async def one(self, *, route, provider, model, payload, messages, first_key, key_resolver, invalidate_key):
        sent.append(provider)
        return await self.adapters[provider].chat()

    monkeypatch.setattr(ProviderDispatcher, "_dispatch_one", one)
    d = ProviderDispatcher()
    d.sent = sent
    return d


async def _go(d, route, **kw):
    return await d.dispatch_with_fallback(route=route, payload=_payload(), messages=[], api_key="k", key_resolver=lambda p, m: "k", **kw)


@pytest.mark.asyncio
async def test_a_primary_outside_the_organisation_is_refused_before_anything_is_sent(dispatcher):
    outside = _Adapter()
    dispatcher.adapters = {"openai": outside}
    route = RouteDecision(provider_name="openai", model_name="gpt-4o")
    with pytest.raises(Exception) as refused:
        await _go(dispatcher, route, in_house_only=True)
    assert getattr(refused.value, "code", "") == "in_house_route_refused"
    assert outside.calls == 0 and dispatcher.sent == []
    # the same route without the promise is an ordinary dispatch
    _, p, _ = await _go(dispatcher, route)
    assert p == "openai" and outside.calls == 1


@pytest.mark.asyncio
async def test_a_hop_to_an_outside_provider_is_not_a_candidate_for_unmasked_text(dispatcher):
    down, outside = _Adapter(ProviderUnavailableError("down")), _Adapter()
    dispatcher.adapters = {"sranai": down, "openai": outside}
    route = RouteDecision(provider_name="sranai", model_name="qwen3.8-27b-fp8")
    route.fallbacks = [("openai", "gpt-4o")]
    # masked text may hop (this is the behaviour the promise switches off)
    _, p, _ = await _go(dispatcher, route)
    assert p == "openai" and outside.calls == 1
    # unmasked text: the in-house model's own error comes back and nothing outside was called
    outside.calls, dispatcher.sent[:] = 0, []
    with pytest.raises(ProviderUnavailableError):
        await _go(dispatcher, route, in_house_only=True)
    assert outside.calls == 0 and dispatcher.sent == ["sranai"]


@pytest.mark.asyncio
async def test_an_in_house_primary_answers_as_usual(dispatcher):
    inside = _Adapter()
    dispatcher.adapters = {"sranai": inside}
    _, p, m = await _go(dispatcher, RouteDecision(provider_name="sranai", model_name="qwen3.8-27b-fp8"), in_house_only=True)
    assert (p, m) == ("sranai", "qwen3.8-27b-fp8") and inside.calls == 1
