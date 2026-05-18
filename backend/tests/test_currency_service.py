"""Tests for ``app.services.currency_service.CurrencyService``.

Covers:
- 1h live-rate cache (HTTP issued once across two reads)
- Network failure → falls back to ``Settings.usd_to_thb_rate`` silently
- ``format_cost`` populates ฿ display when currency == "THB"
- ``format_cost(currency="USD")`` zeroes the THB fields + uses $ display
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import Settings
from app.services.currency_service import (
    LIVE_RATE_URL,
    CurrencyService,
)


def _settings(rate: float = 35.0, display: str = "THB") -> Settings:
    """Build a fresh Settings with deterministic currency knobs.

    ``Settings`` reads env at instantiation; we pass values explicitly
    via kwargs so the test runs are isolated from the host env.
    """
    return Settings(usd_to_thb_rate=rate, display_currency=display)


@pytest.mark.asyncio
@respx.mock
async def test_live_rate_cached_for_one_hour():
    route = respx.get(LIVE_RATE_URL).mock(
        return_value=httpx.Response(200, json={"rates": {"THB": 36.5}})
    )
    svc = CurrencyService(_settings())
    first = await svc.get_usd_to_thb()
    second = await svc.get_usd_to_thb()
    assert first == pytest.approx(36.5)
    assert second == pytest.approx(36.5)
    assert route.call_count == 1, (
        "Second read inside the 1h TTL must hit the cache, not the API"
    )


@pytest.mark.asyncio
@respx.mock
async def test_live_rate_falls_back_on_network_error():
    respx.get(LIVE_RATE_URL).mock(
        side_effect=httpx.ConnectError("simulated DNS error")
    )
    svc = CurrencyService(_settings(rate=42.5))
    result = await svc.get_usd_to_thb()
    assert result == pytest.approx(42.5), (
        "Network failure must silently use the Settings fallback"
    )


def test_format_cost_thb():
    svc = CurrencyService(_settings(rate=35.0, display="THB"))
    info = svc.format_cost(0.001)
    assert info["currency"] == "THB"
    assert info["thb"] == pytest.approx(0.035)
    assert info["display"] == "฿0.0350"
    assert info["usd"] == pytest.approx(0.001)


def test_format_cost_usd_only():
    svc = CurrencyService(_settings(display="USD"))
    info = svc.format_cost(0.001234)
    assert info["currency"] == "USD"
    assert info["thb"] is None
    assert info["display"].startswith("$")
    assert info["usd"] == pytest.approx(0.001234)
