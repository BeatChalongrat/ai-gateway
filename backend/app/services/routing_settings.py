"""Admin-configurable defaults for ``model="auto"`` routing.

``smart_router`` used to pick the *cheapest capable* model for every
auto turn, which in a catalog that carries ``gpt-3.5-turbo`` meant a
budget model was handed the full tool set — and a budget model calls
tools it was told not to call (the memory over-trigger). The fix is a
deployment-level answer rather than a prompt one: an admin names the
model Auto should prefer, plus an ordered fallback chain for when that
model's provider has no usable key.

Two ``system_settings`` rows back it (seeded by migration 073):

    auto_default_model      e.g. "gpt-4o"
    auto_default_fallbacks  comma-separated, e.g. "gpt-4.1,gpt-4o-mini"

Reads are cached for ``_TTL_SECONDS`` so routing doesn't pay a DB hit
per request; the admin PATCH route calls :func:`clear_cache` so an edit
takes effect on the very next turn instead of waiting out the TTL (no
deploy needed). Every failure path degrades to
``_HARDCODED_PREFERENCE`` — a missing table, a deleted row, or a blank
value must never make routing worse than the shipped default.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session


log = structlog.get_logger(__name__)

KEY_DEFAULT_MODEL = "auto_default_model"
KEY_DEFAULT_FALLBACKS = "auto_default_fallbacks"

# Last-resort preference used when the settings rows are missing or
# unreadable. Deliberately the same values migration 073 seeds, so an
# un-migrated deployment behaves like a migrated one.
_HARDCODED_PREFERENCE: tuple[str, ...] = ("gpt-4o", "gpt-4.1", "gpt-4o-mini")

_TTL_SECONDS = 30.0

_lock = threading.Lock()
_cached: tuple[str, ...] | None = None
_cached_at = 0.0


def clear_cache() -> None:
    """Drop the memoized preference so the next read hits the DB."""
    global _cached, _cached_at
    with _lock:
        _cached = None
        _cached_at = 0.0


def parse_model_list(value: str | None) -> list[str]:
    """Split a comma-separated setting value into model ids.

    Blanks and duplicates are dropped, order is preserved — the order IS
    the fallback order.
    """
    if not value:
        return []
    out: list[str] = []
    for part in value.split(","):
        model = part.strip()
        if model and model not in out:
            out.append(model)
    return out


def _read(key: str) -> str | None:
    # FIX7 §3: read via a fresh SYNC session, not the request's session. The chat
    # hot path passes an AsyncSession here (annotated Session, but AsyncSession in
    # practice — orchestrator Track A), whose .get() returns an un-awaited
    # coroutine, so ``row.value`` raised ``'coroutine' object has no attribute
    # 'value'`` — swallowed as ``auto_routing_settings_read_failed`` and silently
    # falling back to the hardcoded preference. These are global, committed admin
    # settings, so a short sync PK read is correct from either path.
    from app.core.db import SessionLocal
    from app.models.system_setting import SystemSettingORM

    with SessionLocal() as s:
        row = s.get(SystemSettingORM, key)
        return row.value if row is not None else None


def get_auto_preference(session: "Session | None") -> tuple[str, ...]:
    """Ordered model ids Auto should prefer, best-first.

    The first entry is the admin's default model; the rest are the
    fallback chain tried in order when the default isn't routable (no
    provider key, deactivated row, capability filtered out). Never
    raises and never returns empty.
    """
    global _cached, _cached_at

    now = time.monotonic()
    with _lock:
        if _cached is not None and now - _cached_at < _TTL_SECONDS:
            return _cached

    if session is None:
        return _HARDCODED_PREFERENCE

    try:
        default = (_read(KEY_DEFAULT_MODEL) or "").strip()
        fallbacks = parse_model_list(_read(KEY_DEFAULT_FALLBACKS))
    except Exception as exc:  # never let a settings read break routing
        log.warning("auto_routing_settings_read_failed", error=str(exc))
        return _HARDCODED_PREFERENCE

    preference = [default] if default else []
    for model in fallbacks:
        if model not in preference:
            preference.append(model)
    if not preference:
        # Row deleted or blanked out — fall back to the shipped default
        # rather than reverting to "cheapest capable".
        preference = list(_HARDCODED_PREFERENCE)

    result = tuple(preference)
    with _lock:
        _cached = result
        _cached_at = time.monotonic()
    return result
