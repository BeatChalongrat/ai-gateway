"""Community-sourced per-token pricing fetcher.

Pulls the litellm ``model_prices_and_context_window.json`` blob, caches
it in-process for 1 hour, and exposes a lookup helper keyed on the
provider-qualified model name.

Failure semantics are intentionally lenient: *any* exception during
fetch (DNS, TLS, JSON parse, non-200 status, schema drift) results in
an empty cache and a warning log. Callers must therefore treat a
``None`` from ``get_model_pricing`` as the normal "no enrichment
available" case and fall back to existing DB values — never raise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog


LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main"
    "/model_prices_and_context_window.json"
)

_CACHE_TTL = timedelta(hours=1)
_FETCH_TIMEOUT_SECONDS = 10.0

logger = structlog.get_logger(__name__)


# Hard-coded fallback for models the litellm community JSON does not
# track. Used as the third lookup in ``get_model_pricing`` after the
# exact-match and provider-qualified litellm lookups both miss. Values
# are early-2025 public list prices in $/token; refresh via admin UI or
# a litellm upstream update. Covers every seed candidate that shipped
# at 0.0/0.0 — the "fill the gaps" sweep of May 2026.
MANUAL_PRICING: dict[str, dict[str, float]] = {
    # --- OpenRouter (aggregator — proxied, midpoint estimates) -----
    "openrouter/auto":                    {"input": 0.000001,     "output": 0.000003},
    "anthropic/claude-3.5-sonnet":        {"input": 0.000003,     "output": 0.000015},
    "openai/gpt-4o":                      {"input": 0.0000025,    "output": 0.000010},
    "meta-llama/llama-3.1-405b":          {"input": 0.000003,     "output": 0.000003},
    "google/gemini-pro-1.5":               {"input": 0.0000035,    "output": 0.0000105},
    # Extra alias mappings that match common litellm namespaces for
    # OpenRouter-style ids (kept for cross-source compatibility):
    "google/gemini-flash-1.5":            {"input": 0.0000000375, "output": 0.00000015},
    "meta-llama/llama-3.1-8b-instruct":   {"input": 0.00000005,   "output": 0.00000008},
    "meta-llama/llama-3.1-70b-instruct":  {"input": 0.00000059,   "output": 0.00000079},
    "mistralai/mixtral-8x7b-instruct":    {"input": 0.00000024,   "output": 0.00000024},
    # --- Groq (LPU, public pricing page) ---------------------------
    "llama-3.3-70b-versatile":            {"input": 0.00000059,   "output": 0.00000079},
    "mixtral-8x7b-32768":                 {"input": 0.00000024,   "output": 0.00000024},
    "gemma2-9b-it":                       {"input": 0.0000002,    "output": 0.0000002},
    "deepseek-r1-distill-llama-70b":      {"input": 0.00000075,   "output": 0.000003},
    "qwen-2.5-72b":                       {"input": 0.0000009,    "output": 0.0000009},
    "qwen-2.5-32b":                       {"input": 0.00000079,   "output": 0.00000079},
    # --- xAI ------------------------------------------------------
    "grok-2-latest":                      {"input": 0.000002,     "output": 0.000010},
    "grok-2-mini":                        {"input": 0.0000002,    "output": 0.0000004},
    # --- Minimax --------------------------------------------------
    "abab5.5-chat":                       {"input": 0.00000015,   "output": 0.00000015},
    # --- Google (missing variants) --------------------------------
    "gemini-2.0-flash-exp":               {"input": 0.0000000375, "output": 0.00000015},
    "gemini-1.5-flash-8b":                {"input": 0.0000000375, "output": 0.00000015},
    # --- Z.AI / 01.AI Yi ------------------------------------------
    "yi-large":                           {"input": 0.000003,     "output": 0.000003},
    "yi-medium":                          {"input": 0.0000004,    "output": 0.0000004},
    # --- DeepSeek (alt SKUs) --------------------------------------
    "deepseek-coder":                     {"input": 0.00000027,   "output": 0.0000011},
    # --- Mistral (missing) ----------------------------------------
    "codestral-latest":                   {"input": 0.000001,     "output": 0.000003},
    "mistral-embed":                      {"input": 0.0000001,    "output": 0.0},
    # --- Cohere (missing) -----------------------------------------
    "command-r-plus-08-2024":             {"input": 0.0000025,    "output": 0.00001},
    "command-r-08-2024":                  {"input": 0.00000015,   "output": 0.0000006},
    "command":                            {"input": 0.0000005,    "output": 0.0000015},
    "command-light":                      {"input": 0.0000003,    "output": 0.0000006},
    # --- Perplexity (missing) -------------------------------------
    "llama-3.1-sonar-small-128k-online":  {"input": 0.0000002,    "output": 0.0000002},
    "llama-3.1-sonar-huge-128k-online":   {"input": 0.000005,     "output": 0.000005},
}


class PricingFetcher:
    """Async fetcher for the litellm community pricing JSON.

    Holds a single in-process cache shared by all callers. Concurrent
    ``fetch()`` calls during a cache miss may all issue the request —
    we accept the duplicated work over the complexity of an
    ``asyncio.Lock``: in steady state the cache hits and at startup
    only ``ModelDiscoveryService`` calls into it.
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_expires_at: datetime | None = None

    async def fetch(self) -> dict[str, dict[str, Any]]:
        """Return the full pricing dict, fetching if cache is cold.

        Returns ``{}`` on any error (network, parse, schema) and logs
        a warning. Callers should treat this as "no data available"
        and skip enrichment — DB rows already hold the last-known
        values.
        """
        now = datetime.now(timezone.utc)
        if (
            self._cache
            and self._cache_expires_at is not None
            and now < self._cache_expires_at
        ):
            return self._cache

        try:
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client:
                response = await client.get(LITELLM_PRICING_URL)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            # Network/timeout/parse/HTTP error — keep the (possibly
            # stale) cache and surface an empty dict to the caller so
            # discovery proceeds with provider-API data alone.
            logger.warning(
                "pricing_fetcher_failed",
                error=str(exc)[:200],
                url=LITELLM_PRICING_URL,
            )
            return {}

        if not isinstance(data, dict):
            logger.warning(
                "pricing_fetcher_unexpected_shape",
                got_type=type(data).__name__,
            )
            return {}

        # Schema sanity: only keep entries whose value is a dict — the
        # litellm file historically has a top-level ``sample_spec`` key
        # whose value is a non-dict marker we don't care about.
        cleaned = {k: v for k, v in data.items() if isinstance(v, dict)}
        self._cache = cleaned
        self._cache_expires_at = now + _CACHE_TTL
        logger.info(
            "pricing_fetcher_refreshed",
            entries=len(cleaned),
            expires_at=self._cache_expires_at.isoformat(),
        )
        return cleaned

    def get_model_pricing(
        self,
        model: str,
        provider: str,
    ) -> dict[str, Any] | None:
        """Lookup pricing for ``model``.

        Resolution order:
          1. ``model`` exactly                        — litellm cache
          2. ``provider/model``                       — litellm cache
          3. ``provider.model``                       — litellm cache
          4. ``model`` / ``provider/model``           — ``MANUAL_PRICING``

        The MANUAL_PRICING fallback covers models that litellm does
        not track (Yi, Grok-2, OpenRouter aggregator entries, etc.).
        Its values are normalised to the same shape as a litellm entry
        — ``{"input_cost_per_token", "output_cost_per_token"}`` — so
        downstream enrichment code is provider-table-agnostic.

        Returns ``None`` only when no source has data; caller treats
        that as "leave existing DB row alone".
        """
        # --- 1-3: litellm cache (live + cached HTTP fetch) -------
        if self._cache:
            for key in (model, f"{provider}/{model}", f"{provider}.{model}"):
                entry = self._cache.get(key)
                if entry is not None:
                    return entry
        # --- 4: MANUAL_PRICING ----------------------------------
        for key in (model, f"{provider}/{model}"):
            manual = MANUAL_PRICING.get(key)
            if manual is not None:
                return {
                    "input_cost_per_token": manual["input"],
                    "output_cost_per_token": manual["output"],
                }
        return None
