"""FIX35 Part C step 3 — cost bands.

Maps a model's catalog price (USD per 1M INPUT tokens, the number the
picker already shows) onto five bands and gives the router a ceiling
filter. The bands are deliberately coarser than prices: a user picking a
ceiling is expressing "how much am I willing to spend on this chat", not
choosing a number.

    low     ≤ $0.50 / 1M     (gpt-4o-mini, gemini-flash, haiku-3.5, llama-70b)
    medium  ≤ $3.00 / 1M     (gpt-4o, sonnet-4.5, gemini-pro, deepseek-r1)
    high    ≤ $10.00 / 1M    (o-series pro, large context flagships)
    xhigh   ≤ $30.00 / 1M    (opus-class)
    max     anything above

Unknown / zero price maps to ``medium`` — an unknown price must never win
a "cheapest" comparison (same rule as providers.cost_tiers), and must never
be excluded by a ``low`` ceiling either… it sits in the middle. The legacy
three-way ``cost_tier`` ("budget"/"standard"/"premium") is mapped when no
price is known so an old registry row still lands in a sane band.
"""

from __future__ import annotations

from typing import Iterable, Sequence, TypeVar

TIERS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
_RANK = {t: i for i, t in enumerate(TIERS)}

# Upper bound (USD / 1M input tokens) of each band, in TIERS order; the
# last band is unbounded.
BAND_MAX_USD_PER_1M: tuple[float, ...] = (0.50, 3.00, 10.00, 30.00)

_LEGACY = {"cheap": "low", "budget": "low", "standard": "medium", "premium": "high"}

_T = TypeVar("_T")


def is_tier(value: str | None) -> bool:
    return bool(value) and value in _RANK


def tier_rank(tier: str | None) -> int:
    """0 = low … 4 = max. Unknown → medium (the neutral middle)."""
    return _RANK.get(tier or "", _RANK["medium"])


def band_for_price(usd_per_1m_input: float | None, legacy_cost_tier: str | None = None) -> str:
    """Price → band. ``None``/0 price falls back to the legacy tier, then medium."""
    if usd_per_1m_input is not None and usd_per_1m_input > 0:
        for tier, bound in zip(TIERS, BAND_MAX_USD_PER_1M):
            if usd_per_1m_input <= bound:
                return tier
        return TIERS[-1]
    return _LEGACY.get((legacy_cost_tier or "").lower(), "medium")


def band_for_candidate(candidate) -> str:
    """Band for a registry ``ModelCandidate`` (cost_per_input_token is $/token)."""
    per_token = float(getattr(candidate, "cost_per_input_token", 0.0) or 0.0)
    return band_for_price(per_token * 1_000_000 if per_token > 0 else None, getattr(candidate, "cost_tier", None))


def effective_ceiling(*tiers: str | None, default: str = "high") -> str:
    """Combine ceilings, most specific first (request → conversation → tenant
    → deployment). The first VALID one wins; a request may go *below* the
    tenant ceiling but never above it, so the result is the min of the
    first valid tier and every later valid tier."""
    chosen: str | None = None
    for t in tiers:
        if not is_tier(t):
            continue
        if chosen is None:
            chosen = t
        elif tier_rank(t) < tier_rank(chosen):
            chosen = t
    return chosen or default


def filter_by_ceiling(candidates: Sequence[_T], ceiling: str) -> list[_T]:
    """Keep candidates whose band is at or under ``ceiling``."""
    limit = tier_rank(ceiling)
    return [c for c in candidates if tier_rank(band_for_candidate(c)) <= limit]


def cheapest_first(candidates: Iterable[_T]) -> list[_T]:
    return sorted(candidates, key=lambda c: (tier_rank(band_for_candidate(c)), getattr(c, "model", "")))
