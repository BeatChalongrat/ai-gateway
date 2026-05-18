"""Currency conversion for cost display.

Routing + billing always store costs in USD (``estimated_cost_usd`` on
``UsageInfo`` / ``request_logs.estimated_cost_usd``). This service
provides a thin display layer that adds a Baht surface alongside the
USD value, sourced from a live FX rate when reachable and falling
back to ``Settings.usd_to_thb_rate`` when not.

Contract:
- Never raises. Network failures, JSON-parse errors, bad responses —
  all silently return the configured fallback rate.
- ``get_usd_to_thb`` is async (1h-cached HTTP fetch).
- ``convert`` and ``format_cost`` are sync — they read the cached
  rate or the settings fallback, suitable for use inside response
  builders and Pydantic serialisers that can't await.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import structlog

from app.core.config import Settings


LIVE_RATE_URL = "https://api.exchangerate-api.com/v4/latest/USD"
_CACHE_TTL = timedelta(hours=1)
_FETCH_TIMEOUT_SECONDS = 5.0

logger = structlog.get_logger(__name__)


class CurrencyService:
    """USD → THB conversion + display formatter.

    Concurrency: ``_cached_rate`` / ``_cache_expires_at`` are shared
    across concurrent ``get_usd_to_thb`` callers; on a cache miss
    multiple requests may issue the fetch in parallel. We accept the
    duplicated work over an ``asyncio.Lock`` because the cost
    accounting path runs at most a handful of FX fetches per process
    lifetime.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached_rate: float | None = None
        self._cache_expires_at: datetime | None = None

    # ------------------------------------------------------------------
    # Rate sourcing
    # ------------------------------------------------------------------

    async def get_usd_to_thb(self) -> float:
        """Fetch (or return cached) USD→THB rate.

        Fallbacks, in order: cached value → live HTTP fetch → settings
        fallback. Any error in the live fetch silently uses the
        settings value and logs once at WARNING.
        """
        now = datetime.now(timezone.utc)
        if (
            self._cached_rate is not None
            and self._cache_expires_at is not None
            and now < self._cache_expires_at
        ):
            return self._cached_rate

        try:
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client:
                response = await client.get(LIVE_RATE_URL)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning(
                "currency_live_rate_failed",
                error=str(exc)[:200],
                fallback=self._settings.usd_to_thb_rate,
            )
            return self._settings.usd_to_thb_rate

        rate: Optional[float] = None
        try:
            rate = float(payload["rates"]["THB"])
        except (TypeError, KeyError, ValueError):
            rate = None
        if not rate or rate <= 0:
            logger.warning(
                "currency_live_rate_bad_payload",
                fallback=self._settings.usd_to_thb_rate,
            )
            return self._settings.usd_to_thb_rate

        self._cached_rate = rate
        self._cache_expires_at = now + _CACHE_TTL
        return rate

    def _current_rate(self) -> float:
        """Synchronous rate accessor for ``convert`` / ``format_cost``.

        Returns the cached live rate when present and unexpired,
        otherwise the settings fallback. Does NOT trigger an HTTP
        fetch — that's reserved for ``get_usd_to_thb``.
        """
        now = datetime.now(timezone.utc)
        if (
            self._cached_rate is not None
            and self._cache_expires_at is not None
            and now < self._cache_expires_at
        ):
            return self._cached_rate
        return self._settings.usd_to_thb_rate

    # ------------------------------------------------------------------
    # Public formatters
    # ------------------------------------------------------------------

    def convert(self, usd_amount: float) -> float:
        """Convert USD to THB using the cached (or fallback) rate."""
        return round(usd_amount * self._current_rate(), 6)

    def format_cost(
        self,
        usd_amount: float,
        currency: str | None = None,
    ) -> dict:
        """Build a display dict for an arbitrary USD figure.

        Returns:
            {
              "usd": float,
              "thb": float | None,     # None when currency == "USD"
              "display": str,           # human-formatted string
              "currency": str,          # "USD" or "THB"
            }

        ``currency`` overrides ``Settings.display_currency`` for a
        single call — useful for admin endpoints that want to force
        USD reporting regardless of the org's default.
        """
        chosen = (currency or self._settings.display_currency or "USD").upper()
        if chosen == "USD":
            return {
                "usd": round(usd_amount, 8),
                "thb": None,
                "display": f"${usd_amount:.6f}",
                "currency": "USD",
            }
        thb_amount = self.convert(usd_amount)
        return {
            "usd": round(usd_amount, 8),
            "thb": thb_amount,
            # Use the explicit Unicode escape (฿) rather than the
            # literal ``฿`` glyph. Both compile to the same one-char
            # string at runtime, but the escape survives any future
            # editor / git filter that re-saves this file under a
            # non-UTF-8 codepage — a real concern on Windows hosts
            # where cp1252 has bitten earlier sessions.
            "display": f"฿{thb_amount:.4f}",
            "currency": "THB",
        }


# Module-level singleton. Eager-bound to ``settings`` at import time so
# sync call sites (response builders, Pydantic serialisers) can reach
# the formatter without an explicit dependency. The async live-rate
# fetch must be triggered by a background job or a startup hook — sync
# callers always read the cached value or the settings fallback.
from app.core.config import settings as _settings  # noqa: E402

currency_service = CurrencyService(_settings)

