"""Provider base-URL resolution: DB override → adapter default.

A self-hosted provider's endpoint is configured from the admin
``/providers`` page and stored as a ``system_settings`` row
``provider_base_url:<name>``. That is the only supported way to set one —
the same rule provider keys follow, for the same reason: the operators of
this product are IT staff, not people who should have to edit a file
inside a container.

Priority order at resolve time:
    1. ``system_settings`` row ``provider_base_url:<name>`` — read via
       a 30s-TTL per-provider cache (mirrors ``_keys_cache`` in
       ``provider_key_service``). Empty value = not configured, fall
       through. DB errors fail open to the next layer.
    2. The adapter's hardcoded default (passed in as ``default``).

Resolving at call time — not at import time — means an admin edit
propagates within the cache TTL without a restart.

REMOVED: a ``PROVIDER_BASE_URL_OVERRIDES`` JSON env map used to sit
between the two layers. It was built for the perf baseline
(PERF_BASELINE_PLAN.md §7) to point the cloud adapters at an in-cluster
mock. ``tools/loadtest/docker-compose.loadtest.yml`` still sets it and is
now a no-op — that harness must seed ``system_settings`` rows instead
(one INSERT per provider), which the DB layer above already honours.

NOTE ON SCOPE: this module ONLY changes base-URL lookup. Auth headers,
retries, streaming semantics, timeouts, and response parsing are all
unchanged — see PERF_BASELINE_PLAN.md §8.4 for why we are deliberately
NOT touching the httpx.AsyncClient factory at this time.
"""

from __future__ import annotations

import ipaddress
import time
from urllib.parse import urlsplit

from app.core.exceptions import ValidationError

# Providers whose endpoint is operator-supplied (self-hosted boxes) and
# therefore configurable from the admin UI. The key-validation flow uses
# the generic OpenAI-compatible probe for these.
SELF_HOSTED_PROVIDERS = frozenset({"sranai"})

# system_settings key prefix; row per provider, e.g. provider_base_url:sranai
PROVIDER_BASE_URL_SETTING_PREFIX = "provider_base_url:"

_DB_CACHE_TTL_S = 30.0
# provider_name -> (monotonic expiry, value-or-None). None caches both
# "no row / empty" and "DB unreachable" so a broken DB can't add a query
# per request.
_db_cache: dict[str, tuple[float, str | None]] = {}


def clear_base_url_cache() -> None:
    """Drop the cached DB overrides (call after an admin endpoint edit)."""
    _db_cache.clear()


def _read_db_override(provider_name: str) -> str | None:
    """Cached read of the ``system_settings`` base-URL row.

    Fail-open: any DB problem resolves to None so dispatch falls back to
    the env/default layers instead of erroring the request.
    """
    mono = time.monotonic()
    cached = _db_cache.get(provider_name)
    if cached is not None and cached[0] > mono:
        return cached[1]

    value: str | None = None
    try:
        from app.core.db import SessionLocal
        from app.models.system_setting import SystemSettingORM

        session = SessionLocal()
        try:
            row = session.get(
                SystemSettingORM, f"{PROVIDER_BASE_URL_SETTING_PREFIX}{provider_name}"
            )
            if row is not None and (row.value or "").strip():
                value = row.value.strip().rstrip("/")
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — fail open to env/default
        value = None
    _db_cache[provider_name] = (mono + _DB_CACHE_TTL_S, value)
    return value


def resolve_base_url(provider_name: str, default: str) -> str:
    """Return the effective base URL for ``provider_name``.

    ``default`` is the adapter's hardcoded production URL, used when no
    override is set. The returned value has any trailing slash stripped
    so ``/chat/completions`` appends cleanly regardless of source.
    """
    db_value = _read_db_override(provider_name)
    if db_value:
        return db_value
    return default.rstrip("/")


# ---------------------------------------------------------------------------
# Admin-facing helpers: validation + storage of the DB override.
# ---------------------------------------------------------------------------

def validate_provider_base_url(url: str) -> str:
    """Lenient SSRF gate for an operator-entered provider endpoint.

    Self-hosted boxes legitimately live on private/internal addresses
    (10/8, 172.16/12, 192.168/16, host.docker.internal, localhost), so
    unlike ``ai_review_provider.classify_review_host`` this gate ALLOWS
    private ranges. It only rejects what can never be a provider box:
    non-HTTP schemes, hostless URLs, and the link-local range that
    carries cloud metadata (169.254.0.0/16 / fe80::/10, incl.
    169.254.169.254). Hostnames are not DNS-resolved — the value is
    admin-supplied, this is a guard against obvious foot-guns, not a
    hostile-input boundary.

    Returns the normalized URL (whitespace + trailing slash stripped).
    Raises ValidationError otherwise.
    """
    u = (url or "").strip()
    if not u:
        raise ValidationError("base_url must not be empty", code="invalid_base_url")
    parts = urlsplit(u)
    if parts.scheme not in ("http", "https"):
        raise ValidationError(
            "base_url scheme must be http or https",
            code="invalid_base_url",
        )
    host = parts.hostname
    if not host:
        raise ValidationError(
            "base_url must include a host", code="invalid_base_url"
        )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # hostname — allowed (private DNS is the point)
    if ip is not None and ip.is_link_local:
        raise ValidationError(
            "link-local addresses (169.254.0.0/16) are not allowed",
            code="invalid_base_url",
        )
    if host.lower() == "metadata.google.internal":
        raise ValidationError(
            "cloud metadata endpoints are not allowed", code="invalid_base_url"
        )
    return u.rstrip("/")


def get_stored_base_url(session, provider_name: str) -> str | None:
    """Read the raw stored override (uncached; for admin/status views)."""
    from app.models.system_setting import SystemSettingORM

    row = session.get(
        SystemSettingORM, f"{PROVIDER_BASE_URL_SETTING_PREFIX}{provider_name}"
    )
    if row is None:
        return None
    return (row.value or "").strip() or None


def set_stored_base_url(session, provider_name: str, url: str) -> str | None:
    """Upsert the ``system_settings`` override row; returns the previous value.

    Caller is responsible for validating ``url`` first
    (``validate_provider_base_url``) and for evicting the old URL's
    pooled httpx client (``http_client.evict_client``) — eviction is
    async and this helper is sync by design.

    Also records that this provider's endpoint is database-owned, so the
    first-boot env seed cannot act on it again (see
    ``app.services.provider_env_seed``). This module still does not import
    ``settings``: the mark is a DB write, not an env read, so there is no
    env tier at resolve time.
    """
    from app.models.system_setting import SystemSettingORM
    from app.services.provider_env_seed import KIND_BASE_URL, mark_provider_owned

    key = f"{PROVIDER_BASE_URL_SETTING_PREFIX}{provider_name}"
    row = session.get(SystemSettingORM, key)
    previous = (row.value or "").strip() if row is not None else None
    if row is None:
        row = SystemSettingORM(
            key=key,
            value=url,
            description=f"{provider_name} endpoint (OpenAI-compatible base URL)",
        )
        session.add(row)
    else:
        row.value = url
    # Insert-if-absent, so the seeder's own "env-seed" reason survives when
    # this is called from the seed path.
    mark_provider_owned(session, KIND_BASE_URL, provider_name, "admin-ui")
    session.flush()
    # PRIME the resolver cache with the new value rather than just
    # clearing it: the row above is only FLUSHED in the caller's
    # transaction, and ``_read_db_override`` opens its OWN session — an
    # uncommitted write is invisible there. Model discovery runs inside
    # this very request (add-key route) and resolves the endpoint via
    # the cache, so it must see the value NOW. If the caller's
    # transaction later rolls back, the stale prime self-heals at TTL
    # expiry (30s).
    _db_cache[provider_name] = (
        time.monotonic() + _DB_CACHE_TTL_S,
        url.strip().rstrip("/") or None,
    )
    return previous or None
