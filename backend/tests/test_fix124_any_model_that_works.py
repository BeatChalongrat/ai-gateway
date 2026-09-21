"""FIX124 — any model that works; the person never gets an empty answer.

Two live failures, both measured:

* a conversation pinned to an EXPLICIT model that has no chain
  (``qwen3.8-27b-fp8``) got one attempt and a red card, because an empty
  chain meant "nothing to fall back to" on both paths;
* an Auto turn did walk, and still failed: the 60-second health marker had
  expired so the dead box was picked again, the router's five alternatives
  were all one provider, and the streaming walk went from a rate-limited
  account to the SAME account (the FIX51 account scope was applied by the
  non-streaming walk only).

Pinned here: the explicit walk continues with the Auto router's list (a
switch in the policy file, inside the boundary, under the kill switch), the
streaming walk rules out a failed account, the alternatives span providers,
the marker's cooldown escalates and resets, and the attempts a person is
shown are the models that were tried.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import (
    NoCapableModelError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitExceededError,
)
from app.domain.entities import RequestContext
from app.domain.providers import ProviderResponse, ProviderStreamChunk
from app.domain.routing import ModelCandidate
from app.services.auto_router import router
from app.services.gateway import fallback_orchestrator as fo
from app.services.gateway import fallback_policy as fp
from app.services.gateway.fallback_orchestrator import FallbackOrchestrator

BACKEND = Path(__file__).resolve().parents[1]

# The live shape: the in-house model has no chain of its own.
PRIMARY = ("sranai", "qwen3.8-27b-fp8")
PROVIDER_OF = {
    "qwen3.8-27b-fp8": "sranai",
    "Typhoon-S-ThaiLLM-8B-Instruct": "sranai",
    "gpt-5-mini": "openai",
    "gpt-5-nano": "openai",
    "gpt-5.6-sol": "openai",
    "deepseek-v4-pro": "deepseek",
    "gpt-4o": "openai",
}
# What the Auto router would offer for the same request.
ROUTER_LIST = ["gpt-5-mini", "gpt-5-nano", "deepseek-v4-pro", "Typhoon-S-ThaiLLM-8B-Instruct"]


def _c(provider, model, *, tools=True, price=1.0):
    return ModelCandidate(
        provider=provider, model=model, supports_streaming=True, supports_tools=tools,
        supports_vision=False, supports_reasoning=False, cost_tier="standard",
        cost_per_input_token=price / 1_000_000,
    )


def _plan(cands) -> router.RoutePlan:
    return router.RoutePlan(
        primary=cands[0], fallbacks=list(cands[1:]), task="chat", task_confidence=1.0,
        tier="medium", reason="test", candidates_count=len(cands), filtered_count=len(cands),
    )


class _Log:
    def __init__(self):
        self.events: dict[str, dict] = {}

    def info(self, ev, **k):
        self.events[ev] = k

    def warning(self, ev, **k):
        self.events[ev] = k

    def bind(self, **k):
        return self


class _Keys:
    def __init__(self, keys_for):
        self.keys_for = set(keys_for)

    def get_api_key(self, session, provider, model=None):
        return f"sk-{provider}" if provider in self.keys_for else None

    async def get_api_key_async(self, session, provider, model=None):
        return self.get_api_key(session, provider, model)


class _Dispatcher:
    """Every candidate answers unless its model is in ``fail``."""

    def __init__(self, fail: dict[str, Exception] | None = None):
        self.fail = fail or {}
        self.calls: list[tuple[str, str]] = []
        self.adapters = {p: MagicMock(supports_streaming=True) for p in ("openai", "deepseek", "sranai", "anthropic", "google", "openrouter")}

    async def dispatch(self, *, route, payload, messages, api_key):
        self.calls.append((route.provider_name, route.model_name))
        if route.model_name in self.fail:
            raise self.fail[route.model_name]
        return ProviderResponse(content="ok", model=route.model_name, finish_reason="stop")


class _Redis:
    """Enough of Redis for the marker, its strike counter and the pool filter."""

    def __init__(self):
        self.store: dict[str, object] = {}
        self.ttl: dict[str, int | None] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttl[key] = ex

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def get(self, key):
        return self.store.get(key)

    def expire_now(self, key):
        """The cooldown ran out (what the TTL does in the real thing)."""
        self.store.pop(key, None)

    async def expire(self, key, seconds):
        self.ttl[key] = seconds

    async def delete(self, key):
        self.store.pop(key, None)
        self.ttl.pop(key, None)

    async def exists(self, key):
        return 1 if key in self.store else 0

    def pipeline(self, transaction=True):
        redis = self

        class _Pipe:
            def __init__(self):
                self.keys: list[str] = []

            def exists(self, key):
                self.keys.append(key)
                return self

            async def execute(self):
                return [1 if k in redis.store else 0 for k in self.keys]

        return _Pipe()


def _ctx(tenant="tenant-x") -> RequestContext:
    return RequestContext(tenant_id=tenant, project_id="proj-x", project_name="Test", request_id="req_unit", turn_id="turn-124")


def _payload(metadata=None, model=PRIMARY[1]):
    p = MagicMock()
    p.model = model
    p.metadata = dict(metadata or {})
    p.messages = []
    p.tools = None
    return p


def _runner(fail=None, keys=("openai", "deepseek", "sranai"), **kw):
    disp = _Dispatcher(fail)
    return FallbackOrchestrator(disp, _Keys(keys), **kw), disp


def _select(runner, original=PRIMARY, **kw):
    return runner.select_streaming_candidate(
        original_provider=original[0], original_model=original[1], session=MagicMock(), **kw
    )


@pytest.fixture
def settings(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(fp, "_read_setting", lambda key: store.get(key))
    fp.clear_cache()
    yield store
    fp.clear_cache()


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(
        "app.services.providers.model_registry.resolve_provider", lambda model: PROVIDER_OF.get(model)
    )


@pytest.fixture
def no_redis(monkeypatch):
    def boom():
        raise ConnectionError("no redis in unit tests")

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", boom)


@pytest.fixture
def fake_redis(monkeypatch):
    r = _Redis()
    monkeypatch.setattr("app.services._redis_pool.get_async_redis", lambda: r)
    return r


# ---------------------------------------------------------------------------
# The policy file carries the new rules as data
# ---------------------------------------------------------------------------

def test_the_rules_are_data_in_both_copies_of_the_policy_file():
    docs = BACKEND.parent / "docs" / "contracts" / "fallback_policy.json"
    if not docs.exists():  # the lane mounts the contracts at /docs
        docs = Path("/docs/contracts/fallback_policy.json")
    assert fp.packaged_policy_path().read_bytes() == docs.read_bytes()
    pol = fp.base_policy()
    assert pol["explicit_without_chain"]["rule"] == "auto_candidates"
    assert fp.explicit_without_chain_rule() == "auto_candidates"
    assert fp.health_cooldown_steps() == [60, 300, 900]
    # the first step is the cooldown that shipped, so one failure is as before
    assert fp.health_cooldown_steps()[0] == fp.health_cooldown_s() == 60
    assert "explicit_without_chain" in pol["overrides"]["overridable"]


def test_an_override_can_switch_the_explicit_rescue_off_and_nothing_else(settings):
    assert fp.validate_override({"explicit_without_chain": {"rule": "give_up"}}, is_tenant=False)
    for bad in ({"rule": "anything"}, {"rule": "auto_candidates", "extra": 1}, "auto_candidates"):
        with pytest.raises(ValueError):
            fp.validate_override({"explicit_without_chain": bad}, is_tenant=True)
    settings["fallback_policy"] = json.dumps({"explicit_without_chain": {"rule": "give_up"}})
    fp.clear_cache()
    assert fp.explicit_without_chain_rule(fp.effective_policy("tenant-x")) == "give_up"


# ---------------------------------------------------------------------------
# (7) the marker's cooldown escalates on the same pair and resets on success
# ---------------------------------------------------------------------------

def test_marker_cooldown_escalates_60_300_900_and_a_success_resets_it(fake_redis):
    key = "provider:health:sranai:qwen3.8-27b-fp8"
    seen = []
    for _ in range(4):
        # each failure comes after the previous cooldown ran out: the pair was
        # given another chance and was still dead
        fake_redis.expire_now(key)
        asyncio.run(fo.mark_provider_unhealthy(*PRIMARY, error_code="provider_timeout"))
        seen.append(fake_redis.ttl[key])
    assert seen == [60, 300, 900, 900]
    # the box-wide key stays a short hint: one model's history says nothing
    # about how long its siblings should be passed over
    assert fake_redis.ttl["provider:health:sranai"] == 60

    asyncio.run(fo.clear_provider_health(*PRIMARY))
    assert fake_redis.store == {}
    asyncio.run(fo.mark_provider_unhealthy(*PRIMARY, error_code="provider_timeout"))
    assert fake_redis.ttl[key] == 60


def test_a_burst_inside_one_cooldown_is_one_strike(fake_redis):
    key = "provider:health:sranai:qwen3.8-27b-fp8"
    for _ in range(5):  # five concurrent turns hit the same dead box
        asyncio.run(fo.mark_provider_unhealthy(*PRIMARY, error_code="rate_limit_exceeded"))
    assert fake_redis.ttl[key] == 60
    fake_redis.expire_now(key)
    asyncio.run(fo.mark_provider_unhealthy(*PRIMARY, error_code="rate_limit_exceeded"))
    asyncio.run(fo.mark_provider_unhealthy(*PRIMARY, error_code="rate_limit_exceeded"))
    assert fake_redis.ttl[key] == 300


def test_strikes_are_per_pair(fake_redis):
    for _ in range(3):
        fake_redis.expire_now("provider:health:sranai:qwen3.8-27b-fp8")
        asyncio.run(fo.mark_provider_unhealthy(*PRIMARY, error_code="rate_limit_exceeded"))
    asyncio.run(fo.mark_provider_unhealthy("sranai", "Typhoon-S-ThaiLLM-8B-Instruct", error_code="provider_error"))
    assert fake_redis.ttl["provider:health:sranai:qwen3.8-27b-fp8"] == 900
    assert fake_redis.ttl["provider:health:sranai:Typhoon-S-ThaiLLM-8B-Instruct"] == 60


def test_a_redis_that_cannot_count_still_marks_with_the_base_cooldown(fake_redis, monkeypatch):
    class _SetOnly:
        def __init__(self):
            self.ttl = {}

        async def set(self, key, value, ex=None):
            self.ttl[key] = ex

    r = _SetOnly()
    monkeypatch.setattr("app.services._redis_pool.get_async_redis", lambda: r)
    asyncio.run(fo.mark_provider_unhealthy(*PRIMARY, error_code="provider_error"))
    assert r.ttl == {"provider:health:sranai:qwen3.8-27b-fp8": 60}


# ---------------------------------------------------------------------------
# (1) Auto does not pick a pair the (escalated) marker says is dead — and a
# pool where everything is marked is still a pool
# ---------------------------------------------------------------------------

POOL = [
    _c("sranai", "qwen3.8-27b-fp8", price=0.0),
    _c("openai", "gpt-5-mini", price=0.25),
    _c("deepseek", "deepseek-v4-pro", price=0.3),
]


async def _auto(monkeypatch, pool=POOL):
    from app.services import routing_settings
    from app.services.auto_router import ranking as _ranking
    from app.services.orchestrator import GatewayOrchestrator
    from app.services.providers import model_registry

    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.provider_key_service = SimpleNamespace()  # session is None → not consulted
    monkeypatch.setattr(model_registry, "get_all_providers", lambda: sorted({c.provider for c in pool}))
    monkeypatch.setattr(model_registry, "get_all_candidates", lambda: list(pool))
    # the dead box is the tenant's favourite, as it was live ("tenant-ranked")
    monkeypatch.setattr(routing_settings, "get_auto_preference", lambda _s: [PRIMARY[1]])
    monkeypatch.setattr(_ranking, "get_ranking", lambda *_a, **_k: None)
    payload = SimpleNamespace(
        model="auto", routing_hints=None, reasoning=None, metadata=None, tools=None,
        messages=[{"role": "user", "content": "สวัสดี"}],
    )
    ctx = SimpleNamespace(tenant_id="t", project_id="p")
    return await orch._auto_route(ctx, payload, payload.messages, None, _Log())


def test_auto_skips_the_pair_a_third_strike_marked_for_fifteen_minutes(fake_redis, monkeypatch):
    assert asyncio.run(_auto(monkeypatch)).primary.model == PRIMARY[1]  # healthy: the favourite wins
    for _ in range(3):
        fake_redis.expire_now("provider:health:sranai:qwen3.8-27b-fp8")
        asyncio.run(fo.mark_provider_unhealthy(*PRIMARY, error_code="rate_limit_exceeded"))
    assert fake_redis.ttl["provider:health:sranai:qwen3.8-27b-fp8"] == 900
    plan = asyncio.run(_auto(monkeypatch))
    assert PRIMARY[1] not in [c.model for c in plan.ordered]
    assert plan.primary.provider != "sranai"


def test_auto_keeps_the_pool_when_every_candidate_is_marked(fake_redis, monkeypatch):
    for c in POOL:
        asyncio.run(fo.mark_provider_unhealthy(c.provider, c.model, error_code="provider_timeout"))
    plan = asyncio.run(_auto(monkeypatch))
    assert plan is not None and len(plan.ordered) == len(POOL)


# ---------------------------------------------------------------------------
# (8) the alternatives span providers
# ---------------------------------------------------------------------------

def test_alternatives_take_the_best_model_of_each_provider_first_then_fill():
    ranked = [
        _c("openai", "o-1"), _c("openai", "o-2"), _c("openai", "o-3"), _c("openai", "o-4"),
        _c("openai", "o-5"), _c("deepseek", "d-1"), _c("openrouter", "r-1"), _c("openai", "o-6"),
    ]
    out = [c.model for c in router.provider_diverse(ranked, limit=5)]
    # the router's order inside each group, one per provider first, length kept
    assert out == ["o-1", "d-1", "r-1", "o-2", "o-3"]
    assert [c.model for c in router.provider_diverse(ranked[:3], limit=5)] == ["o-1", "o-2", "o-3"]
    assert router.provider_diverse([], limit=5) == []


@pytest.mark.asyncio
async def test_the_list_the_router_hands_to_the_fallback_walk_is_provider_diverse(monkeypatch):
    from app.services.orchestrator import GatewayOrchestrator

    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    ranked = [_c("sranai", PRIMARY[1])] + [_c("openai", f"o-{i}") for i in range(1, 6)] + [_c("deepseek", "d-1")]

    async def fake_route(ctx, payload, dict_messages, session, log, *, reasoning_requested=False):
        return _plan(ranked)

    monkeypatch.setattr(orch, "_auto_route", fake_route, raising=False)
    payload = SimpleNamespace(model="auto", routing_hints=None, reasoning=None, metadata=None, tools=None)
    log = _Log()
    await orch._apply_router_chain(SimpleNamespace(tenant_id="t", project_id="p"), payload, [], None, log)
    handed = payload.metadata["auto_candidates"]
    assert handed == ["o-1", "d-1", "o-2", "o-3", "o-4"]
    assert log.events["auto_route"]["fallbacks"] == handed


# ---------------------------------------------------------------------------
# (A) where the explicit walk's list comes from
# ---------------------------------------------------------------------------

def _orch_with_plan(monkeypatch, plan_or_exc):
    from app.services.orchestrator import GatewayOrchestrator

    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    seen: list = []

    async def fake_route(ctx, payload, dict_messages, session, log, *, reasoning_requested=False):
        seen.append(payload)
        if isinstance(plan_or_exc, Exception):
            raise plan_or_exc
        return plan_or_exc

    monkeypatch.setattr(orch, "_auto_route", fake_route, raising=False)
    return orch, seen


@pytest.mark.asyncio
async def test_explicit_request_gets_the_auto_routers_list_for_the_same_request(settings, monkeypatch):
    ranked = [_c("openai", "gpt-5-mini"), _c("openai", "gpt-5-nano"), _c("deepseek", "deepseek-v4-pro")]
    orch, seen = _orch_with_plan(monkeypatch, _plan(ranked))
    payload = SimpleNamespace(model=PRIMARY[1], metadata={}, routing_hints=None, reasoning=None, tools=None)
    got = await orch.explicit_rescue_candidates(_ctx(), payload, [], None, _Log(), policy=fp.base_policy())
    # the router's pick included (for an explicit turn it is an alternative
    # like any other), spread over providers
    assert got == ["gpt-5-mini", "deepseek-v4-pro", "gpt-5-nano"]
    # the router saw the request itself: its tools / images / allowed providers
    assert seen == [payload]


@pytest.mark.asyncio
async def test_no_list_for_an_auto_turn_a_switched_off_rule_the_kill_switch_or_a_router_error(settings, monkeypatch):
    ranked = [_c("openai", "gpt-5-mini")]
    orch, seen = _orch_with_plan(monkeypatch, _plan(ranked))
    explicit = SimpleNamespace(model=PRIMARY[1], metadata={}, routing_hints=None, reasoning=None, tools=None)
    auto_turn = SimpleNamespace(model="gpt-5-mini", metadata={"auto_candidates": ["x"]}, routing_hints=None, reasoning=None, tools=None)

    assert await orch.explicit_rescue_candidates(_ctx(), auto_turn, [], None, _Log(), policy=fp.base_policy()) is None
    off = fp.apply_overrides(fp.base_policy(), {"explicit_without_chain": {"rule": "give_up"}})
    assert await orch.explicit_rescue_candidates(_ctx(), explicit, [], None, _Log(), policy=off) is None
    settings["fallback_enabled"] = "false"
    fp.clear_cache()
    assert await orch.explicit_rescue_candidates(_ctx(), explicit, [], None, _Log(), policy=fp.base_policy()) is None
    assert seen == []  # the router was never even asked

    settings.pop("fallback_enabled")
    fp.clear_cache()
    broken, _ = _orch_with_plan(monkeypatch, NoCapableModelError(details={"missing": ["tools"]}))
    assert await broken.explicit_rescue_candidates(_ctx(), explicit, [], None, _Log(), policy=fp.base_policy()) is None


# ---------------------------------------------------------------------------
# (2) non-streaming: explicit model, no chain → another model answers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_stream_explicit_chainless_model_is_rescued_by_the_routers_list(settings, registry, no_redis):
    runner, disp = _runner()
    res = await runner.run(
        original_provider=PRIMARY[0], original_model=PRIMARY[1], request=_payload(), messages=[],
        session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("no first token"),
        rescue_candidates=ROUTER_LIST,
    )
    assert (res.provider_used, res.model_used) == ("openai", "gpt-5-mini")
    assert res.attempt_number == 2 and res.attempts == []
    assert disp.calls == [("openai", "gpt-5-mini")]


@pytest.mark.asyncio
async def test_non_stream_without_a_list_the_original_error_comes_back_untouched(settings, registry, no_redis):
    runner, disp = _runner()
    original = ProviderTimeoutError("no first token")
    with pytest.raises(ProviderTimeoutError) as ei:
        await runner.run(
            original_provider=PRIMARY[0], original_model=PRIMARY[1], request=_payload(), messages=[],
            session=MagicMock(), ctx=_ctx(), original_error=original,
        )
    assert ei.value is original and disp.calls == []


@pytest.mark.asyncio
async def test_a_model_with_a_chain_walks_its_own_chain_first(settings, registry, no_redis):
    # gpt-4o from openai: anthropic → google → openrouter, none of which has
    # a key here, so the walk reaches the router's list with both slots left.
    runner, disp = _runner(keys=("deepseek",))
    res = await runner.run(
        original_provider="openai", original_model="gpt-4o", request=_payload(model="gpt-4o"), messages=[],
        session=MagicMock(), ctx=_ctx(), original_error=ProviderError("boom", code="provider_error"),
        rescue_candidates=ROUTER_LIST,
    )
    assert res.model_used == "deepseek-v4-pro"
    assert [a.error_code for a in res.attempts][:3] == ["no_api_key"] * 3


# ---------------------------------------------------------------------------
# (3) a rate-limited account is left, on both paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_stream_rate_limit_moves_to_another_account(settings, registry, no_redis):
    runner, disp = _runner(fail={"gpt-5-mini": RateLimitExceededError("429 from openai")})
    res = await runner.run(
        original_provider=PRIMARY[0], original_model=PRIMARY[1], request=_payload(), messages=[],
        session=MagicMock(), ctx=_ctx(), original_error=ProviderError("upstream 503", code="provider_5xx"),
        rescue_candidates=ROUTER_LIST,
    )
    assert disp.calls == [("openai", "gpt-5-mini"), ("deepseek", "deepseek-v4-pro")]
    assert res.model_used == "deepseek-v4-pro" and res.attempt_number == 3


def test_streaming_walk_rules_out_the_account_with_the_non_stream_classifier(settings, registry):
    runner, _ = _runner()
    skip_providers: set[str] = set()
    skip_pairs: set[tuple[str, str]] = set()

    # the primary died on a model-scoped failure: its siblings stay
    scope = fo.rule_out_after_failure(
        ProviderError("pulled", code="provider_model_not_found"), *PRIMARY,
        skip_providers=skip_providers, skip_pairs=skip_pairs, policy=fp.base_policy(),
    )
    assert scope == "model" and skip_providers == set() and skip_pairs == {PRIMARY}

    first = _select(runner, rescue_candidates=ROUTER_LIST, skip_providers=skip_providers, skip_pairs=skip_pairs)
    assert first["model"] == "gpt-5-mini"
    exc = RateLimitExceededError("429 from openai")
    scope = fo.rule_out_after_failure(
        exc, first["provider"], first["model"],
        skip_providers=skip_providers, skip_pairs=skip_pairs, policy=fp.base_policy(),
    )
    # the same answer run() acts on
    assert scope == fo.classify_failure_scope(exc, fp.base_policy()) == "account"
    assert skip_providers == {"openai"}
    second = _select(runner, rescue_candidates=ROUTER_LIST, skip_providers=skip_providers, skip_pairs=skip_pairs)
    assert (second["provider"], second["model"]) == ("deepseek", "deepseek-v4-pro")


def test_a_provider_wide_code_still_rules_out_the_provider():
    skip_providers: set[str] = set()
    fo.rule_out_after_failure(
        ProviderTimeoutError("30 s"), *PRIMARY, skip_providers=skip_providers, skip_pairs=set(), policy=None,
    )
    assert skip_providers == {"sranai"}


def test_the_chat_route_uses_the_shared_rule_and_hands_the_list_to_every_selection():
    import ast

    src = (BACKEND / "app" / "api" / "routes" / "public_chat.py").read_text(encoding="utf-8")
    assert "rule_out_after_failure" in src
    assert "provider_wide_codes(_fb_policy)" not in src, "a second scope table in the chat route"
    calls = [
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("select_streaming_candidate", "select_streaming_candidate_async")
    ]
    assert len(calls) >= 2
    for call in calls:
        assert "rescue_candidates" in {k.arg for k in call.keywords}, f"public_chat.py:{call.lineno}"


# ---------------------------------------------------------------------------
# Streaming selectors: the list, the boundary, the kill switch, the markers
# ---------------------------------------------------------------------------

def test_streaming_selector_walks_the_rescue_list_for_a_chainless_model(settings, registry):
    runner, _ = _runner()
    assert _select(runner) is None  # no list handed in: as before
    pick = _select(runner, rescue_candidates=ROUTER_LIST)
    assert pick == {"provider": "openai", "model": "gpt-5-mini", "api_key": "sk-openai"}


@pytest.mark.asyncio
async def test_pre_check_selector_walks_the_rescue_list_and_respects_markers(settings, registry, monkeypatch):
    async def marked(provider, model=None):
        return provider == "openai"

    monkeypatch.setattr(fo, "is_provider_unhealthy", marked)
    runner, _ = _runner()
    skipped: list[dict] = []
    pick = await runner.select_streaming_candidate_async(
        original_provider=PRIMARY[0], original_model=PRIMARY[1], session=MagicMock(),
        rescue_candidates=ROUTER_LIST, skipped=skipped,
    )
    assert pick["model"] == "deepseek-v4-pro"
    assert {"provider": "openai", "model": "gpt-5-mini", "reason": "health_marker"} in skipped


def test_tenant_boundary_holds_on_the_explicit_walk(settings, registry):
    # (5) the project's allowed providers, and a tenant override's
    runner, _ = _runner()
    skipped: list[dict] = []
    pick = _select(runner, rescue_candidates=ROUTER_LIST, allowed_providers=["sranai"], skipped=skipped)
    assert pick["model"] == "Typhoon-S-ThaiLLM-8B-Instruct"
    assert {s["reason"] for s in skipped} == {"outside_boundary"}

    fenced = fp.apply_overrides(fp.base_policy(), {"allowed_providers": ["deepseek"]})
    pick = _select(runner, rescue_candidates=ROUTER_LIST, policy=fenced)
    assert pick["provider"] == "deepseek"
    assert _select(runner, rescue_candidates=ROUTER_LIST, policy=fenced, allowed_providers=["sranai"]) is None


@pytest.mark.asyncio
async def test_tenant_boundary_holds_on_the_non_stream_explicit_walk(settings, registry, no_redis):
    runner, disp = _runner()
    res = await runner.run(
        original_provider=PRIMARY[0], original_model=PRIMARY[1],
        request=_payload({"allowed_providers": ["sranai"]}), messages=[], session=MagicMock(), ctx=_ctx(),
        original_error=ProviderError("pulled", code="provider_model_not_found"),
        rescue_candidates=ROUTER_LIST,
    )
    assert disp.calls == [("sranai", "Typhoon-S-ThaiLLM-8B-Instruct")]
    assert res.provider_used == "sranai"


@pytest.mark.asyncio
async def test_kill_switch_still_wins(settings, registry, no_redis):
    # (6)
    settings["fallback_enabled"] = "false"
    fp.clear_cache()
    runner, disp = _runner()
    assert _select(runner, rescue_candidates=ROUTER_LIST) is None
    original = ProviderTimeoutError("no first token")
    with pytest.raises(ProviderTimeoutError) as ei:
        await runner.run(
            original_provider=PRIMARY[0], original_model=PRIMARY[1], request=_payload(), messages=[],
            session=MagicMock(), ctx=_ctx(), original_error=original, rescue_candidates=ROUTER_LIST,
        )
    assert ei.value is original and disp.calls == []


def test_the_in_stream_selector_passes_over_marked_pairs_but_never_ends_on_markers_alone(settings, registry):
    runner, _ = _runner()
    skipped: list[dict] = []
    pick = _select(runner, rescue_candidates=ROUTER_LIST, unhealthy={("openai", "gpt-5-mini"), ("openai", "gpt-5-nano")}, skipped=skipped)
    assert pick["model"] == "deepseek-v4-pro"
    assert [s["reason"] for s in skipped] == ["health_marker", "health_marker"]

    # every candidate marked: a marker is a hint, trying beats refusing
    everything = {(PROVIDER_OF[m], m) for m in ROUTER_LIST}
    pick = _select(runner, rescue_candidates=ROUTER_LIST, unhealthy=everything)
    assert pick is not None and pick["model"] == "gpt-5-mini"


@pytest.mark.asyncio
async def test_the_walks_marked_pairs_are_read_once_for_the_in_stream_selection(settings, registry, monkeypatch):
    async def marked(provider, model=None):
        return provider == "openai"

    monkeypatch.setattr(fo, "is_provider_unhealthy", marked)
    runner, _ = _runner()
    got = await runner.marked_in_streaming_walk(
        original_provider=PRIMARY[0], original_model=PRIMARY[1], policy=None,
        action="cooldown_then_next", auto_candidates=None, rescue_candidates=ROUTER_LIST,
    )
    assert got == {("openai", "gpt-5-mini"), ("openai", "gpt-5-nano")}


# ---------------------------------------------------------------------------
# The chat route, end to end (the explicit model is a seeded one that has no
# chain; the Auto router is a stub that ranks three seeded models)
# ---------------------------------------------------------------------------

E2E_PRIMARY = ("deepseek", "deepseek-coder")
E2E_ROUTER = ["gpt-4o-mini", "gpt-4o", "claude-3-haiku-20240307"]
_E2E_PAIRS = [E2E_PRIMARY, ("openai", "gpt-4o-mini"), ("openai", "gpt-4o"), ("anthropic", "claude-3-haiku-20240307")]
_HEADERS = {"Authorization": "Bearer demo-key"}


def _parse_sse(body: bytes) -> list[dict]:
    events = []
    for line in body.decode().splitlines():
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            events.append(json.loads(line[6:]))
    return events


def _failing(exc_factory):
    async def _stream(self, payload, messages, *, resolved_model=None, api_key=None) -> AsyncIterator[ProviderStreamChunk]:
        _stream.calls.append(resolved_model)
        raise exc_factory()
        yield  # pragma: no cover — makes this an async generator

    _stream.calls = []
    return _stream


def _answering():
    async def _stream(self, payload, messages, *, resolved_model=None, api_key=None) -> AsyncIterator[ProviderStreamChunk]:
        _stream.calls.append(resolved_model)
        yield ProviderStreamChunk(delta="answer from the model that works. " * 6, finish_reason="stop", model=resolved_model)

    _stream.calls = []
    return _stream


def _five_hundred():
    return ProviderError("upstream 503", code="provider_5xx", details={"provider": "deepseek", "status": 503})


@pytest.fixture
def chat(monkeypatch):
    """The real route with a stubbed Auto router and clean markers."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.orchestrator import GatewayOrchestrator
    from app.services.providers.model_registry import get_candidate

    def _clear():
        # A sync client of its own: the pooled async one is bound to whichever
        # event loop used it first, and a clear that half-works leaves the
        # marker (or its strike count) in the Redis the lane shares.
        try:
            import redis

            from app.core.config import settings as _settings

            client = redis.Redis.from_url(_settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
            keys = [k for provider, model in _E2E_PAIRS for k in (
                fo._health_key(provider, model), fo._strikes_key(provider, model), fo._health_key(provider),
            )]
            client.delete(*keys)
            client.close()
        except Exception:  # noqa: BLE001 — no Redis here is not a failure
            pass

    async def fake_route(self, ctx, payload, dict_messages, session, log, *, reasoning_requested=False):
        return _plan([get_candidate(m) for m in E2E_ROUTER])

    monkeypatch.setattr(GatewayOrchestrator, "_auto_route", fake_route)
    store: dict[str, str] = {}
    monkeypatch.setattr(fp, "_read_setting", lambda key: store.get(key))
    fp.clear_cache()
    _clear()
    client = TestClient(app, raise_server_exceptions=False)

    def post(stream: bool):
        # a fresh prompt per call: never the response cache
        return client.post("/v1/chat/completions", headers=_HEADERS, json={
            "model": E2E_PRIMARY[1], "stream": stream, "metadata": {},
            "messages": [{"role": "user", "content": f"fix124 turn {uuid.uuid4().int % 10**6}"}],
        })

    post.settings = store
    yield post
    _clear()
    fp.clear_cache()


def test_stream_explicit_chainless_model_is_answered_by_another_model(chat, monkeypatch):
    from app.services.providers.deepseek_adapter import DeepSeekAdapter
    from app.services.providers.openai_adapter import OpenAIAdapter

    dead, alive = _failing(_five_hundred), _answering()
    monkeypatch.setattr(DeepSeekAdapter, "stream_chat", dead)
    monkeypatch.setattr(OpenAIAdapter, "stream_chat", alive)

    resp = chat(stream=True)
    events = _parse_sse(resp.content)
    assert not [e for e in events if "error" in e], events
    info = [e for e in events if e.get("type") == "fallback_info"]
    assert len(info) == 1
    info = info[0]["fallback_info"]
    assert info["requested_model"] == "deepseek-coder" and info["actual_model"] == "gpt-4o-mini"
    # the attempts a person is shown = the models that were tried
    assert [(a["model"], a.get("error_code") or a.get("status")) for a in info["attempts"]] == [
        ("deepseek-coder", "provider_5xx"), ("gpt-4o-mini", "success"),
    ]
    assert info["attempts"][0]["latency_ms"] > 0
    assert dead.calls == ["deepseek-coder"] and alive.calls == ["gpt-4o-mini"]
    assert any("answer from the model that works" in (c.get("delta") or {}).get("content", "")
               for e in events for c in e.get("choices", []))

    # the record says which model answered, exactly as for any other fallback
    from app.models.request_log import RequestLogORM
    from tests.conftest import _IntegrationSession

    # the content frames carry the id the row is stored under
    request_id = next(e["id"] for e in events if e.get("object") == "chat.completion.chunk")
    with _IntegrationSession() as s:
        row = s.query(RequestLogORM).filter(RequestLogORM.request_id == request_id).one()
        assert row.fallback_used is True
        assert (row.original_provider, row.original_model) == E2E_PRIMARY
        assert (row.provider, row.model) == ("openai", "gpt-4o-mini")
        assert [a["model"] for a in row.fallback_attempts] == ["deepseek-coder"]


def test_stream_rate_limited_account_is_left_for_another_provider(chat, monkeypatch):
    from app.services.providers.anthropic_adapter import AnthropicAdapter
    from app.services.providers.deepseek_adapter import DeepSeekAdapter
    from app.services.providers.openai_adapter import OpenAIAdapter

    limited, alive = _failing(lambda: RateLimitExceededError("429 from openai")), _answering()
    monkeypatch.setattr(DeepSeekAdapter, "stream_chat", _failing(_five_hundred))
    monkeypatch.setattr(OpenAIAdapter, "stream_chat", limited)
    monkeypatch.setattr(AnthropicAdapter, "stream_chat", alive)

    events = _parse_sse(chat(stream=True).content)
    assert not [e for e in events if "error" in e], events
    info = [e for e in events if e.get("type") == "fallback_info"][0]["fallback_info"]
    assert info["actual_model"] == "claude-3-haiku-20240307"
    assert [a["model"] for a in info["attempts"]] == ["deepseek-coder", "gpt-4o-mini", "claude-3-haiku-20240307"]
    # one try on the rate-limited account, never its second model
    assert limited.calls == ["gpt-4o-mini"]


def test_stream_every_model_dead_is_one_terminal_frame_with_the_real_attempt_count(chat, monkeypatch):
    from app.services.providers.anthropic_adapter import AnthropicAdapter
    from app.services.providers.deepseek_adapter import DeepSeekAdapter
    from app.services.providers.openai_adapter import OpenAIAdapter

    dead = [_failing(_five_hundred), _failing(lambda: RateLimitExceededError("429")), _failing(lambda: ProviderTimeoutError("30 s"))]
    monkeypatch.setattr(DeepSeekAdapter, "stream_chat", dead[0])
    monkeypatch.setattr(OpenAIAdapter, "stream_chat", dead[1])
    monkeypatch.setattr(AnthropicAdapter, "stream_chat", dead[2])

    events = _parse_sse(chat(stream=True).content)
    errors = [e["error"] for e in events if "error" in e]
    assert len(errors) == 1 and errors[0]["code"] == "all_models_failed"
    tried = dead[0].calls + dead[1].calls + dead[2].calls
    assert tried == ["deepseek-coder", "gpt-4o-mini", "claude-3-haiku-20240307"]
    assert [a["model"] for a in errors[0]["attempts"]] == tried
    assert not [e for e in events if e.get("type") == "fallback_info"]


def test_stream_kill_switch_off_leaves_the_primarys_own_error(chat, monkeypatch):
    from app.services.providers.deepseek_adapter import DeepSeekAdapter
    from app.services.providers.openai_adapter import OpenAIAdapter

    alive = _answering()
    monkeypatch.setattr(DeepSeekAdapter, "stream_chat", _failing(_five_hundred))
    monkeypatch.setattr(OpenAIAdapter, "stream_chat", alive)
    chat.settings["fallback_enabled"] = "false"
    fp.clear_cache()

    events = _parse_sse(chat(stream=True).content)
    errors = [e["error"] for e in events if "error" in e]
    assert [e["code"] for e in errors] == ["provider_5xx"]
    assert alive.calls == []


def test_non_stream_explicit_chainless_model_is_answered_and_says_by_whom(chat, monkeypatch):
    from app.services.provider_dispatcher import ProviderDispatcher

    calls: list[str] = []

    async def _dispatch(self, route, payload, messages, api_key=None):
        calls.append(route.model_name)
        if route.model_name == "deepseek-coder":
            raise _five_hundred()
        return ProviderResponse(content="answer from the model that works", model=route.model_name, finish_reason="stop")

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _dispatch)
    resp = chat(stream=False)
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == "gpt-4o-mini"
    assert resp.headers["X-Gateway-Fallback"] == "true"
    assert resp.headers["X-Gateway-Model"] == "gpt-4o-mini" and resp.headers["X-Gateway-Attempt"] == "2"
    assert calls == ["deepseek-coder", "gpt-4o-mini"]
