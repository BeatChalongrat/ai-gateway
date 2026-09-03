"""Model-tier registry powering Phase-2 cross-provider fallback.

A *tier* groups models with comparable cost / latency / capability so a
failure on the primary candidate can be answered by another vendor's
model of the same class instead of dropping the request.

``TIERS`` is the authoritative table. ``get_tier`` resolves a model to
its tier name; ``get_fallback_chain`` produces an ordered list of
``{"provider", "model"}`` candidates for the FallbackOrchestrator.

The chain is deliberately small — at most four candidates per tier so
a single inbound failure cannot fan out into a wide rescue sweep.
"""

from __future__ import annotations

import re
from typing import Iterable

# Providers ship dated/versioned model ids (Anthropic ``-YYYYMMDD``,
# OpenAI ``-YYYY-MM-DD``) while the tier table is keyed by the stable
# short alias (``claude-sonnet-4``, ``gpt-4o``). Strip a trailing date
# stamp so a concrete id like ``claude-sonnet-4-20250514`` resolves to
# its tier. Only an 8-digit run or a ``YYYY-MM-DD`` triplet is removed,
# so non-date version suffixes (``claude-sonnet-4-5``) are preserved.
_MODEL_DATE_SUFFIX = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")


def canonical_model(model: str) -> str:
    """Strip a trailing date stamp from a provider model id."""
    return _MODEL_DATE_SUFFIX.sub("", model)


def _strip_vendor_prefix(model: str) -> str:
    """Reduce an OpenRouter ``vendor/model`` id to the bare model alias.

    OpenRouter routes by ``vendor/model`` (``anthropic/claude-sonnet-4``)
    while direct providers use the bare alias (``claude-sonnet-4``).
    Dropping the leading ``vendor/`` segment lets a single chain table be
    keyed by one alias and resolve regardless of how the caller addressed
    the model. ``openrouter/auto`` reduces to ``auto`` (no tier — fine).
    """
    return model.split("/", 1)[1] if "/" in model else model


def _alias(model: str) -> str:
    """Normalised lookup key: vendor-prefix-stripped + date-stripped."""
    return canonical_model(_strip_vendor_prefix(model))


# ---------------------------------------------------------------------------
# B2 — explicit, tier-matched fallback chains for the curated chat catalog.
#
# Keyed by the bare model alias (see ``_alias``) so BOTH the direct
# provider id (``claude-sonnet-4``) and the OpenRouter ``vendor/model`` id
# (``anthropic/claude-sonnet-4``) resolve to the same chain. Each hop is
# a DIFFERENT underlying vendor model (or a same-vendor newer revision)
# and never a more expensive tier, satisfying the cross-provider +
# no-silent-upgrade rules. Chains are 2 deep (3 total attempts including
# the primary).
#
# FIX51 Part 4 (P1-F) — chains must span ACCOUNTS, not just vendors.
# The original table sent every hop through the OpenRouter adapter
# ("the provider holding a live key"), which made both rescue hops die
# together the day that one account ran dry — the "cross-provider" rule
# had quietly become cross-MODEL on a single account. Every chain now
# carries at least one hop on a different account (DeepSeek direct or
# OpenAI direct); ``test_fallback_orchestrator.py`` fails any chain
# that collapses back to a single provider.
# ---------------------------------------------------------------------------
_OR = "openrouter"
_DS = "deepseek"
_OA = "openai"


def _c(provider: str, model: str) -> dict[str, str]:
    return {"provider": provider, "model": model}


# Cross-account rescue hops: DeepSeek direct is the second account in
# this deployment. v4-pro stands in for the standard/premium class,
# v4-flash for the budget/fast class (down, never up).
_DS_STD = _c(_DS, "deepseek-v4-pro")
_DS_FAST = _c(_DS, "deepseek-v4-flash")

CURATED_FALLBACKS: dict[str, list[dict[str, str]]] = {
    # Standard / flagship tier — peers stay standard; hop 2 crosses to
    # the DeepSeek account.
    "claude-sonnet-4": [_c(_OR, "anthropic/claude-sonnet-4.5"), _DS_STD],
    "claude-sonnet-4.5": [_c(_OR, "anthropic/claude-sonnet-4"), _DS_STD],
    "gpt-4o": [_c(_OR, "anthropic/claude-sonnet-4"), _DS_STD],
    "gemini-2.5-pro": [_c(_OR, "anthropic/claude-sonnet-4"), _DS_STD],
    "deepseek-r1": [_c(_OR, "openai/gpt-4o"), _DS_STD],
    # Premium → premium/standard (down, never up).
    "claude-opus-4.1": [_c(_OR, "anthropic/claude-sonnet-4.5"), _DS_STD],
    "claude-opus-4": [_c(_OR, "anthropic/claude-sonnet-4.5"), _DS_STD],
    # Reasoning peers fall back to another reasoning model first, then
    # cross accounts (v4-pro answers without thinking — a downgrade the
    # fallback notice already reports, same as any vendor swap).
    "o3-mini": [_c(_OR, "deepseek/deepseek-r1"), _DS_STD],
    "o4-mini": [_c(_OR, "deepseek/deepseek-r1"), _DS_STD],
    # Budget / fast tier — peers stay budget/fast (no upgrade).
    "claude-3.5-haiku": [_c(_OR, "openai/gpt-4o-mini"), _DS_FAST],
    "gpt-4o-mini": [_c(_OR, "anthropic/claude-3.5-haiku"), _DS_FAST],
    "gemini-2.5-flash": [_c(_OR, "openai/gpt-4o-mini"), _DS_FAST],
    "deepseek-chat": [_c(_OR, "openai/gpt-4o-mini"), _DS_FAST],
    "llama-3.3-70b-instruct": [_DS_FAST, _c(_OR, "openai/gpt-4o-mini")],
    # The DeepSeek-direct primaries themselves rescue AWAY from the
    # DeepSeek account (OpenRouter, then OpenAI direct as the third
    # account).
    "deepseek-v4-pro": [_c(_OR, "openai/gpt-4o"), _c(_OA, "gpt-4o-mini")],
    "deepseek-v4-flash": [
        _c(_OR, "meta-llama/llama-3.3-70b-instruct"),
        _c(_OA, "gpt-4o-mini"),
    ],
}

# ---------------------------------------------------------------------------
# Tier table — premium / standard / fast.
#
# ``models``        : list of canonical model names that live in this tier.
# ``fallback_chain``: ordered preference of providers to try when the
#                     primary fails (the original provider is filtered
#                     out at lookup time, so it can stay in the list).
# ``model_map``     : per-original-model mapping of ``provider -> model``
#                     describing what we ask each fallback provider to
#                     answer with. ``"auto"`` is a sentinel honoured by
#                     OpenRouter's adapter.
# ---------------------------------------------------------------------------
TIERS: dict[str, dict] = {
    "premium": {
        "models": [
            "gpt-4o",
            "claude-opus-4",
            "claude-opus-4-5",
            "gemini-1.5-pro",
            "gpt-4-turbo",
        ],
        "fallback_chain": ["openai", "anthropic", "google", "openrouter"],
        "model_map": {
            "gpt-4o": {
                "anthropic": "claude-opus-4",
                "google": "gemini-1.5-pro",
                "openrouter": "auto",
            },
            "claude-opus-4": {
                "openai": "gpt-4o",
                "google": "gemini-1.5-pro",
                "openrouter": "auto",
            },
            "claude-opus-4-5": {
                "openai": "gpt-4o",
                "google": "gemini-1.5-pro",
                "openrouter": "auto",
            },
            "gemini-1.5-pro": {
                "openai": "gpt-4o",
                "anthropic": "claude-opus-4",
                "openrouter": "auto",
            },
            "gpt-4-turbo": {
                "anthropic": "claude-opus-4",
                "google": "gemini-1.5-pro",
                "openrouter": "auto",
            },
        },
    },
    "standard": {
        "models": [
            "gpt-4o-mini",
            "claude-sonnet-4",
            "claude-sonnet-4-5",
            "gemini-1.5-flash",
            "llama-3.1-70b",
            "llama-3.1-70b-versatile",
        ],
        "fallback_chain": [
            "openai",
            "anthropic",
            "google",
            "groq",
            "openrouter",
        ],
        "model_map": {
            "gpt-4o-mini": {
                "anthropic": "claude-sonnet-4",
                "google": "gemini-1.5-flash",
                "groq": "llama-3.1-70b-versatile",
                "openrouter": "auto",
            },
            "claude-sonnet-4": {
                "openai": "gpt-4o-mini",
                "google": "gemini-1.5-flash",
                "groq": "llama-3.1-70b-versatile",
                "openrouter": "auto",
            },
            "claude-sonnet-4-5": {
                "openai": "gpt-4o-mini",
                "google": "gemini-1.5-flash",
                "groq": "llama-3.1-70b-versatile",
                "openrouter": "auto",
            },
            "gemini-1.5-flash": {
                "openai": "gpt-4o-mini",
                "anthropic": "claude-sonnet-4",
                "groq": "llama-3.1-70b-versatile",
                "openrouter": "auto",
            },
            # Llama on Groq is the primary; the chain rescues to a
            # mid-cost vendor when Groq has an incident. The canonical
            # short name (``llama-3.1-70b``) and the Groq-specific
            # long name (``-versatile``) both map to the same chain so
            # callers that route by either name get equivalent rescue.
            "llama-3.1-70b": {
                "openai": "gpt-4o-mini",
                "anthropic": "claude-sonnet-4",
                "google": "gemini-1.5-flash",
                "openrouter": "auto",
            },
            "llama-3.1-70b-versatile": {
                "openai": "gpt-4o-mini",
                "anthropic": "claude-sonnet-4",
                "google": "gemini-1.5-flash",
                "openrouter": "auto",
            },
        },
    },
    "fast": {
        "models": [
            "gpt-3.5-turbo",
            "claude-haiku-4-5",
            "claude-haiku-3",
            "gemini-flash",
            "llama-3.1-8b",
            "llama-3.1-8b-instant",
        ],
        "fallback_chain": ["groq", "openai", "anthropic", "openrouter"],
        "model_map": {
            "gpt-3.5-turbo": {
                "anthropic": "claude-haiku-4-5",
                "groq": "llama-3.1-8b-instant",
                "openrouter": "auto",
            },
            "claude-haiku-4-5": {
                "openai": "gpt-3.5-turbo",
                "groq": "llama-3.1-8b-instant",
                "openrouter": "auto",
            },
            "claude-haiku-3": {
                "openai": "gpt-3.5-turbo",
                "groq": "llama-3.1-8b-instant",
                "openrouter": "auto",
            },
            "gemini-flash": {
                "openai": "gpt-3.5-turbo",
                "anthropic": "claude-haiku-4-5",
                "groq": "llama-3.1-8b-instant",
                "openrouter": "auto",
            },
            # Fast-tier Llama: same dual-name convention as the
            # standard-tier 70b entry — the short canonical name and
            # the Groq-specific ``-instant`` long name both wire to
            # the same cross-vendor rescue list.
            "llama-3.1-8b": {
                "openai": "gpt-3.5-turbo",
                "anthropic": "claude-haiku-4-5",
                "openrouter": "auto",
            },
            "llama-3.1-8b-instant": {
                "openai": "gpt-3.5-turbo",
                "anthropic": "claude-haiku-4-5",
                "openrouter": "auto",
            },
        },
    },
}


def get_tier(model: str) -> str | None:
    """Return the tier name for ``model``, or ``None`` if unknown.

    Matches the exact id first, then a date-stripped form (so a dated
    provider id like ``claude-sonnet-4-20250514`` resolves to the
    ``claude-sonnet-4`` tier). Callers that pass ``"auto"`` or a
    provider-prefixed name still get ``None`` — fallback only applies to
    concrete, registered models.
    """
    canon = canonical_model(model)
    for tier_name, tier in TIERS.items():
        if model in tier["models"] or canon in tier["models"]:
            return tier_name
    return None


def get_fallback_chain(
    model: str, original_provider: str
) -> list[dict[str, str]]:
    """Return ordered fallback candidates for ``(model, original_provider)``.

    Each entry is ``{"provider": str, "model": str}``. The original
    provider is filtered out so the chain is purely *cross-provider*.
    Providers without an entry in the tier's ``model_map[model]`` are
    skipped — the registry only lists vendor pairings that have been
    vetted as equivalent-class, so a missing entry means "don't try
    that vendor for this model".

    Returns ``[]`` when:
      * ``model`` is not in any tier, or
      * ``model`` has no ``model_map`` entries (the tier knows about it
        but no fallbacks have been wired yet).
    """
    # B2: curated chains apply ONLY to models the legacy tier table does
    # not already own — the OpenRouter ``vendor/model`` chat ids and newer
    # models (deepseek-r1, gemini-2.5-*, claude-3.5-haiku, o-mini, …).
    # Legacy bare names (``gpt-4o``, ``claude-sonnet-4``) keep their
    # generic tier behavior so the existing tier contract is unchanged.
    # A self-hop (same provider + same alias) is dropped so the curated
    # chain stays a pure alternative list.
    if get_tier(model) is None:
        alias = _alias(model)
        curated = CURATED_FALLBACKS.get(alias)
        if curated:
            out = [
                dict(c)
                for c in curated
                if not (
                    c["provider"] == original_provider
                    and _alias(c["model"]) == alias
                )
            ]
            if out:
                return out

    tier_name = get_tier(model)
    if tier_name is None:
        return []

    tier = TIERS[tier_name]
    # Match the model_map by exact id first, then date-stripped alias,
    # so dated provider ids share the alias's wired fallbacks.
    model_map: dict[str, str] = (
        tier["model_map"].get(model)
        or tier["model_map"].get(canonical_model(model))
        or {}
    )
    if not model_map:
        return []

    chain: Iterable[str] = tier["fallback_chain"]
    candidates: list[dict[str, str]] = []
    for provider in chain:
        if provider == original_provider:
            continue
        fallback_model = model_map.get(provider)
        if fallback_model is None:
            continue
        candidates.append({"provider": provider, "model": fallback_model})
    return candidates
