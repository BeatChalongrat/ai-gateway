"""USD display layer over the raw credit ledger.

The ledger stores credits at ``usage_credits_per_usd`` scale (3,000,000 =
$1), which is meaningless to end users. Every user-facing surface shows
USD; the raw credit integer is debug-only. This module is the single
conversion point — keep it consistent with
``credit_service.credits_for_cost`` (which converts USD cost → credits at
the same scale).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.core.config import settings


def _rate() -> Decimal:
    return Decimal(int(settings.usage_credits_per_usd))


def credits_to_usd(credits: int) -> Decimal:
    """Convert a credit ledger amount to USD, quantized to 4 decimals."""
    if credits <= 0:
        return Decimal("0.0000")
    usd = Decimal(int(credits)) / _rate()
    return usd.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def credits_to_usd_display(credits: int) -> str:
    """Human-facing string: ``'$X.XX'``, or ``'<$0.01'`` for sub-cent."""
    usd = credits_to_usd(credits)
    if Decimal("0") < usd < Decimal("0.01"):
        return "<$0.01"
    return f"${usd:.2f}"


def usd_to_credits(usd: Decimal) -> int:
    """Convert USD to a credit ledger amount (half-up to nearest credit)."""
    if usd <= 0:
        return 0
    return int(
        (usd * _rate()).to_integral_value(rounding=ROUND_HALF_UP)
    )
