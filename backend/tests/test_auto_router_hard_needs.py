"""FIX47 Part B — a capability the request NEEDS is not a preference.

``_relax`` (FIX35) returned the whole pool whenever a filter emptied it,
for tools / vision / reasoning alike. For tools and vision that meant a
request the pool could not serve was routed to a model guaranteed to
fail — the owner saw it as "Model unavailable, please try again" on a
tools turn that ``auto`` had sent to deepseek-r1 (FIX42 trap).

Now: tools in the payload / an image in the messages are HARD — an empty
pool is ``no_capable_model`` (logged with the pool that was searched).
Reasoning stays SOFT — the gateway already answers without thinking and
sends a ``reasoning_notice`` (checked below against the real frame name).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.exceptions import NoCapableModelError
from app.domain.routing import ModelCandidate
from app.schemas.public import RoutingHints
from app.services.auto_router import router

BACKEND = Path(__file__).resolve().parents[1]


def _c(provider, model, *, tools=False, vision=False, reasoning=False, price=1.0):
    return ModelCandidate(
        provider=provider, model=model, supports_streaming=True,
        supports_tools=tools, supports_vision=vision, supports_reasoning=reasoning,
        cost_tier="standard", cost_per_input_token=price / 1_000_000,
    )


NO_TOOLS_POOL = [
    _c("openrouter", "deepseek/deepseek-r1", reasoning=True, price=0.55),
    _c("openrouter", "some/prose-only", price=0.2),
]
MIXED_POOL = NO_TOOLS_POOL + [_c("openai", "gpt-4o-mini", tools=True, vision=True, price=0.15)]


def _route(pool, **kw):
    return router.route_auto(
        candidates=pool, hints=kw.pop("hints", None),
        messages=[{"role": "user", "content": "เปิดไฟล์ report.xlsx แล้วสรุปให้หน่อย"}],
        ranking=None, ceiling="high", **kw,
    )


# ── hard: tools ────────────────────────────────────────────────────────────


def test_tools_request_with_no_tool_capable_candidate_is_an_error_not_a_broken_model():
    with pytest.raises(router.NoCapableModelError) as ei:
        _route(NO_TOOLS_POOL, require_tools=True)
    assert ei.value.missing == ["tools"]
    assert ei.value.pool_size == 2
    assert "deepseek/deepseek-r1" in ei.value.pool


def test_tools_request_lands_only_on_tool_capable_candidates():
    plan = _route(MIXED_POOL, require_tools=True)
    assert plan is not None
    assert plan.primary.supports_tools
    assert all(c.supports_tools for c in plan.ordered)


def test_hard_need_is_not_swallowed_by_the_never_fail_wrapper(monkeypatch):
    # route_auto wraps every step in "never fail because of routing";
    # the hard-need error must pass through it, not collapse to default_plan.
    with pytest.raises(router.NoCapableModelError):
        _route(NO_TOOLS_POOL, require_tools=True, preferred=("deepseek/deepseek-r1",))


# ── hard: vision ───────────────────────────────────────────────────────────


def test_image_request_with_no_vision_candidate_is_an_error():
    with pytest.raises(router.NoCapableModelError) as ei:
        _route(NO_TOOLS_POOL, require_vision=True)
    assert ei.value.missing == ["vision"]


def test_both_hard_needs_missing_are_named_together():
    with pytest.raises(router.NoCapableModelError) as ei:
        _route(NO_TOOLS_POOL, require_tools=True, require_vision=True)
    assert ei.value.missing == ["vision", "tools"]


# ── soft: reasoning, and a task's preferred capability ─────────────────────


def test_reasoning_is_soft_because_the_gateway_downgrades_with_a_notice():
    # The claim "reasoning is soft" rests on the gateway answering without
    # thinking and telling the client — that frame exists in the code.
    src = (BACKEND / "app" / "api" / "routes" / "public_chat.py").read_text(encoding="utf-8")
    assert '"reasoning_notice"' in src
    plan = _route([_c("openrouter", "some/prose-only", tools=True)], reasoning_requested=True)
    assert plan is not None and not plan.primary.supports_reasoning


def test_a_hint_alone_stays_a_preference():
    # The hint says tools but the caller did not mark the need as hard:
    # the old relaxing behaviour, so an older client that sets hints
    # loosely is not broken. The orchestrator derives the hard flags
    # from the payload itself.
    plan = _route(NO_TOOLS_POOL, hints=RoutingHints(has_tools=True))
    assert plan is not None


# ── the orchestrator derives hard needs from the payload ───────────────────


@pytest.mark.asyncio
async def test_orchestrator_turns_the_router_error_into_no_capable_model(monkeypatch):
    from app.services.orchestrator import GatewayOrchestrator as Orchestrator
    from app.services.providers import model_registry

    orch = Orchestrator.__new__(Orchestrator)
    orch.provider_key_service = SimpleNamespace()  # session is None → not consulted
    monkeypatch.setattr(model_registry, "get_all_providers", lambda: ["openrouter"])
    monkeypatch.setattr(model_registry, "get_all_candidates", lambda: list(NO_TOOLS_POOL))

    async def healthy(_p):
        return False

    monkeypatch.setattr("app.services.gateway.fallback_orchestrator.is_provider_unhealthy", healthy)
    from app.services import routing_settings
    monkeypatch.setattr(routing_settings, "get_auto_preference", lambda _s: [])
    from app.services.auto_router import ranking as _ranking
    monkeypatch.setattr(_ranking, "get_ranking", lambda *_a, **_k: None)

    logged: dict = {}

    class _Log:
        def info(self, *a, **k):
            pass

        def warning(self, ev, **k):
            logged[ev] = k

    payload = SimpleNamespace(
        model="auto", routing_hints=None, reasoning=None, metadata=None,
        tools=[{"type": "function", "function": {"name": "read_file"}}],
        messages=[{"role": "user", "content": "เปิดไฟล์ให้หน่อย"}],
    )
    ctx = SimpleNamespace(tenant_id="t", project_id="p")
    with pytest.raises(NoCapableModelError) as ei:
        await orch._auto_route(ctx, payload, payload.messages, None, _Log())
    assert ei.value.code == "no_capable_model"
    assert ei.value.details == {"missing": ["tools"], "pool_size": 2}
    # the owner learns what the catalogue lacks
    assert logged["auto_routing_no_capable_model"]["missing"] == ["tools"]
    assert "deepseek/deepseek-r1" in logged["auto_routing_no_capable_model"]["pool"]


def test_no_capable_model_is_a_400_and_not_fallback_eligible():
    from app.middleware.error_handler import _status_for
    from app.services.gateway.fallback_orchestrator import is_fallback_eligible

    err = NoCapableModelError(details={"missing": ["tools"]})
    assert _status_for(err) == 400
    assert not is_fallback_eligible(err)


def test_the_never_fail_collapse_is_also_capability_bound(monkeypatch):
    # A router-internal error collapses to default_plan; that plan must not
    # contain a tools-blind model on a tools request either.
    from app.services.auto_router import taxonomy

    def boom(*a, **k):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(taxonomy, "classify_task", boom)
    plan = _route(MIXED_POOL, require_tools=True, preferred=("deepseek/deepseek-r1",))
    assert plan is not None and plan.reason.startswith("router_error:")
    assert all(c.supports_tools for c in plan.ordered)
