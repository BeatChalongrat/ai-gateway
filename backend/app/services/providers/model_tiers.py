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

from typing import Iterable

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

    Lookup is exact (no aliasing). Callers that pass ``"auto"`` or a
    provider-prefixed name will get ``None`` — that's intentional;
    fallback only applies to concrete, registered models.
    """
    for tier_name, tier in TIERS.items():
        if model in tier["models"]:
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
    tier_name = get_tier(model)
    if tier_name is None:
        return []

    tier = TIERS[tier_name]
    model_map: dict[str, str] = tier["model_map"].get(model, {})
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
