"""Model-tier registry powering Phase-2 cross-provider fallback.

A *tier* groups models with comparable cost / latency / capability so a
failure on the primary candidate can be answered by another vendor's
model of the same class instead of dropping the request.

FIX120 — the two tables this module used to hold as Python literals
(``CURATED_FALLBACKS`` and ``TIERS``) now live in
``docs/contracts/fallback_policy.json`` → ``chains.curated`` /
``chains.tiers`` and are read through :mod:`app.services.gateway.fallback_policy`.
The module-level names are kept (read-only views of the shipped file) so
every existing import and test still works; the lookup rules below are
unchanged. ``get_fallback_chain`` reads the SHIPPED tables;
``chain_from_tables`` is the same walk over any tables (an effective
policy with overrides hands its own in).

The chain is deliberately small — at most four candidates per tier so
a single inbound failure cannot fan out into a wide rescue sweep.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.services.gateway.fallback_policy import base_policy

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
# Every chain carries at least one hop on a different account (DeepSeek
# direct or OpenAI direct); ``test_fallback_orchestrator.py`` fails any
# chain that collapses back to a single provider.
#
# FIX120 — the table itself is data: docs/contracts/fallback_policy.json.
# ---------------------------------------------------------------------------
CURATED_FALLBACKS: dict[str, list[dict[str, str]]] = base_policy()["chains"]["curated"]

# ---------------------------------------------------------------------------
# Tier table — premium / standard / fast (data: fallback_policy.json).
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
TIERS: dict[str, dict] = base_policy()["chains"]["tiers"]


def _tier_of(model: str, tiers: dict) -> str | None:
    canon = canonical_model(model)
    for tier_name, tier in tiers.items():
        if model in tier["models"] or canon in tier["models"]:
            return tier_name
    return None


def get_tier(model: str) -> str | None:
    """Return the tier name for ``model``, or ``None`` if unknown.

    Matches the exact id first, then a date-stripped form (so a dated
    provider id like ``claude-sonnet-4-20250514`` resolves to the
    ``claude-sonnet-4`` tier). Callers that pass ``"auto"`` or a
    provider-prefixed name still get ``None`` — fallback only applies to
    concrete, registered models.
    """
    return _tier_of(model, TIERS)


def chain_from_tables(
    model: str,
    original_provider: str,
    *,
    curated: dict[str, list[dict[str, str]]],
    tiers: dict[str, dict],
) -> list[dict[str, str]]:
    """The chain walk over the given tables (the rules of ``get_fallback_chain``).

    Curated chains apply ONLY to models the tier table does not own — the
    OpenRouter ``vendor/model`` chat ids and newer models. Legacy bare
    names (``gpt-4o``, ``claude-sonnet-4``) keep their generic tier
    behaviour. A self-hop (same provider + same alias) is dropped so the
    curated chain stays a pure alternative list.
    """
    if _tier_of(model, tiers) is None:
        alias = _alias(model)
        curated_hops = curated.get(alias)
        if curated_hops:
            out = [
                dict(c)
                for c in curated_hops
                if not (
                    c["provider"] == original_provider
                    and _alias(c["model"]) == alias
                )
            ]
            if out:
                return out

    tier_name = _tier_of(model, tiers)
    if tier_name is None:
        return []

    tier = tiers[tier_name]
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


def get_fallback_chain(
    model: str, original_provider: str
) -> list[dict[str, str]]:
    """Return ordered fallback candidates for ``(model, original_provider)``
    from the SHIPPED tables.

    Each entry is ``{"provider": str, "model": str}``. The original
    provider is filtered out so the chain is purely *cross-provider*.
    Providers without an entry in the tier's ``model_map[model]`` are
    skipped — the registry only lists vendor pairings that have been
    vetted as equivalent-class, so a missing entry means "don't try
    that vendor for this model".

    Returns ``[]`` when:
      * ``model`` is not in any tier and has no curated chain, or
      * ``model`` has no ``model_map`` entries (the tier knows about it
        but no fallbacks have been wired yet).
    """
    return chain_from_tables(model, original_provider, curated=CURATED_FALLBACKS, tiers=TIERS)
