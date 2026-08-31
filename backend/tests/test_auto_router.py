"""FIX35 Part C — the four-step Auto router.

Locks, in order of how much they matter:

1. An EXPLICIT model is never rewritten by any router — not by the auto
   router, not by keyword rules, not by reasoning routing — including
   ``openrouter/auto`` (passed through to OpenRouter's own router).
2. Routing never fails because of routing: a broken classifier / ranking /
   asset collapses to the default set; only an empty key-valid pool is an
   error (raised by the orchestrator, not here).
3. The key-valid filter is the orchestrator's job and the router trusts it —
   so the router must never re-introduce a candidate it wasn't given.
4. Taxonomy is ONE asset; classifier handles Thai; tiers map prices; ranking
   ignores thin cells (cold start) and scores trusted ones.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.routing import ModelCandidate
from app.schemas.public import RoutingHints
from app.services.auto_router import ranking, router, taxonomy, tiers

BACKEND = Path(__file__).resolve().parents[1]
# In the container /app IS the backend dir; on a checkout the repo root sits above it.
REPO = BACKEND.parent if (BACKEND.parent / "services").exists() else BACKEND
ASSET = BACKEND / "app" / "services" / "auto_router" / "task_taxonomy.json"


def _c(provider, model, *, price=None, cost="standard", vision=False, reasoning=False, tools=True):
    return ModelCandidate(
        provider=provider,
        model=model,
        supports_streaming=True,
        supports_tools=tools,
        supports_vision=vision,
        cost_tier=cost,
        supports_reasoning=reasoning,
        cost_per_input_token=(price / 1_000_000) if price is not None else 0.0,
    )


POOL = [
    _c("openai", "gpt-4o-mini", price=0.15, cost="budget", vision=True),
    _c("openai", "gpt-4o", price=2.5, vision=True),
    _c("openrouter", "anthropic/claude-sonnet-4.5", price=3.0, vision=True, reasoning=True),
    _c("openrouter", "deepseek/deepseek-r1", price=0.55, reasoning=True),
    _c("openrouter", "anthropic/claude-opus-4.1", price=15.0, cost="premium", vision=True, reasoning=True),
    _c("openrouter", "google/gemini-2.5-flash", price=0.30, cost="budget", vision=True),
]


# ---------------------------------------------------------------------------
# Step 1 — taxonomy + classifier
# ---------------------------------------------------------------------------

def test_taxonomy_is_one_asset_with_about_thirty_tasks():
    ids = taxonomy.task_ids()
    assert 28 <= len(ids) <= 40, ids
    assert taxonomy.GENERAL in ids
    # Single source: the only copy of the taxonomy in the repo is the asset.
    copies = [
        p for p in REPO.rglob("task_taxonomy*.json")
        if "node_modules" not in p.parts and "dist" not in p.parts
    ]
    assert copies == [ASSET], copies
    spec = json.loads(copies[0].read_text(encoding="utf-8"))
    for tid, t in spec["tasks"].items():
        assert t["prefer_tier"] in tiers.TIERS, tid
        assert isinstance(t["default_rank"], list) and t["default_rank"], tid
        assert t["label"]["th"] and t["label"]["en"], tid


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ช่วยหาจุดบกพร่องในโค้ดนี้หน่อย มัน error ตอนรัน", "code_debug"),
        ("Find the bug in this traceback and fix it", "code_debug"),
        ("15*37 เท่ากับเท่าไหร่ ช่วยคำนวณ", "math_calculation"),
        ("ช่วยสรุปเอกสารนี้ให้หน่อย", "summarize_document"),
        ("แปลประโยคนี้เป็นภาษาอังกฤษ", "translate"),
        ("ร่างอีเมลขอเลื่อนนัดแบบสุภาพ", "business_writing"),
        ("Write a short story about a lighthouse", "creative_writing"),
        ("เปรียบเทียบข้อดีข้อเสียของ Python กับ Go", "compare_evaluate"),
        ("สวัสดีครับ", "greeting_smalltalk"),
        ("hmm", taxonomy.GENERAL),
    ],
)
def test_classifier_english_and_thai(text, expected):
    tc = taxonomy.classify_task([{"role": "user", "content": text}])
    assert tc.task == expected, (text, tc)
    if expected != taxonomy.GENERAL:
        assert tc.confidence > 0 and tc.signals


def test_classifier_uses_structural_signals_and_never_raises():
    tc = taxonomy.classify_task([{"role": "user", "content": [{"type": "image_url"}]}], has_images=True)
    assert tc.task == "vision_image"
    assert taxonomy.classify_task(None).task == taxonomy.GENERAL  # type: ignore[arg-type]
    assert taxonomy.classify_task([{"role": "user", "content": 42}]).task == taxonomy.GENERAL


# ---------------------------------------------------------------------------
# Step 3 — tiers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "price, legacy, band",
    [(0.15, None, "low"), (0.5, None, "low"), (2.5, None, "medium"), (3.0, None, "medium"),
     (10.0, None, "high"), (15.0, None, "xhigh"), (60.0, None, "max"),
     (None, "budget", "low"), (None, "premium", "high"), (None, None, "medium"), (0.0, "standard", "medium")],
)
def test_band_for_price(price, legacy, band):
    assert tiers.band_for_price(price, legacy) == band


def test_effective_ceiling_is_most_specific_but_never_above_a_later_layer():
    assert tiers.effective_ceiling(None, "", "high") == "high"
    assert tiers.effective_ceiling("low", "high", "max") == "low"        # request lowers
    assert tiers.effective_ceiling("max", "medium", "high") == "medium"  # tenant caps request
    assert tiers.effective_ceiling("bogus", None, None) == "high"        # default
    assert tiers.effective_ceiling(None, None, None, default="low") == "low"


def test_filter_by_ceiling():
    low = tiers.filter_by_ceiling(POOL, "low")
    assert {c.model for c in low} == {"gpt-4o-mini", "google/gemini-2.5-flash"}
    assert len(tiers.filter_by_ceiling(POOL, "max")) == len(POOL)


# ---------------------------------------------------------------------------
# Step 2 — ranking (cold start + scoring)
# ---------------------------------------------------------------------------

def test_ranking_ignores_thin_cells_and_scores_trusted_ones():
    rows = [
        # task, model, n, ok, avg_latency, avg_cost
        ("code_debug", "gpt-4o", 50, 49, 1200.0, 0.002),
        ("code_debug", "anthropic/claude-sonnet-4.5", 40, 40, 2500.0, 0.004),
        ("code_debug", "gpt-4o-mini", 5, 5, 300.0, 0.0001),  # thin → untrusted
    ]
    r = ranking.build_ranking("t1", rows, min_samples=20)
    assert r.score("code_debug", "gpt-4o-mini") is None
    s_4o = r.score("code_debug", "gpt-4o")
    s_son = r.score("code_debug", "anthropic/claude-sonnet-4.5")
    assert s_4o is not None and s_son is not None
    assert 0 <= s_4o <= 1 and 0 <= s_son <= 1
    # Falls back to the task-agnostic cell when the per-task one is thin.
    assert r.score("translate", "gpt-4o") is not None
    assert r.score("translate", "never-seen") is None


def test_ranking_refresh_failure_keeps_defaults_not_errors(monkeypatch):
    ranking.clear_cache()

    class Boom:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    r = ranking.get_ranking("t-broken", min_samples=20, session_factory=lambda: Boom())
    assert r.cells == {}
    # Cached — a second call within the TTL must not touch the factory.
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return Boom()

    ranking.get_ranking("t-broken", min_samples=20, session_factory=factory)
    assert calls["n"] == 0
    ranking.clear_cache()


# ---------------------------------------------------------------------------
# Step 4 — the router
# ---------------------------------------------------------------------------

def _plan(text, *, hints=None, ceiling="high", rank=None, preferred=(), reasoning=False, pool=POOL):
    return router.route_auto(
        candidates=pool,
        hints=hints,
        messages=[{"role": "user", "content": text}],
        ranking=rank,
        ceiling=ceiling,
        preferred=preferred,
        proven=frozenset({"openai", "openrouter"}),
        reasoning_requested=reasoning,
    )


def test_router_returns_ordered_list_primary_plus_fallbacks():
    plan = _plan("ช่วยหาจุดบกพร่องในโค้ดนี้หน่อย")
    assert plan is not None
    assert plan.task == "code_debug"
    assert plan.primary.model == "anthropic/claude-sonnet-4.5"  # taxonomy default_rank[0] present in pool
    assert plan.primary not in plan.fallbacks
    within = tiers.filter_by_ceiling(POOL, "high")  # opus (xhigh) is above the ceiling
    assert len(plan.ordered) == len(within)
    assert {c.model for c in plan.ordered} == {c.model for c in within}  # never invents a candidate


def test_router_honours_the_tier_ceiling_and_relaxes_when_it_must():
    plan = _plan("ช่วยหาจุดบกพร่องในโค้ดนี้หน่อย", ceiling="low")
    assert plan is not None and plan.tier == "low" and not plan.tier_relaxed
    assert tiers.band_for_candidate(plan.primary) == "low"
    only_premium = [c for c in POOL if c.model == "anthropic/claude-opus-4.1"]
    plan = _plan("hello", ceiling="low", pool=only_premium)
    assert plan is not None and plan.tier_relaxed and plan.primary.model == "anthropic/claude-opus-4.1"


def test_router_prefers_tenant_ranking_over_static_default():
    rows = [("code_debug", "gpt-4o", 100, 100, 900.0, 0.002),
            ("code_debug", "anthropic/claude-sonnet-4.5", 100, 60, 4000.0, 0.005)]
    r = ranking.build_ranking("t", rows, min_samples=20)
    plan = _plan("Find the bug in this traceback", rank=r)
    assert plan is not None and plan.used_ranking
    assert plan.primary.model == "gpt-4o"


def test_router_keeps_reasoning_capability_for_an_explicit_reasoning_request():
    plan = _plan("สวัสดี", reasoning=True)
    assert plan is not None
    assert plan.primary.supports_reasoning
    assert all(c.supports_reasoning for c in plan.ordered[: sum(1 for c in POOL if c.supports_reasoning)])


def test_router_never_fails_because_of_routing(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(taxonomy, "classify_task", boom)
    plan = _plan("anything", preferred=("gpt-4o",))
    assert plan is not None
    assert plan.reason.startswith("router_error:RuntimeError")
    assert plan.primary.model == "gpt-4o"  # admin preference survives
    assert router.route_auto(candidates=[], hints=None, messages=[], ranking=None, ceiling="high") is None


def test_router_default_plan_is_cheapest_when_no_preference_routable():
    plan = router.default_plan(POOL, preferred=("not-in-pool",), reason="x")
    assert plan is not None and tiers.band_for_candidate(plan.primary) == "low"


# ---------------------------------------------------------------------------
# The contract that must never break: explicit models are untouched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    ["gpt-4o-mini", "anthropic/claude-sonnet-4.5", "openrouter/auto", "claude-opus-4-20250514", "some-unknown-id"],
)
async def test_explicit_model_is_never_rewritten_by_any_router(model, monkeypatch):
    from app.services.orchestrator import GatewayOrchestrator as Orchestrator

    orch = Orchestrator.__new__(Orchestrator)  # no __init__: routing chain touches no member on the explicit path

    async def must_not_run(*a, **k):
        raise AssertionError("router ran on an explicit model")

    monkeypatch.setattr(orch, "_auto_route", must_not_run, raising=False)
    logged = {}

    class _Log:
        def info(self, event, **kw):
            logged.update(kw)

        def warning(self, *a, **k):
            pass

    payload = SimpleNamespace(
        model=model,
        routing_hints=RoutingHints(complexity="low", tier="low"),
        reasoning=SimpleNamespace(mode="high"),
        metadata={},
        tools=None,
    )
    ctx = SimpleNamespace(tenant_id="t", project_id="p")
    # The classic P0 trap: a message that matches the keyword 'simple_tasks' rule.
    messages = [{"role": "user", "content": "What is 15*37?"}]
    router_used, rule = await orch._apply_router_chain(ctx, payload, messages, None, _Log())
    assert router_used == "explicit" and rule is None
    assert payload.model == model
    assert logged.get("model") == model


@pytest.mark.asyncio
async def test_auto_goes_through_the_router_and_sets_a_concrete_model(monkeypatch):
    from app.services.orchestrator import GatewayOrchestrator as Orchestrator

    orch = Orchestrator.__new__(Orchestrator)

    async def fake_route(ctx, payload, dict_messages, session, log, *, reasoning_requested=False):
        return router.route_auto(
            candidates=POOL, hints=payload.routing_hints, messages=dict_messages,
            ranking=None, ceiling="medium", reasoning_requested=reasoning_requested,
        )

    monkeypatch.setattr(orch, "_auto_route", fake_route, raising=False)

    class _Log:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

    payload = SimpleNamespace(model="auto", routing_hints=RoutingHints(complexity="low"), reasoning=None, metadata=None, tools=None)
    ctx = SimpleNamespace(tenant_id="t", project_id="p")
    router_used, _ = await orch._apply_router_chain(ctx, payload, [{"role": "user", "content": "แปลเป็นภาษาอังกฤษ: สวัสดี"}], None, _Log())
    assert router_used == "auto"
    assert payload.model != "auto" and payload.model in {c.model for c in POOL}
    assert payload.metadata["auto_task"] == "translate"
    assert payload.metadata["auto_tier"] == "medium"
    assert tiers.band_for_price(None, None) == "medium"  # sanity: ceiling honoured below


@pytest.mark.asyncio
async def test_auto_with_no_usable_provider_is_a_clear_error(monkeypatch):
    from app.core.exceptions import ProviderUnavailableError
    from app.services.orchestrator import GatewayOrchestrator as Orchestrator

    orch = Orchestrator.__new__(Orchestrator)

    async def none_route(*a, **k):
        return None

    monkeypatch.setattr(orch, "_auto_route", none_route, raising=False)

    class _Log:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

    payload = SimpleNamespace(model="auto", routing_hints=None, reasoning=None, metadata=None, tools=None)
    with pytest.raises(ProviderUnavailableError):
        await orch._apply_router_chain(SimpleNamespace(tenant_id="t", project_id="p"), payload, [{"role": "user", "content": "hi"}], None, _Log())


# ---------------------------------------------------------------------------
# Step 4 — the ordered list feeds the fallback chain, cross-provider first
# ---------------------------------------------------------------------------

def test_auto_candidates_become_the_fallback_chain_cross_provider_first():
    from app.services.gateway.fallback_orchestrator import merge_auto_candidates

    provider_of = {
        "gpt-4o": "openai", "gpt-4o-mini": "openai",
        "anthropic/claude-sonnet-4.5": "openrouter", "google/gemini-2.5-flash": "openrouter",
    }
    merged = merge_auto_candidates(
        ["gpt-4o", "gpt-4o-mini", "anthropic/claude-sonnet-4.5", "google/gemini-2.5-flash", "gpt-5.6-terra", "unknown-id"],
        [{"provider": "google", "model": "gemini-1.5-pro"}, {"provider": "openai", "model": "gpt-4o"}],
        original_provider="openai",
        original_model="gpt-5.6-terra",
        resolve=lambda m: provider_of.get(m),
    )
    models = [f"{e['provider']}:{e['model']}" for e in merged]
    # Different provider than the one that just failed comes first …
    assert models[:2] == ["openrouter:anthropic/claude-sonnet-4.5", "openrouter:google/gemini-2.5-flash"]
    # … same-provider after, the static chain last, no duplicates, no
    # unresolvable id, never the model that just failed.
    assert models[2:4] == ["openai:gpt-4o", "openai:gpt-4o-mini"]
    assert models[4:] == ["google:gemini-1.5-pro"]
    assert "gpt-5.6-terra" not in " ".join(models) and "unknown-id" not in " ".join(models)
