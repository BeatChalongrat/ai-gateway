"""Group B / B2 — every curated chat model has a fallback chain.

Asserts the curated catalog all resolve to a non-empty, cross-vendor,
length-bounded chain via ``get_fallback_chain``, and that the chain
resolves identically whether the model is addressed by its bare alias or
its OpenRouter ``vendor/model`` id.
"""

from __future__ import annotations

import pytest

from app.services.providers.model_tiers import (
    CURATED_FALLBACKS,
    _alias,
    get_fallback_chain,
)

# (addressed_id, original_provider) pairs that the chat path actually
# emits — bare alias for direct providers, vendor/model for OpenRouter.
CURATED_MODELS = [
    # NOTE: bare legacy-tier names (e.g. ``claude-sonnet-4``) intentionally
    # keep their generic tier chain, so they're not listed here — only the
    # OpenRouter chat ids + off-tier models route through CURATED_FALLBACKS.
    ("anthropic/claude-sonnet-4", "openrouter"),
    ("anthropic/claude-sonnet-4.5", "openrouter"),
    ("anthropic/claude-3.5-haiku", "openrouter"),
    ("anthropic/claude-opus-4.1", "openrouter"),
    ("openai/gpt-4o", "openrouter"),
    ("openai/gpt-4o-mini", "openrouter"),
    ("google/gemini-2.5-pro", "openrouter"),
    ("google/gemini-2.5-flash", "openrouter"),
    ("deepseek/deepseek-r1", "openrouter"),
    ("deepseek/deepseek-chat", "openrouter"),
    ("meta-llama/llama-3.3-70b-instruct", "openrouter"),
    ("o3-mini", "openai"),
    ("o4-mini", "openai"),
]


@pytest.mark.parametrize("model,provider", CURATED_MODELS)
def test_every_curated_model_has_a_chain(model, provider):
    chain = get_fallback_chain(model, provider)
    assert chain, f"{model} has no fallback chain"
    assert 1 <= len(chain) <= 3, f"{model} chain length {len(chain)} out of bounds"


@pytest.mark.parametrize("model,provider", CURATED_MODELS)
def test_chain_hops_are_cross_vendor(model, provider):
    """No hop may be the same underlying model the request started on."""
    origin = _alias(model)
    chain = get_fallback_chain(model, provider)
    for hop in chain:
        assert _alias(hop["model"]) != origin, (
            f"{model} falls back to itself via {hop}"
        )


def test_alias_unifies_direct_and_openrouter_ids():
    assert _alias("anthropic/claude-sonnet-4") == "claude-sonnet-4"
    assert _alias("claude-sonnet-4-20250514") == "claude-sonnet-4"
    assert _alias("openai/gpt-4o") == "gpt-4o"


def test_direct_and_openrouter_resolve_same_curated_chain():
    # For a model the legacy tier table does NOT own (deepseek-r1), the
    # bare alias and the OpenRouter vendor/model id resolve the same
    # curated chain. Legacy bare names (e.g. ``claude-sonnet-4``) keep
    # their tier chain by design, so equivalence only holds off-tier.
    direct = get_fallback_chain("deepseek-r1", "deepseek")
    via_or = get_fallback_chain("deepseek/deepseek-r1", "openrouter")
    assert [h["model"] for h in direct] == [h["model"] for h in via_or]


def test_budget_models_do_not_upgrade_to_premium():
    """A budget/fast model must not silently fall back to a premium one."""
    premium_aliases = {"claude-opus-4", "claude-opus-4.1", "gpt-4", "gpt-4-turbo"}
    for alias in ("gpt-4o-mini", "deepseek-chat", "claude-3.5-haiku",
                  "gemini-2.5-flash", "llama-3.3-70b-instruct"):
        chain = CURATED_FALLBACKS[alias]
        for hop in chain:
            assert _alias(hop["model"]) not in premium_aliases, (
                f"{alias} upgrades to premium via {hop}"
            )
