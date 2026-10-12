"""Nothing goes to a recipient the caller did not name (app/services/providers/recipients.py).

A provider row is a route; behind a router the company that RUNS the model is a second party — and it is
not always the one that made it (13 Oct 2026, with this deployment's key: Claude was run by Amazon
Bedrock, GPT by Azure). A caller that names its recipients (``metadata.allowed_recipients``) is held to
them, and what is held here is the refusal — each time, that the provider was not called at all:

* no company that can run the model is on the list — by route, and by company;
* the maker is not the host: naming Anthropic does not let Amazon run its model, and the other way;
* a list that is empty, null or unreadable: nobody outside;
* a model no company can be said to run can be on no list;
* an in-house provider is not a recipient and is always reached;
* with no list, nothing changes — not the refusal, not the request sent to the router;
* behind OpenRouter the request is pinned to the endpoints of the companies named, and to no other's,
  with no fallback and no endpoint that keeps what it is sent.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import NoAllowedProviderError
from app.domain.entities import RouteDecision
from app.domain.providers import ProviderResponse
from app.main import app
from app.schemas.public import ChatCompletionsRequest, Message
from app.services.provider_dispatcher import ProviderDispatcher
from app.services.providers import recipients
from app.services.providers.openrouter_adapter import OpenRouterAdapter

# (taken when this file is read: conftest replaces the method for every test, so no test of the suite
# ever reaches a provider — and this file must reach the real one to see it refuse)
REAL_DISPATCH = ProviderDispatcher.__dict__["dispatch"]

client = TestClient(app, raise_server_exceptions=False)
KEY = {"Authorization": "Bearer demo-key"}

GEMINI = ("openrouter", "google/gemini-2.5-flash")
CLAUDE = ("openrouter", "anthropic/claude-sonnet-4.5")
GPT = ("openrouter", "openai/gpt-4o")
INSIDE = ("sranai", "qwen3.8-27b-fp8")
PIN = {"allow_fallbacks": False, "data_collection": "deny"}


def _payload(model: str, meta: dict | None = None, **kw) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(model=model, messages=[Message(role="user", content="hello")], metadata=meta or {}, **kw)


class _Spy:
    """An adapter that records that it was called."""

    def __init__(self):
        self.calls: list[str] = []

    async def chat(self, *, payload, messages, resolved_model, api_key=None):
        self.calls.append(resolved_model)
        return ProviderResponse(content="ok", model=resolved_model)


# ---------------------------------------------------------------------------
# who the recipient is
# ---------------------------------------------------------------------------


def test_a_direct_route_is_its_own_company():
    assert recipients.keys_of("deepseek", "deepseek-v4-flash") == ["deepseek"]
    assert recipients.keys_of("google", "gemini-2.5-flash") == ["google"]


def test_behind_the_router_a_model_has_a_recipient_for_each_company_that_can_run_it():
    assert recipients.keys_of(*GEMINI) == ["openrouter:google"]
    assert set(recipients.keys_of(*CLAUDE)) == {"openrouter:anthropic", "openrouter:amazon", "openrouter:google"}
    assert set(recipients.keys_of(*GPT)) == {"openrouter:openai", "openrouter:microsoft"}


def test_the_direct_route_and_the_routed_one_are_different_recipients():
    assert recipients.keys_of("google", "gemini-2.5-flash") != recipients.keys_of(*GEMINI)


@pytest.mark.parametrize("model", ["openrouter/auto", "deepseek/deepseek-chat", "meta-llama/llama-3.3-70b-instruct",
                                   "some-maker/some-model", "no-slash-at-all", ""])
def test_a_model_no_company_can_be_said_to_run_has_no_key(model):
    # (deepseek's and meta's models were answered by DeepInfra, Novita and Parasail: whoever hosts them that day)
    assert recipients.keys_of("openrouter", model) == []


def test_an_in_house_provider_is_not_a_recipient():
    (r,) = recipients.recipients_of(*INSIDE)
    assert r.in_house and r.key == ""
    assert recipients.keys_of(*INSIDE) == []


# ---------------------------------------------------------------------------
# the list
# ---------------------------------------------------------------------------


def test_no_list_is_not_a_list_and_an_empty_one_is():
    assert recipients.asked({}) is None
    assert recipients.asked(None) is None
    assert recipients.asked({"allowed_providers": ["openrouter"]}) is None
    assert recipients.asked({"allowed_recipients": []}) == frozenset()


@pytest.mark.parametrize("value", [None, "openrouter:google", 7, b"openrouter:google"])
def test_a_list_that_is_null_or_cannot_be_read_allows_nobody(value):
    # (null is what an empty list becomes in more than one language's JSON: it must never mean "did not ask")
    assert recipients.asked({"allowed_recipients": value}) == frozenset()
    with pytest.raises(NoAllowedProviderError):
        recipients.require(*GEMINI, {"allowed_recipients": value})


def test_with_no_list_every_call_goes_as_before():
    for provider, model in (GEMINI, CLAUDE, INSIDE, ("openrouter", "openrouter/auto"), ("deepseek", "deepseek-v4-flash")):
        recipients.require(provider, model, {})
        recipients.require(provider, model, {"allowed_providers": ["sranai"]})


def test_a_recipient_on_the_list_is_reached_and_one_that_is_not_is_refused():
    meta = {"allowed_recipients": ["openrouter:google"]}
    recipients.require(*GEMINI, meta)
    with pytest.raises(NoAllowedProviderError) as e:
        recipients.require(*GPT, meta)
    assert e.value.details["reason"] == "recipient_not_allowed"
    assert set(e.value.details["recipients"]) == {"openrouter:openai", "openrouter:microsoft"}
    assert e.value.details["allowed_recipients"] == ["openrouter:google"]
    assert "Nothing was sent" in str(e.value)


def test_the_maker_is_not_the_host():
    # Amazon named: Anthropic's model may be run by Amazon — and the pin lets nobody else run it
    recipients.require(*CLAUDE, {"allowed_recipients": ["openrouter:amazon"]})
    assert recipients.router_pin(*CLAUDE, {"allowed_recipients": ["openrouter:amazon"]}) == {"only": ["amazon-bedrock"], **PIN}
    # Anthropic named: Amazon may not
    assert recipients.router_pin(*CLAUDE, {"allowed_recipients": ["openrouter:anthropic"]}) == {"only": ["anthropic"], **PIN}
    # Microsoft named: OpenAI's model may be run by Azure, Anthropic's by nobody
    recipients.require(*GPT, {"allowed_recipients": ["openrouter:microsoft"]})
    assert recipients.router_pin(*GPT, {"allowed_recipients": ["openrouter:microsoft"]}) == {"only": ["azure"], **PIN}
    with pytest.raises(NoAllowedProviderError):
        recipients.require(*CLAUDE, {"allowed_recipients": ["openrouter:microsoft", "openrouter:openai"]})


def test_the_route_alone_is_not_enough_and_neither_is_the_company_alone():
    for listed in (["openrouter"], ["google"], ["openrouter:"], [":google"], ["OPENROUTER:GOOGLE "]):
        with pytest.raises(NoAllowedProviderError):
            recipients.require(*GEMINI, {"allowed_recipients": listed})
    # … and the company reached directly is not the company reached through the router
    with pytest.raises(NoAllowedProviderError):
        recipients.require("google", "gemini-2.5-flash", {"allowed_recipients": ["openrouter:google"]})


def test_an_empty_list_reaches_nobody_outside_and_still_reaches_the_in_house_model():
    meta = {"allowed_recipients": []}
    recipients.require(*INSIDE, meta)
    for provider, model in (GEMINI, CLAUDE, GPT, ("deepseek", "deepseek-v4-flash")):
        with pytest.raises(NoAllowedProviderError):
            recipients.require(provider, model, meta)


def test_a_model_no_company_can_be_said_to_run_is_refused_whatever_the_list_holds():
    everything = ["openrouter:google", "openrouter:anthropic", "openrouter:amazon", "openrouter:openai", "openrouter:microsoft",
                  "openrouter", "openrouter:", "openrouter:deepseek", "openrouter:meta", ""]
    for model in ("openrouter/auto", "deepseek/deepseek-chat", "meta-llama/llama-3.3-70b-instruct"):
        with pytest.raises(NoAllowedProviderError) as e:
            recipients.require("openrouter", model, {"allowed_recipients": everything})
        assert e.value.details["reason"] == "recipient_cannot_be_named"


# ---------------------------------------------------------------------------
# where a provider is called: it is not called
# ---------------------------------------------------------------------------


def _dispatcher() -> tuple[ProviderDispatcher, dict[str, _Spy]]:
    d = ProviderDispatcher()
    spies = {"openrouter": _Spy(), "sranai": _Spy(), "deepseek": _Spy()}
    d.adapters = dict(spies)
    return d, spies


@pytest.mark.asyncio
async def test_the_dispatcher_does_not_call_a_provider_that_is_not_on_the_list():
    d, spies = _dispatcher()
    route = RouteDecision(provider_name="openrouter", model_name=GPT[1])
    payload = _payload(GPT[1], {"allowed_recipients": ["openrouter:google"]})
    with pytest.raises(NoAllowedProviderError):
        await REAL_DISPATCH(d, route, payload, payload.messages)
    assert spies["openrouter"].calls == [], "a provider that was not named was called"


@pytest.mark.asyncio
async def test_the_dispatcher_calls_a_named_recipient_and_the_in_house_model():
    d, spies = _dispatcher()
    meta = {"allowed_recipients": ["openrouter:google"]}
    await REAL_DISPATCH(d, RouteDecision(provider_name="openrouter", model_name=GEMINI[1]), _payload(GEMINI[1], meta), _payload("x").messages)
    await REAL_DISPATCH(d, RouteDecision(provider_name="sranai", model_name=INSIDE[1]), _payload(INSIDE[1], {"allowed_recipients": []}),
                        _payload("x").messages)
    assert spies["openrouter"].calls == [GEMINI[1]]
    assert spies["sranai"].calls == [INSIDE[1]]


@pytest.mark.asyncio
async def test_a_second_route_and_a_rescue_are_held_by_the_same_line():
    # every non-stream call leaves through dispatch(): a hop is refused there like a first attempt
    d, spies = _dispatcher()
    payload = _payload(GEMINI[1], {"allowed_recipients": ["openrouter:google"]})
    for provider, model in (("deepseek", "deepseek-v4-flash"), GPT, ("openrouter", "openrouter/auto")):
        with pytest.raises(NoAllowedProviderError):
            await REAL_DISPATCH(d, RouteDecision(provider_name=provider, model_name=model), payload, payload.messages)
    assert spies["deepseek"].calls == [] and spies["openrouter"].calls == []


@pytest.mark.asyncio
async def test_the_boundary_refuses_the_primary_before_a_stream_begins_and_drops_hops(monkeypatch):
    from app.services.gateway import fallback_policy as fp
    from app.services.routing_service import enforce_provider_boundary

    async def _no_tenant_policy(tenant_id):
        return {}

    monkeypatch.setattr(fp, "effective_policy_async", _no_tenant_policy)
    meta = {"allowed_recipients": ["openrouter:google"]}
    with pytest.raises(NoAllowedProviderError):
        await enforce_provider_boundary(RouteDecision(provider_name="openrouter", model_name=GPT[1]), _payload(GPT[1], meta), "t")
    route = RouteDecision(provider_name="openrouter", model_name=GEMINI[1],
                          fallbacks=[GPT, INSIDE, ("deepseek", "deepseek-v4-flash"), ("openrouter", "google/gemini-2.5-pro")])
    await enforce_provider_boundary(route, _payload(GEMINI[1], meta), "t")
    assert route.fallbacks == [INSIDE, ("openrouter", "google/gemini-2.5-pro")]


def test_the_streaming_path_asks_before_it_calls_the_provider():
    from app.api.routes import public_chat

    src = inspect.getsource(public_chat)
    call = src.index("lambda: stream_adapter.stream_chat(")
    ask = src.rindex("_recipients.require(route.provider_name, route.model_name, payload.metadata)", 0, call)
    assert call - ask < 600, "the streaming call is not asked right before it is made"


def test_http_a_request_that_names_nobody_is_400_and_no_provider_is_called(monkeypatch):
    calls: list[str] = []
    stubbed = ProviderDispatcher.dispatch

    async def _spy(self, route, *a, **k):
        calls.append(route.provider_name)
        return await stubbed(self, route, *a, **k)

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _spy)
    for stream in (False, True):
        r = client.post("/v1/chat/completions", headers=KEY, json={
            "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": stream,
            "metadata": {"allowed_recipients": []}})
        assert r.status_code == 400, r.text
        assert "no_allowed_provider" in r.text
    assert calls == []


def test_http_the_same_request_with_no_list_is_served(monkeypatch):
    r = client.post("/v1/chat/completions", headers=KEY, json={
        "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "metadata": {}})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# behind the router: pinned
# ---------------------------------------------------------------------------


def test_openrouter_is_pinned_to_the_endpoints_of_the_companies_named():
    a = OpenRouterAdapter()
    p = _payload(GEMINI[1], {"allowed_recipients": ["openrouter:google"]})
    body = a._build_body(p, p.messages, resolved_model=GEMINI[1])
    assert body["provider"] == {"only": ["google-ai-studio", "google-vertex"], **PIN}
    # two companies named that can run the model: the endpoints of both, and of no third
    p = _payload(CLAUDE[1], {"allowed_recipients": ["openrouter:amazon", "openrouter:google", "openrouter:microsoft"]})
    assert a._build_body(p, p.messages, resolved_model=CLAUDE[1])["provider"] == {"only": ["amazon-bedrock", "google-vertex"], **PIN}


def test_a_company_s_endpoints_are_its_own_and_no_other_s():
    hosts = recipients.OPENROUTER_HOSTS
    assert hosts["anthropic"]["anthropic"] == ("anthropic",)
    assert hosts["anthropic"]["amazon"] == ("amazon-bedrock",)
    assert hosts["openai"]["openai"] == ("openai",)
    assert hosts["openai"]["microsoft"] == ("azure",)
    for maker, companies in hosts.items():
        seen: dict[str, str] = {}
        for company, slugs in companies.items():
            for s in slugs:
                assert s not in seen, f"{maker}: the endpoint {s} is listed for {seen.get(s)} and for {company}"
                seen[s] = company
    # an open model's hosts change by the day: no entry, so no key
    assert "deepseek" not in hosts and "meta-llama" not in hosts


def test_with_no_list_the_request_to_openrouter_is_unchanged():
    a = OpenRouterAdapter()
    p = _payload(GEMINI[1], {"allowed_providers": ["openrouter"]})
    assert "provider" not in a._build_body(p, p.messages, resolved_model=GEMINI[1])


# ---------------------------------------------------------------------------
# the catalogue says who the recipients are
# ---------------------------------------------------------------------------


def test_the_catalogue_names_each_model_s_recipients_and_none_for_an_in_house_model(monkeypatch):
    from app.services.provider_key_service import ProviderKeyService
    from app.services.providers.model_registry import get_all_candidates

    # (the catalogue lists a provider only while it holds a key that works; here every provider does)
    every = {c.provider for c in get_all_candidates()}
    monkeypatch.setattr(ProviderKeyService, "providers_with_valid_keys", lambda self, session: set(every))
    r = client.get("/v1/models")
    assert r.status_code == 200, r.text
    seen = set()
    for m in r.json()["data"]:
        if m["provider"] == "gateway":  # (the gateway's own "auto": not a model that is sent anything)
            continue
        assert m.get("recipients") == (recipients.keys_of(m["provider"], m["id"]) or None), m
        assert "recipient" not in m and "recipient_company" not in m
        seen.add(m["provider"])
    assert {"openai", "anthropic", "openrouter"} <= seen, seen
