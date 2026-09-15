"""FIX120 — the decision engine in motion: kill switch, boundary, tenant
override, cost cap, per-attempt records, the Layer-1 hops, the streaming
selectors. Every scenario also asserts the shape a caller relied on before
(the original error comes back untouched when nothing is tried).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import (
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ValidationError,
)
from app.domain.entities import RequestContext, RouteDecision
from app.domain.providers import ProviderResponse
from app.services.gateway import fallback_orchestrator as fo
from app.services.gateway import fallback_policy as fp
from app.services.gateway.fallback_orchestrator import FallbackOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(tenant="tenant-x") -> RequestContext:
    return RequestContext(tenant_id=tenant, project_id="proj-x", project_name="Test", request_id="req_unit", turn_id="turn-abc")


def _payload(allowed=None, model="gpt-4o"):
    p = MagicMock()
    p.model = model
    p.metadata = {} if allowed is None else {"allowed_providers": list(allowed)}
    p.messages = []
    p.tools = None
    return p


def _ok(model: str) -> ProviderResponse:
    return ProviderResponse(content="ok", model=model, finish_reason="stop")


class _Keys:
    def __init__(self, keys_for: set[str]):
        self.keys_for = keys_for

    def get_api_key(self, session, provider, model=None):
        return f"sk-{provider}" if provider in self.keys_for else None

    async def get_api_key_async(self, session, provider, model=None):
        return self.get_api_key(session, provider, model)


class _Dispatcher:
    """Every candidate answers unless its provider is in ``fail``."""

    def __init__(self, fail: dict[str, Exception] | None = None):
        self.fail = fail or {}
        self.calls: list[tuple[str, str]] = []
        self.adapters = {p: MagicMock(supports_streaming=True) for p in ("openai", "anthropic", "google", "openrouter", "deepseek", "groq", "sranai")}

    async def dispatch(self, *, route, payload, messages, api_key):
        self.calls.append((route.provider_name, route.model_name))
        if route.provider_name in self.fail:
            raise self.fail[route.provider_name]
        return _ok(route.model_name)


@pytest.fixture
def settings(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(fp, "_read_setting", lambda key: store.get(key))
    fp.clear_cache()
    yield store
    fp.clear_cache()


@pytest.fixture
def attempts(monkeypatch):
    """Capture the per-attempt and per-turn records instead of structlog."""
    rec: dict[str, list[dict]] = {"attempt": [], "outcome": []}
    orig_a, orig_o = fo.log_attempt, fo.log_outcome

    def a(log, **kw):
        rec["attempt"].append(kw)
        orig_a(log, **kw)

    def o(log, **kw):
        rec["outcome"].append(kw)
        orig_o(log, **kw)

    monkeypatch.setattr(fo, "log_attempt", a)
    monkeypatch.setattr(fo, "log_outcome", o)
    return rec


# conftest stubs ProviderDispatcher.dispatch_with_fallback for every test;
# the Layer-1 scenario below drives the REAL one (same override the routing
# tests use). The run() scenarios use their own fake dispatcher.
@pytest.fixture(autouse=True)
def _mock_provider_dispatch():
    yield


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    def boom():
        raise ConnectionError("no redis in unit tests")

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", boom)


# ---------------------------------------------------------------------------
# run() — non-streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_policy_rescues_like_before_and_records_every_attempt(settings, attempts):
    disp = _Dispatcher(fail={"anthropic": ProviderUnavailableError("anthropic down")})
    runner = FallbackOrchestrator(disp, _Keys({"anthropic", "google"}))
    res = await runner.run(
        original_provider="openai", original_model="gpt-4o", request=_payload(), messages=[],
        session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("primary timed out"),
    )
    # premium tier chain for gpt-4o from openai: anthropic → google → openrouter
    assert res.provider_used == "google" and res.model_used == "gemini-1.5-pro"
    assert res.attempt_number == 3 and [a.error_code for a in res.attempts] == ["provider_unavailable"]
    assert disp.calls == [("anthropic", "claude-opus-4"), ("google", "gemini-1.5-pro")]
    outcomes = [(a["attempt"], a["model"], a["outcome"]) for a in attempts["attempt"]]
    assert outcomes == [(2, "claude-opus-4", "failed"), (3, "gemini-1.5-pro", "success")]
    assert attempts["outcome"][-1]["outcome"] == "rescued"
    assert attempts["outcome"][-1]["served_model"] == "gemini-1.5-pro"
    assert attempts["outcome"][-1]["path"] == "non_stream"


@pytest.mark.asyncio
async def test_kill_switch_off_tries_nothing_and_returns_the_original_error(settings, attempts):
    settings["fallback_enabled"] = "false"
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys({"anthropic", "google"}))
    original = ProviderTimeoutError("primary timed out")
    with pytest.raises(ProviderTimeoutError) as ei:
        await runner.run(
            original_provider="openai", original_model="gpt-4o", request=_payload(), messages=[],
            session=MagicMock(), ctx=_ctx(), original_error=original,
        )
    assert ei.value is original
    assert disp.calls == []
    assert attempts["outcome"] == [dict(attempts["outcome"][0])]
    assert attempts["outcome"][0]["outcome"] == "disabled" and attempts["outcome"][0]["reason"] == "kill_switch"


@pytest.mark.asyncio
async def test_forbidden_code_is_never_walked(settings, attempts):
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys({"anthropic", "google"}))
    original = ValidationError("bad request")
    with pytest.raises(ValidationError):
        await runner.run(
            original_provider="openai", original_model="gpt-4o", request=_payload(), messages=[],
            session=MagicMock(), ctx=_ctx(), original_error=original,
        )
    assert disp.calls == []
    assert attempts["outcome"][0]["outcome"] == "forbidden"


@pytest.mark.asyncio
async def test_boundary_from_the_project_skips_every_outside_candidate(settings, attempts):
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys({"anthropic", "google", "openrouter"}))
    with pytest.raises(ProviderError):
        await runner.run(
            original_provider="openai", original_model="gpt-4o", request=_payload(allowed=["openai"]),
            messages=[], session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("t"),
        )
    assert disp.calls == []
    assert {a["reason"] for a in attempts["attempt"]} == {"outside_boundary"}
    assert [a["provider"] for a in attempts["attempt"]] == ["anthropic", "google", "openrouter"]
    assert attempts["outcome"][-1]["outcome"] == "exhausted"


@pytest.mark.asyncio
async def test_boundary_from_the_tenant_override_and_no_setting_can_widen_it(settings, attempts):
    settings["fallback_policy:tenant:tenant-x"] = json.dumps({"allowed_providers": ["google"]})
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys({"anthropic", "google", "openrouter"}))
    res = await runner.run(
        original_provider="openai", original_model="gpt-4o", request=_payload(), messages=[],
        session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("t"),
    )
    assert res.provider_used == "google"
    assert disp.calls == [("google", "gemini-1.5-pro")]
    assert [(a["provider"], a["outcome"]) for a in attempts["attempt"]] == [
        ("anthropic", "skipped"), ("google", "success"),
    ]
    # project ∩ tenant: the project says openai only → nothing left
    disp2 = _Dispatcher()
    runner2 = FallbackOrchestrator(disp2, _Keys({"anthropic", "google", "openrouter"}))
    with pytest.raises(ProviderError):
        await runner2.run(
            original_provider="openai", original_model="gpt-4o", request=_payload(allowed=["openai"]),
            messages=[], session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("t"),
        )
    assert disp2.calls == []


@pytest.mark.asyncio
async def test_tenant_override_chain_gives_a_chainless_model_a_chain(settings, attempts):
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys({"sranai"}))
    not_found = ProviderError("pulled", code="provider_model_not_found")
    with pytest.raises(ProviderError):
        await runner.run(
            original_provider="sranai", original_model="qwen3.8-27b-fp8", request=_payload(model="qwen3.8-27b-fp8"),
            messages=[], session=MagicMock(), ctx=_ctx(), original_error=not_found,
        )
    assert attempts["outcome"][-1]["outcome"] == "no_chain"
    settings["fallback_policy:tenant:tenant-x"] = json.dumps(
        {"chains": {"curated": {"qwen3.8-27b-fp8": [{"provider": "sranai", "model": "qwen3.6-35b-a3b"}]}}}
    )
    fp.clear_cache()
    res = await runner.run(
        original_provider="sranai", original_model="qwen3.8-27b-fp8", request=_payload(model="qwen3.8-27b-fp8"),
        messages=[], session=MagicMock(), ctx=_ctx(), original_error=not_found,
    )
    assert (res.provider_used, res.model_used) == ("sranai", "qwen3.6-35b-a3b")
    # another tenant still has no chain
    disp2 = _Dispatcher()
    with pytest.raises(ProviderError):
        await FallbackOrchestrator(disp2, _Keys({"sranai"})).run(
            original_provider="sranai", original_model="qwen3.8-27b-fp8", request=_payload(model="qwen3.8-27b-fp8"),
            messages=[], session=MagicMock(), ctx=_ctx("tenant-y"), original_error=not_found,
        )
    assert disp2.calls == []


@pytest.mark.asyncio
async def test_cost_cap_skips_the_dearer_hop_and_the_tenant_daily_cap_refuses_the_walk(settings, attempts, monkeypatch):
    from app.services.providers.model_registry import get_all_candidates

    cands = sorted(get_all_candidates(), key=lambda c: c.cost_per_input_token)
    cheap = cands[0]
    settings["fallback_policy"] = json.dumps(
        {"cost_cap": {"enabled": True, "max_cost_ratio_vs_requested": 1.0},
         "chains": {"curated": {"nano-primary": [{"provider": "google", "model": "gemini-1.5-pro"}, {"provider": cheap.provider, "model": cheap.model}]}}}
    )
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys({"google", cheap.provider}))
    # the requested model has no registry price → nothing is blocked
    res = await runner.run(
        original_provider="x", original_model="nano-primary", request=_payload(model="nano-primary"),
        messages=[], session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("t"),
    )
    assert res.provider_used == "google"
    # a requested model with a known price → the dearer hop is skipped
    settings["fallback_policy"] = json.dumps(
        {"cost_cap": {"enabled": True, "max_cost_ratio_vs_requested": 1.0},
         "chains": {"curated": {cheap.model: [{"provider": "google", "model": "gemini-1.5-pro"}]}}}
    )
    fp.clear_cache()
    disp2 = _Dispatcher()
    with pytest.raises(ProviderError):
        await FallbackOrchestrator(disp2, _Keys({"google"})).run(
            original_provider=cheap.provider, original_model=cheap.model, request=_payload(model=cheap.model),
            messages=[], session=MagicMock(), ctx=_ctx(),
            original_error=ProviderError("pulled", code="provider_model_not_found"),
        )
    assert disp2.calls == [] and attempts["attempt"][-1]["reason"].startswith("cost_cap")
    # the tenant daily cap refuses the whole walk
    settings["fallback_policy"] = json.dumps({"cost_cap": {"enabled": True, "tenant_daily_usd": 1.0}})
    fp.clear_cache()
    monkeypatch.setattr(fo, "_spend_reader", lambda session: (lambda t: 7.5))
    disp3 = _Dispatcher()
    with pytest.raises(ProviderTimeoutError):
        await FallbackOrchestrator(disp3, _Keys({"anthropic"})).run(
            original_provider="openai", original_model="gpt-4o", request=_payload(), messages=[],
            session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("t"),
        )
    assert disp3.calls == [] and attempts["outcome"][-1]["outcome"] == "cost_capped"


@pytest.mark.asyncio
async def test_limits_from_an_override_apply_but_an_explicit_constructor_value_wins(settings):
    settings["fallback_policy"] = json.dumps({"limits": {"non_stream": {"max_candidates": 1}}})
    disp = _Dispatcher(fail={"anthropic": ProviderUnavailableError("down")})
    with pytest.raises(ProviderUnavailableError):
        await FallbackOrchestrator(disp, _Keys({"anthropic", "google"})).run(
            original_provider="openai", original_model="gpt-4o", request=_payload(), messages=[],
            session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("t"),
        )
    assert disp.calls == [("anthropic", "claude-opus-4")]
    disp2 = _Dispatcher(fail={"anthropic": ProviderUnavailableError("down")})
    res = await FallbackOrchestrator(disp2, _Keys({"anthropic", "google"}), max_candidates=2).run(
        original_provider="openai", original_model="gpt-4o", request=_payload(), messages=[],
        session=MagicMock(), ctx=_ctx(), original_error=ProviderTimeoutError("t"),
    )
    assert res.provider_used == "google"


@pytest.mark.asyncio
async def test_breaker_off_marks_on_the_first_failure_as_before(monkeypatch):
    sets: list[tuple[str, int]] = []

    class _R:
        async def set(self, key, value, ex=None):
            sets.append((key, ex))

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", lambda: _R())
    await fo.mark_provider_unhealthy("openai", "gpt-4o", error_code="provider_timeout")
    assert sets == [("provider:health:openai:gpt-4o", 60), ("provider:health:openai", 60)]
    sets.clear()
    await fo.mark_provider_unhealthy("openai", "gpt-4o", error_code="provider_model_not_found")
    assert sets == [("provider:health:openai:gpt-4o", 60)]


@pytest.mark.asyncio
async def test_breaker_on_holds_the_marker_until_allowed_fails(monkeypatch):
    sets: list[tuple[str, int]] = []
    counts: dict[str, int] = {}

    class _R:
        async def set(self, key, value, ex=None):
            sets.append((key, ex))

        async def incr(self, key):
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        async def expire(self, key, ttl):
            pass

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", lambda: _R())
    pol = fp.apply_overrides(fp.base_policy(), {"breaker": {"enabled": True, "allowed_fails": 2, "cooldown_s": 30}})
    await fo.mark_provider_unhealthy("openai", "gpt-4o", error_code="provider_5xx", policy=pol)
    assert sets == []
    await fo.mark_provider_unhealthy("openai", "gpt-4o", error_code="provider_5xx", policy=pol)
    assert sets == [("provider:health:openai:gpt-4o", 30)]


# ---------------------------------------------------------------------------
# The streaming selectors
# ---------------------------------------------------------------------------

def test_streaming_selector_honours_kill_switch_boundary_and_reports_skips(settings):
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys({"anthropic", "google", "openrouter"}))
    skipped: list[dict] = []
    pick = runner.select_streaming_candidate(
        original_provider="openai", original_model="gpt-4o", session=MagicMock(),
        allowed_providers=["openai", "google"], skipped=skipped,
    )
    assert pick["provider"] == "google"
    assert skipped == [{"provider": "anthropic", "model": "claude-opus-4", "reason": "outside_boundary"}]
    settings["fallback_enabled"] = "false"
    fp.clear_cache()
    assert runner.select_streaming_candidate(original_provider="openai", original_model="gpt-4o", session=MagicMock()) is None


@pytest.mark.asyncio
async def test_streaming_selector_async_walks_the_action_chain(settings, monkeypatch):
    monkeypatch.setattr(fo, "is_provider_unhealthy", _false)
    disp = _Dispatcher()
    runner = FallbackOrchestrator(disp, _Keys({"google"}))
    pol = fp.apply_overrides(fp.base_policy(), {
        "opt_in": {"context_window_fallback": True},
        "chains": {"context_window": {"gpt-4o": [{"provider": "google", "model": "gemini-1.5-pro"}]}},
    })
    pick = await runner.select_streaming_candidate_async(
        original_provider="openai", original_model="gpt-4o", session=MagicMock(),
        policy=pol, action="context_window_chain",
    )
    assert pick["model"] == "gemini-1.5-pro"
    assert await runner.select_streaming_candidate_async(
        original_provider="openai", original_model="gpt-4o", session=MagicMock(),
        policy=fp.base_policy(), action="context_window_chain",
    ) is None


async def _false(provider, model=None):
    return False


# ---------------------------------------------------------------------------
# Layer-1 hops (dispatch_with_fallback)
# ---------------------------------------------------------------------------

class _Adapter:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls = 0

    async def chat(self, *a, **k):
        self.calls += 1
        if self.exc:
            raise self.exc
        return _ok("m")


@pytest.mark.asyncio
async def test_layer1_hops_are_off_under_the_kill_switch_and_outside_the_boundary(settings, monkeypatch):
    from app.services.provider_dispatcher import ProviderDispatcher

    primary, hop = _Adapter(ProviderUnavailableError("down")), _Adapter()
    d = ProviderDispatcher()
    d.adapters = {"openai": primary, "google": hop}

    async def _one(self, *, route, provider, model, payload, messages, first_key, key_resolver, invalidate_key):
        return await self.adapters[provider].chat()

    monkeypatch.setattr(ProviderDispatcher, "_dispatch_one", _one)
    route = RouteDecision(provider_name="openai", model_name="gpt-4o")
    route.fallbacks = [("google", "gemini-1.5-pro")]

    resp, p, m = await d.dispatch_with_fallback(route=route, payload=_payload(), messages=[], api_key="k", key_resolver=lambda p, m: "k")
    assert (p, m) == ("google", "gemini-1.5-pro") and hop.calls == 1

    hop.calls = 0
    with pytest.raises(ProviderUnavailableError):
        await d.dispatch_with_fallback(route=route, payload=_payload(allowed=["openai"]), messages=[], api_key="k", key_resolver=lambda p, m: "k")
    assert hop.calls == 0

    settings["fallback_enabled"] = "false"
    fp.clear_cache()
    with pytest.raises(ProviderUnavailableError):
        await d.dispatch_with_fallback(route=route, payload=_payload(), messages=[], api_key="k", key_resolver=lambda p, m: "k")
    assert hop.calls == 0
