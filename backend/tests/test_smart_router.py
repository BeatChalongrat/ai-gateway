"""Unit tests for the provider-agnostic smart model selector.

Uses synthetic ``ModelCandidate`` pools so the assertions don't depend
on the live model registry / DB rows.
"""

from __future__ import annotations

from app.domain.routing import ModelCandidate
from app.schemas.public import RoutingHints
from app.services import smart_router


def _c(provider, model, *, cost="standard", lat="standard",
       vision=False, reasoning=False, tools=True, streaming=True):
    return ModelCandidate(
        provider=provider,
        model=model,
        supports_streaming=streaming,
        supports_tools=tools,
        supports_vision=vision,
        cost_tier=cost,
        latency_tier=lat,
        supports_reasoning=reasoning,
    )


# A small, mixed-provider pool (no provider is special-cased).
POOL = [
    _c("openai", "gpt-4o-mini", cost="budget", lat="fast", vision=True),
    _c("openai", "gpt-4o", cost="standard", vision=True),
    _c("openai", "o4-mini", cost="standard", reasoning=True),
    _c("anthropic", "claude-sonnet-4", cost="standard", vision=True, reasoning=True),
    _c("google", "gemini-1.5-pro", cost="premium", vision=True),
    _c("groq", "llama-3.3-70b", cost="budget", lat="fast"),
]


def test_empty_pool_returns_none():
    assert smart_router.select_model(RoutingHints(complexity="low"), []) is None


def test_low_complexity_picks_cheapest():
    sel = smart_router.select_model(RoutingHints(complexity="low"), POOL)
    assert sel is not None
    assert sel.candidate.cost_tier == "budget"
    # Two budget models tie on cost → fast latency then model-id breaks it.
    assert sel.candidate.model in {"gpt-4o-mini", "llama-3.3-70b"}


def test_high_complexity_prefers_flagship_then_cheapest():
    sel = smart_router.select_model(
        RoutingHints(complexity="high", has_code=True), POOL
    )
    # Budget tier excluded (flagship preference) → cheapest non-budget wins.
    assert sel.candidate.cost_tier != "budget"
    assert sel.candidate.model in {"gpt-4o", "o4-mini", "claude-sonnet-4"}


def test_needs_vision_hard_filter():
    no_vision_pool = [
        _c("groq", "llama-3.3-70b", cost="budget"),
        _c("openai", "gpt-4o", cost="standard", vision=True),
    ]
    sel = smart_router.select_model(
        RoutingHints(complexity="low", needs_vision=True), no_vision_pool
    )
    assert sel.candidate.supports_vision is True
    assert sel.candidate.model == "gpt-4o"


def test_needs_reasoning_hard_filter():
    sel = smart_router.select_model(
        RoutingHints(complexity="high", needs_reasoning=True), POOL
    )
    assert sel.candidate.supports_reasoning is True
    assert sel.candidate.model in {"o4-mini", "claude-sonnet-4"}


def test_vision_filter_relaxes_when_no_vision_model():
    """Graceful degradation: a vision request still gets an answer when
    no vision model is available, rather than failing."""
    pool = [_c("groq", "llama-3.3-70b", cost="budget")]
    sel = smart_router.select_model(
        RoutingHints(complexity="low", needs_vision=True), pool
    )
    assert sel is not None
    assert sel.candidate.model == "llama-3.3-70b"


def test_provider_agnostic_no_hardcoded_default():
    """Selection is driven purely by tier/capability, not provider name:
    drop OpenAI entirely and a different provider still wins on merit."""
    pool = [
        _c("google", "gemini-flash", cost="budget", lat="fast"),
        _c("anthropic", "claude-sonnet-4", cost="standard", vision=True),
    ]
    sel = smart_router.select_model(RoutingHints(complexity="low"), pool)
    assert sel.candidate.provider == "google"  # cheapest, no OpenAI bias


def test_no_hints_picks_cheapest():
    sel = smart_router.select_model(None, POOL)
    assert sel is not None
    assert sel.candidate.cost_tier == "budget"


def test_proven_provider_breaks_cost_tie():
    """Two models tie on cost/latency; the one whose provider key is
    proven-valid wins so we don't elect a model on an untested key."""
    pool = [
        _c("anthropic", "claude-sonnet-4", cost="standard", reasoning=True),
        _c("openai", "o3", cost="standard", reasoning=True),
    ]
    # Without proof, alphabetical → claude-sonnet-4.
    assert smart_router.select_model(
        RoutingHints(complexity="high", needs_reasoning=True), pool
    ).candidate.model == "claude-sonnet-4"
    # With openai proven, o3 wins despite sorting later alphabetically.
    sel = smart_router.select_model(
        RoutingHints(complexity="high", needs_reasoning=True),
        pool,
        proven_providers=frozenset({"openai"}),
    )
    assert sel.candidate.provider == "openai"
    assert sel.candidate.model == "o3"


def test_direct_model_id_beats_aggregator_on_tie():
    """A bare first-party id beats an aggregator ("/") id at equal
    cost/latency/proof — the aggregator may 404 on that model."""
    pool = [
        _c("openrouter", "anthropic/claude-3.7-sonnet:thinking",
           cost="standard", reasoning=True),
        _c("openai", "o3", cost="standard", reasoning=True),
    ]
    sel = smart_router.select_model(
        RoutingHints(complexity="high", needs_reasoning=True), pool
    )
    assert sel.candidate.model == "o3"


def test_cheap_tier_ranks_as_cheapest():
    pool = [
        _c("x", "standard-model", cost="standard"),
        _c("y", "cheap-model", cost="cheap"),
    ]
    sel = smart_router.select_model(RoutingHints(complexity="low"), pool)
    assert sel.candidate.model == "cheap-model"
