"""Unit tests for the Phase-2 model_tiers registry."""

from __future__ import annotations

import pytest

from app.services.providers.model_tiers import (
    TIERS,
    canonical_model,
    get_fallback_chain,
    get_tier,
)


# ---------------------------------------------------------------------------
# dated / versioned model id canonicalization (fallback regression)
# ---------------------------------------------------------------------------

def test_canonical_model_strips_anthropic_date():
    assert canonical_model("claude-sonnet-4-20250514") == "claude-sonnet-4"


def test_canonical_model_strips_openai_date():
    assert canonical_model("gpt-4o-2024-08-06") == "gpt-4o"


def test_canonical_model_keeps_non_date_version_suffix():
    # ``-5`` is a model version, not a date — must NOT be stripped.
    assert canonical_model("claude-sonnet-4-5") == "claude-sonnet-4-5"


def test_dated_anthropic_id_resolves_tier_and_chain():
    # Regression: the auto-router emits the dated id; without
    # canonicalization the tier lookup missed and fallback died with
    # "No fallback chain registered", surfacing an empty response.
    assert get_tier("claude-sonnet-4-20250514") == "standard"
    chain = get_fallback_chain("claude-sonnet-4-20250514", "anthropic")
    pairs = {(c["provider"], c["model"]) for c in chain}
    assert ("openai", "gpt-4o-mini") in pairs


# ---------------------------------------------------------------------------
# get_tier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model,expected_tier",
    [
        ("gpt-4o", "premium"),
        ("claude-opus-4", "premium"),
        ("claude-opus-4-5", "premium"),
        ("gemini-1.5-pro", "premium"),
        ("gpt-4-turbo", "premium"),
        ("gpt-4o-mini", "standard"),
        ("claude-sonnet-4", "standard"),
        ("claude-sonnet-4-5", "standard"),
        ("gemini-1.5-flash", "standard"),
        ("llama-3.1-70b", "standard"),
        ("gpt-3.5-turbo", "fast"),
        ("claude-haiku-4-5", "fast"),
        ("claude-haiku-3", "fast"),
        ("gemini-flash", "fast"),
        ("llama-3.1-8b", "fast"),
    ],
)
def test_get_tier_known_models(model, expected_tier):
    assert get_tier(model) == expected_tier


@pytest.mark.parametrize(
    "model",
    ["", "auto", "openai/gpt-4o", "unknown-model", "GPT-4O"],
)
def test_get_tier_unknown_returns_none(model):
    """Lookup is exact — no aliasing, no case folding."""
    assert get_tier(model) is None


# ---------------------------------------------------------------------------
# get_fallback_chain
# ---------------------------------------------------------------------------

def test_get_fallback_chain_unknown_model_returns_empty():
    assert get_fallback_chain("nope", "openai") == []


def test_get_fallback_chain_excludes_original_provider():
    chain = get_fallback_chain("gpt-4o", "openai")
    providers = [c["provider"] for c in chain]
    assert "openai" not in providers


def test_get_fallback_chain_preserves_tier_order():
    chain = get_fallback_chain("gpt-4o", "openai")
    # Premium tier: openai, anthropic, google, openrouter
    # → anthropic, google, openrouter (after openai is dropped)
    assert [c["provider"] for c in chain] == [
        "anthropic",
        "google",
        "openrouter",
    ]


def test_get_fallback_chain_returns_mapped_models():
    chain = get_fallback_chain("gpt-4o", "openai")
    pairs = {(c["provider"], c["model"]) for c in chain}
    assert ("anthropic", "claude-opus-4") in pairs
    assert ("google", "gemini-1.5-pro") in pairs
    assert ("openrouter", "auto") in pairs


def test_get_fallback_chain_standard_tier_includes_groq():
    chain = get_fallback_chain("gpt-4o-mini", "openai")
    providers = [c["provider"] for c in chain]
    # Standard chain: openai, anthropic, google, groq, openrouter
    assert providers == ["anthropic", "google", "groq", "openrouter"]
    assert any(c["model"] == "llama-3.1-70b-versatile" for c in chain)


def test_get_fallback_chain_fast_tier_prefers_groq():
    chain = get_fallback_chain("gpt-3.5-turbo", "openai")
    # Fast chain: groq, openai, anthropic, openrouter
    # With openai excluded: groq, anthropic, openrouter
    providers = [c["provider"] for c in chain]
    assert providers[0] == "groq"
    assert providers == ["groq", "anthropic", "openrouter"]


def test_get_fallback_chain_from_anthropic_primary():
    chain = get_fallback_chain("claude-sonnet-4", "anthropic")
    providers = [c["provider"] for c in chain]
    assert "anthropic" not in providers
    # Standard chain order after dropping anthropic
    assert providers == ["openai", "google", "groq", "openrouter"]


def test_get_fallback_chain_returns_at_least_three_candidates_for_top_models():
    """The marquee tier-flagship models all wire ≥3 fallbacks. Operators
    rely on this to keep an inbound incident from collapsing the whole
    chain into a single alternate provider."""
    for model in ("gpt-4o", "claude-opus-4-5", "gemini-1.5-pro"):
        primary_provider = {
            "gpt-4o": "openai",
            "claude-opus-4-5": "anthropic",
            "gemini-1.5-pro": "google",
        }[model]
        chain = get_fallback_chain(model, primary_provider)
        assert len(chain) >= 3, f"{model} has only {len(chain)} fallbacks"


def test_llama_70b_has_fallback_chain():
    """Audit gap-fix: the canonical Groq Llama 70b name now wires fallbacks."""
    chain = get_fallback_chain("llama-3.1-70b", "groq")
    assert chain != []
    providers = [c["provider"] for c in chain]
    assert "groq" not in providers
    # Standard tier minus groq → openai / anthropic / google / openrouter
    # in tier.fallback_chain order.
    assert providers == ["openai", "anthropic", "google", "openrouter"]


def test_llama_70b_versatile_has_fallback_chain():
    """Long-form Groq model name also resolves."""
    chain = get_fallback_chain("llama-3.1-70b-versatile", "groq")
    assert chain != []
    pairs = {(c["provider"], c["model"]) for c in chain}
    assert ("openai", "gpt-4o-mini") in pairs
    assert ("anthropic", "claude-sonnet-4") in pairs


def test_llama_8b_has_fallback_chain():
    """Fast-tier canonical Llama name resolves a chain."""
    chain = get_fallback_chain("llama-3.1-8b", "groq")
    assert chain != []
    providers = [c["provider"] for c in chain]
    assert "groq" not in providers
    # Fast tier chain minus groq → openai / anthropic / openrouter
    assert providers == ["openai", "anthropic", "openrouter"]


def test_llama_8b_instant_has_fallback_chain():
    """Long-form fast Groq model name also resolves."""
    chain = get_fallback_chain("llama-3.1-8b-instant", "groq")
    assert chain != []
    pairs = {(c["provider"], c["model"]) for c in chain}
    assert ("openai", "gpt-3.5-turbo") in pairs
    assert ("anthropic", "claude-haiku-4-5") in pairs


def test_tiers_registry_invariants():
    """Every model_map entry's models live in the same tier's ``models`` list."""
    for tier_name, tier in TIERS.items():
        models_set = set(tier["models"])
        for original_model in tier["model_map"]:
            assert original_model in models_set, (
                f"{tier_name}.model_map references '{original_model}' "
                f"which is not in {tier_name}.models"
            )
            # Every provider in model_map[X] must appear in the tier's
            # fallback_chain so the chain lookup actually emits it.
            for provider in tier["model_map"][original_model]:
                assert provider in tier["fallback_chain"], (
                    f"{tier_name}.model_map[{original_model}] maps to "
                    f"provider '{provider}' which is not in "
                    f"{tier_name}.fallback_chain"
                )
