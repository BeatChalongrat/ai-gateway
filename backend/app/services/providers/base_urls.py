"""Provider base-URL resolution with env-driven override.

Perf-baseline work (see PERF_BASELINE_PLAN.md §7) needs every adapter
to point its outbound HTTP at a local mock provider. Hard-coding that
URL into the adapters would either (a) require a code change before
each load run or (b) leak into production. Instead we add a single
settings-backed override map that each adapter consults at call time.

Priority order at resolve time:
    1. ``settings.provider_base_url_overrides`` (JSON dict) — if the
       provider_name key is present and non-empty, its value wins.
    2. The adapter's hardcoded default (passed in as ``default``).

Resolving at call time — not at import time — means the perf runner
can flip overrides on / off via env var between scenarios without
restarting the process (in principle; the directive still restarts the
backend per variant for hygiene).

NOTE ON SCOPE: this module ONLY changes base-URL lookup. Auth headers,
retries, streaming semantics, timeouts, and response parsing are all
unchanged — see PERF_BASELINE_PLAN.md §8.4 for why we are deliberately
NOT touching the httpx.AsyncClient factory at this time.
"""

from __future__ import annotations

from app.core.config import settings


def resolve_base_url(provider_name: str, default: str) -> str:
    """Return the effective base URL for ``provider_name``.

    ``default`` is the adapter's hardcoded production URL, used when no
    override is set. The returned value has any trailing slash stripped
    so ``/chat/completions`` appends cleanly regardless of source.
    """
    overrides = settings.provider_base_url_overrides_map
    if overrides and provider_name in overrides:
        return overrides[provider_name]
    return default.rstrip("/")
