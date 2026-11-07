"""FIX124 A1 — the model that rescues a turn is the one CLOSEST to the model
the person picked, not the best model of each provider.

"Best per provider first" could put an opus-class model in front of a person
who had picked the free in-house one — and the turn is charged at the price of
the model that answers. The order is now, among the models the router already
found capable (tools / vision / window — unchanged):

1. nearest price band to the requested model (``auto_router.tiers``);
2. nearest price inside the band (log ratio; cheapest first when the requested
   model has no price in the catalogue);
3. the faster ``latency_tier``, then the router's own ranking (which carries
   the tenant's 7-day success rates);
4. still one model per provider before any provider's second model.

The pool below is the tool-capable part of the live catalogue on 2026-09-21
(USD per 1M input tokens).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.domain.routing import ModelCandidate
from app.services.auto_router import router
from app.services.gateway import fallback_policy as fp


def _c(provider, model, price, *, cost_tier="standard", latency="standard"):
    return ModelCandidate(
        provider=provider, model=model, supports_streaming=True, supports_tools=True,
        cost_tier=cost_tier, latency_tier=latency, cost_per_input_token=price / 1_000_000,
    )


QWEN = _c("sranai", "qwen3.8-27b-fp8", 0.0)  # free in-house: no price in the catalogue
FLASH = _c("deepseek", "deepseek-v4-flash", 0.14, cost_tier="budget")
SOL = _c("openai", "gpt-5.6-sol", 5.0, cost_tier="premium")

# In the order a router that likes big models would rank them.
RANKED = [
    _c("anthropic", "claude-opus-4-8", 5.0, cost_tier="premium", latency="slow"),
    _c("openrouter", "anthropic/claude-opus-4.1", 15.0, cost_tier="premium"),
    SOL,
    _c("anthropic", "claude-sonnet-5", 3.0),
    _c("openai", "gpt-5.6-terra", 2.0),
    _c("deepseek", "deepseek-v4-pro", 1.74),
    _c("google", "gemini-3.6-flash", 1.5, latency="fast"),
    _c("anthropic", "claude-haiku-4-5", 1.0, latency="fast"),
    _c("openrouter", "google/gemini-2.5-flash", 0.3, cost_tier="budget"),
    _c("openai", "gpt-5.6-luna", 0.2, cost_tier="budget"),
    _c("openrouter", "openai/gpt-4o-mini", 0.15, cost_tier="budget"),
    FLASH,
]


def _order(requested, ranked=RANKED, **kw):
    return [c.model for c in router.closest_to_requested(ranked, requested, **kw)]


def _alternatives(requested, limit=5, **kw):
    pool = [c for c in RANKED if c.model != requested.model]
    return [c.model for c in router.provider_diverse(router.closest_to_requested(pool, requested, **kw), limit)]


# ---------------------------------------------------------------------------
# The measure
# ---------------------------------------------------------------------------

def test_a_free_model_is_rescued_by_the_cheapest_band_and_never_by_opus():
    got = _alternatives(QWEN)
    assert got == [
        "deepseek-v4-flash", "openai/gpt-4o-mini", "gpt-5.6-luna",
        "claude-haiku-4-5", "gemini-3.6-flash",
    ]
    assert not [m for m in got if "opus" in m or m == "gpt-5.6-sol"]
    # and in the full order the opus-class models are the very last resort
    assert _order(QWEN)[-2:] == ["claude-opus-4-8", "anthropic/claude-opus-4.1"]


def test_a_cheap_model_is_rescued_by_its_price_neighbours():
    got = _alternatives(FLASH)
    assert got[:3] == ["openai/gpt-4o-mini", "gpt-5.6-luna", "claude-haiku-4-5"]
    assert "claude-opus-4-8" not in got and "anthropic/claude-opus-4.1" not in got


def test_a_high_tier_model_is_rescued_by_a_model_of_its_own_tier():
    got = _alternatives(SOL)
    # same band (high: ≤ $10), same price first; the dearer band after it
    assert got[0] == "claude-opus-4-8"
    assert got.index("claude-opus-4-8") < got.index("anthropic/claude-opus-4.1")
    assert "deepseek-v4-flash" not in got[:3]


def test_provider_diversity_still_holds_over_the_closeness_order():
    for requested in (QWEN, FLASH, SOL):
        got = router.provider_diverse(
            router.closest_to_requested([c for c in RANKED if c.model != requested.model], requested), 5,
        )
        providers = [c.provider for c in got]
        # five providers in the pool, five places: one model each
        assert len(set(providers)) == 5, providers


def test_ties_go_to_the_faster_model_then_to_the_routers_ranking():
    a = _c("p1", "slowpoke", 1.0, latency="slow")
    b = _c("p2", "steady", 1.0)
    c = _c("p3", "quick", 1.0, latency="fast")
    d = _c("p4", "steady-too", 1.0)
    assert _order(_c("x", "asked", 1.0), [a, b, c, d]) == ["quick", "steady", "steady-too", "slowpoke"]


def test_a_candidate_without_a_price_never_wins_a_closeness_comparison():
    unknown = _c("kimi", "moonshot-v1-8k", 0.0, latency="fast")
    priced = _c("deepseek", "deepseek-v4-pro", 1.74)
    assert _order(_c("openai", "gpt-5.6-terra", 2.0), [unknown, priced]) == ["deepseek-v4-pro", "moonshot-v1-8k"]


def test_without_a_requested_model_the_routers_ranking_stands():
    assert _order(None) == [c.model for c in RANKED]


# ---------------------------------------------------------------------------
# The rule is data
# ---------------------------------------------------------------------------

@pytest.fixture
def settings(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(fp, "_read_setting", lambda key: store.get(key))
    fp.clear_cache()
    yield store
    fp.clear_cache()


def test_the_rule_ships_in_the_policy_file_and_is_validated(settings):
    pol = fp.base_policy()
    assert pol["rescue_order"]["rule"] == "closest_to_requested"
    assert fp.rescue_order(pol) == ("closest_to_requested", "low")
    assert "rescue_order" in pol["overrides"]["overridable"]
    assert fp.validate_override({"rescue_order": {"rule": "router_ranking"}}, is_tenant=True)
    assert fp.validate_override({"rescue_order": {"requested_price_unknown_band": "medium"}}, is_tenant=False)
    for bad in ({"rule": "best_model"}, {"requested_price_unknown_band": "cheap"}, {"extra": 1}, "closest"):
        with pytest.raises(ValueError):
            fp.validate_override({"rescue_order": bad}, is_tenant=True)
    settings["fallback_policy"] = json.dumps({"rescue_order": {"rule": "router_ranking"}})
    fp.clear_cache()
    assert fp.rescue_order(fp.effective_policy("t"))[0] == "router_ranking"


def test_the_band_assumed_for_a_requested_model_without_a_price_is_the_policys():
    # medium = the catalogue's own convention for an unknown price
    got = _alternatives(QWEN, unknown_band="medium")
    assert got[0] in {"gpt-5.6-terra", "deepseek-v4-pro", "gemini-3.6-flash", "claude-haiku-4-5", "claude-sonnet-5"}
    # a legacy cost_tier that says something is believed before the policy's assumption
    premium_no_price = _c("x", "big-unpriced", 0.0, cost_tier="premium")
    assert _order(premium_no_price)[0] in {"claude-opus-4-8", "gpt-5.6-sol"}


# ---------------------------------------------------------------------------
# Both lists use it
# ---------------------------------------------------------------------------

class _Log:
    def __init__(self):
        self.events: dict[str, dict] = {}

    def info(self, ev, **k):
        self.events[ev] = k

    warning = info


def _plan(cands):
    return router.RoutePlan(
        primary=cands[0], fallbacks=list(cands[1:]), task="chat", task_confidence=1.0,
        tier="high", reason="test", candidates_count=len(cands), filtered_count=len(cands),
    )


def _orch(monkeypatch, plan, registry):
    from app.services.orchestrator import GatewayOrchestrator

    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)

    async def fake_route(ctx, payload, dict_messages, session, log, *, reasoning_requested=False):
        return plan

    monkeypatch.setattr(orch, "_auto_route", fake_route, raising=False)
    monkeypatch.setattr(
        "app.services.providers.model_registry.get_candidate",
        lambda model: {c.model: c for c in registry}.get(model),
    )
    return orch


@pytest.mark.asyncio
async def test_the_explicit_rescue_list_is_ordered_by_closeness_to_the_picked_model(settings, monkeypatch):
    orch = _orch(monkeypatch, _plan(RANKED), [QWEN, *RANKED])
    payload = SimpleNamespace(model=QWEN.model, metadata={}, routing_hints=None, reasoning=None, tools=None)
    ctx = SimpleNamespace(tenant_id="t", project_id="p")
    got = await orch.explicit_rescue_candidates(ctx, payload, [], None, _Log(), policy=fp.base_policy())
    assert got == ["deepseek-v4-flash", "openai/gpt-4o-mini", "gpt-5.6-luna", "claude-haiku-4-5", "gemini-3.6-flash"]

    # the previous order is one override away
    ranking = fp.apply_overrides(fp.base_policy(), {"rescue_order": {"rule": "router_ranking"}})
    got = await orch.explicit_rescue_candidates(ctx, payload, [], None, _Log(), policy=ranking)
    assert got[0] == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_an_auto_turns_alternatives_are_ordered_by_closeness_to_the_routers_pick(settings, monkeypatch):
    # the router picked the cheap model; its alternatives must not open with opus
    orch = _orch(monkeypatch, _plan([FLASH, *[c for c in RANKED if c is not FLASH]]), RANKED)
    payload = SimpleNamespace(model="auto", routing_hints=None, reasoning=None, metadata=None, tools=None)
    log = _Log()
    await orch._apply_router_chain(SimpleNamespace(tenant_id="t", project_id="p"), payload, [], None, log)
    assert payload.model == FLASH.model
    assert payload.metadata["auto_candidates"][:3] == ["openai/gpt-4o-mini", "gpt-5.6-luna", "claude-haiku-4-5"]
    assert not [m for m in payload.metadata["auto_candidates"] if "opus" in m]


# ── metadata.closest_to: "any model that works, nearest to THIS one" ─────────
# chat-edge sends it on the title's second ask (the model that answered the
# turn could not name the chat). The Auto plan is then re-ordered by the same
# closeness, with the named model left out — it was just asked.


def _auto_plan(primary, fallbacks):
    from app.services.auto_router.router import RoutePlan
    import dataclasses

    fields = {f.name for f in dataclasses.fields(RoutePlan)}
    base = dict(primary=primary, fallbacks=list(fallbacks), reason="tenant-ranked")
    for name in fields - set(base):
        base[name] = {"task": "chat", "tier": "high", "signals": {}, "task_confidence": 1.0}.get(name, 0 if "count" in name else False)
    return RoutePlan(**base)


def _hint(plan, closest_to, monkeypatch, known=None):
    from app.services import orchestrator as orch
    from app.services.providers import model_registry

    table = {c.model: c for c in [QWEN, *RANKED]} if known is None else known
    monkeypatch.setattr(model_registry, "get_candidate", lambda name: table.get(name))
    log = SimpleNamespace(info=lambda *a, **k: None)
    return orch.GatewayOrchestrator._closest_to_hint(plan, closest_to, SimpleNamespace(tenant_id=None), log)


def test_closest_to_reorders_the_auto_plan_towards_the_named_model(monkeypatch):
    plan = _auto_plan(RANKED[0], RANKED[1:])  # the router likes opus
    out = _hint(plan, "qwen3.8-27b-fp8", monkeypatch)
    assert out.primary.model == "deepseek-v4-flash"  # nearest the free in-house model, not opus
    assert "claude-opus" not in out.primary.model
    assert out.reason == "closest_to:qwen3.8-27b-fp8"
    assert {c.model for c in [out.primary, *out.fallbacks]} == {c.model for c in RANKED}


def test_closest_to_leaves_the_named_model_out(monkeypatch):
    plan = _auto_plan(FLASH, [c for c in RANKED if c is not FLASH])
    out = _hint(plan, "deepseek-v4-flash", monkeypatch)
    assert out.primary.model != "deepseek-v4-flash"
    assert all(c.model != "deepseek-v4-flash" for c in out.fallbacks)


@pytest.mark.parametrize("hint", [None, "", "   ", 7, "no-such-model"])
def test_closest_to_unknown_or_absent_changes_nothing(monkeypatch, hint):
    plan = _auto_plan(RANKED[0], RANKED[1:])
    assert _hint(plan, hint, monkeypatch) is plan
