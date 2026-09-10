"""FIX102 Part A — the router is told how big the request is.

chat-edge budgets an ``auto`` conversation before the gateway picks the
model, so the budget cannot follow the model that WILL answer. It follows
the one that did, and sends two hints: ``min_context_tokens`` (what the
compiled request needs — hard, like tools) and ``preferred_context_tokens``
(the window it was budgeted against — soft, after the tier ceiling).

Before this, nothing in routing looked at a context window at all: a
tool-bearing turn could land on gpt-4 (8,192) with an 11,000-token request.
"""

from __future__ import annotations

import random

import pytest

from app.domain.routing import ModelCandidate
from app.schemas.public import RoutingHints
from app.services.auto_router import router


def _c(model: str, window: int, *, price: float):
    return ModelCandidate(
        provider=f"p-{model}", model=model, supports_streaming=True, supports_tools=True,
        max_context_tokens=window, cost_tier="standard", cost_per_input_token=price / 1_000_000,
    )


SMALL = _c("small-8k", 8192, price=0.10)
MID = _c("mid-128k", 128_000, price=2.00)
BIG = _c("big-272k", 272_000, price=0.25)
POOL = [SMALL, MID, BIG]


def _route(pool, hints, *, ceiling="high"):
    return router.route_auto(
        candidates=pool, hints=hints,
        messages=[{"role": "user", "content": "สรุปหัวข้อที่คุยกันให้หน่อย"}],
        ranking=None, ceiling=ceiling, require_tools=True, rng=random.Random(0),
    )


def _names(plan):
    return [plan.primary.model] + [c.model for c in plan.fallbacks]


def test_without_context_hints_every_window_is_still_a_candidate():
    assert set(_names(_route(POOL, RoutingHints(has_tools=True)))) == {"small-8k", "mid-128k", "big-272k"}


def test_a_request_larger_than_a_window_never_reaches_that_model_even_as_a_fallback():
    plan = _route(POOL, RoutingHints(has_tools=True, min_context_tokens=11_000))
    assert "small-8k" not in _names(plan)
    assert "context>=11000" in plan.reason


def test_nothing_can_hold_it_is_no_capable_model_that_names_context():
    with pytest.raises(router.NoCapableModelError) as ei:
        _route(POOL, RoutingHints(has_tools=True, min_context_tokens=300_000))
    assert ei.value.missing == ["context"]


def test_the_preferred_window_keeps_the_conversation_on_a_model_that_large():
    plan = _route(POOL, RoutingHints(has_tools=True, min_context_tokens=11_000, preferred_context_tokens=128_000))
    assert set(_names(plan)) == {"mid-128k", "big-272k"}
    assert "window>=128000" in plan.reason


def test_the_preference_never_beats_the_tier_ceiling():
    # "low" keeps only the ≤ $0.50 models: small-8k and big-272k. Preferring
    # 128,000 then leaves big-272k; mid-128k stays out on price.
    plan = _route(POOL, RoutingHints(has_tools=True, preferred_context_tokens=128_000), ceiling="low")
    assert _names(plan) == ["big-272k"]
    # And when nothing under the ceiling is that large, the preference yields.
    plan = _route([SMALL, MID], RoutingHints(has_tools=True, preferred_context_tokens=128_000), ceiling="low")
    assert _names(plan) == ["small-8k"]


def test_the_hard_need_is_checked_before_the_router_can_fall_back_to_a_default():
    # A broken ranking makes route_auto collapse to default_plan; that plan
    # must be built from the pool that can hold the request.
    class Boom:
        def __getattr__(self, _):
            raise RuntimeError("ranking exploded")

    plan = router.route_auto(
        candidates=POOL, hints=RoutingHints(has_tools=True, min_context_tokens=11_000),
        messages=[{"role": "user", "content": "hi"}], ranking=Boom(), ceiling="high",
        require_tools=True, rng=random.Random(0),
    )
    assert "small-8k" not in _names(plan)


def test_negative_or_missing_hints_are_ignored():
    assert set(_names(_route(POOL, None))) == {"small-8k", "mid-128k", "big-272k"}
    with pytest.raises(Exception):
        RoutingHints(min_context_tokens=-1)
