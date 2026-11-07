"""Tests for the USD credit-display layer (Sprint 2A).

Asserts conversion at the real 3,000,000 credits/USD scale and the
sub-cent / zero formatting rules.
"""

from decimal import Decimal

from app.services.portal.credit_display import (
    credits_to_usd,
    credits_to_usd_display,
    usd_to_credits,
)


def test_credits_to_usd_one_dollar():
    assert credits_to_usd(3_000_000) == Decimal("1.0000")


def test_display_one_dollar():
    assert credits_to_usd_display(3_000_000) == "$1.00"


def test_display_sub_cent():
    # 1000 credits ≈ $0.000333 → below a cent
    assert credits_to_usd_display(1000) == "<$0.01"


def test_display_zero():
    assert credits_to_usd_display(0) == "$0.00"


def test_usd_to_credits_round_trip():
    assert usd_to_credits(Decimal("2.00")) == 6_000_000


def test_round_trip_seven_cents():
    assert credits_to_usd(usd_to_credits(Decimal("0.07"))) == Decimal("0.0700")
