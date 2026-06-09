"""Provider API key management — multi-key with round-robin and validation.

Keys stored in provider_keys table. Falls back to legacy providers.api_key_encrypted
then environment variables.

All direct ``ProviderKeyORM`` queries live in
``app.repositories.ProviderKeyRepository``. This service keeps the
non-DB concerns: encryption / decryption, round-robin counter, label
generation, async HTTP key validation, and the legacy ``providers``
table fallback (which still uses raw session access because it
touches a different ORM model).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decrypt_provider_key, encrypt_provider_key
from app.models.provider import ProviderORM
from app.models.provider_key import ProviderKeyORM
from app.repositories.provider_key_repository import ProviderKeyRepository

logger = logging.getLogger(__name__)


def _filter_placeholder_candidates(
    rows: list[ProviderKeyORM], provider_name: str
) -> list[str]:
    """Decode active rows, dropping any whose key is a dev placeholder.

    Group A / A2: a placeholder stored in ``provider_keys`` (e.g. one a
    pre-guard boot imported) would 401 silently on dispatch. We filter it
    out of the resolved candidate list — not just the env fallback — so the
    pool only ever yields keys that look real. Each drop is logged.
    """
    out: list[str] = []
    for r in rows:
        if r.last_test_status == "invalid":
            continue
        key = _decode(r.api_key_encrypted)
        if _looks_like_placeholder_key(key):
            logger.warning(
                "Filtering placeholder key %s for provider %s from pool",
                r.id,
                provider_name,
            )
            continue
        out.append(key)
    return out


def _encode(key: str) -> str:
    # AES-256-GCM at rest (was plain Base64). All provider-key reads/writes
    # in this module route through _encode/_decode, so this single swap
    # covers both the provider_keys and legacy providers tables.
    return encrypt_provider_key(key, settings.effective_master_key)


def _decode(encoded: str) -> str:
    return decrypt_provider_key(encoded, settings.effective_master_key)


def _mask(raw_key: str) -> str:
    # Suffix-only. Provider key PREFIXES are predictable (sk-ant-, sk-proj-,
    # gw_), so exposing them leaks structure for no benefit — show last 4
    # only, matching Stripe/AWS/etc.
    if len(raw_key) <= 4:
        return "••••"
    return "••••" + raw_key[-4:]


# Markers that flag an env var as a dev placeholder rather than a real
# API key. A non-empty placeholder (e.g. "sk-ant-...") would otherwise be
# handed to the provider and 401; the dispatch/fallback chains only kick
# in when a key is MISSING, so we treat placeholders as missing. Real keys
# are long (OpenAI ~164, Gemini ~39, Anthropic ~100+) with none of these
# markers, so this never rejects a live key.
_KEY_PLACEHOLDER_MARKERS = (
    "...",
    "changeme",
    "change-me",
    "placeholder",
    "your-",
    "your_",
    "example",
    "dummy",
    "xxxx",
)


def _looks_like_placeholder_key(key: str | None) -> bool:
    """True when ``key`` is empty or an obvious dev placeholder."""
    if not key:
        return True
    k = key.strip()
    if len(k) < 20:
        return True
    low = k.lower()
    return any(marker in low for marker in _KEY_PLACEHOLDER_MARKERS)


_ENV_KEYS = {
    "openai": lambda: settings.openai_api_key,
    "anthropic": lambda: settings.anthropic_api_key,
    "openrouter": lambda: settings.openrouter_api_key,
    "minimax": lambda: settings.minimax_api_key,
    "qwen": lambda: settings.qwen_api_key,
    "kimi": lambda: settings.kimi_api_key,
    "zai": lambda: settings.zai_api_key,
}

# In-memory round-robin counters (per worker process)
_rr_counters: dict[str, int] = defaultdict(int)

# Phase-1 B5: ``get_active_key`` ran a DB SELECT (provider_keys, + a
# legacy providers query on the env-key path) on every provider
# dispatch. Cache the *resolved* candidate key list per provider for a
# short TTL — values are plain decoded strings (already transient in
# process memory). Round-robin still rotates across the cached list via
# ``_rr_counters``. Per-worker; self-heals within the TTL.
# ``clear_keys_cache()`` is exposed for explicit invalidation after an
# admin key add / rotate / test-status change.
_KEYS_CACHE_TTL_S = 30.0
_keys_cache: dict[str, tuple[float, list[str]]] = {}


def clear_keys_cache() -> None:
    """Drop all cached provider keys (call after an admin key edit)."""
    _keys_cache.clear()


class ProviderKeyService:

    # ------------------------------------------------------------------
    # Key resolution (round-robin across active valid keys)
    # ------------------------------------------------------------------

    def get_active_key(self, session: Session, provider_name: str) -> str | None:
        """Get next API key for provider via round-robin.

        Priority: provider_keys table → legacy providers.api_key_encrypted → env var.
        Skips keys where last_test_status = 'invalid'.
        """
        mono = time.monotonic()
        cached = _keys_cache.get(provider_name)
        if cached is not None and cached[0] > mono:
            candidates = cached[1]
        else:
            candidates = self._resolve_key_candidates(session, provider_name)
            _keys_cache[provider_name] = (mono + _KEYS_CACHE_TTL_S, candidates)

        if not candidates:
            return None
        idx = _rr_counters[provider_name] % len(candidates)
        _rr_counters[provider_name] += 1
        return candidates[idx]

    def _resolve_key_candidates(
        self, session: Session, provider_name: str
    ) -> list[str]:
        """Resolve the ordered candidate key list (uncached).

        Priority: provider_keys table → legacy providers.api_key_encrypted
        → env var. Decoded to plain strings so the cache holds no ORM.
        """
        repo = ProviderKeyRepository(session)
        rows = repo.list_active_by_provider(provider_name)
        valid = _filter_placeholder_candidates(rows, provider_name)
        if valid:
            return valid

        # Fallback: legacy single key on providers table
        provider = session.execute(
            select(ProviderORM).where(ProviderORM.name == provider_name)
        ).scalar_one_or_none()
        if provider and provider.api_key_encrypted:
            return [_decode(provider.api_key_encrypted)]

        # Fallback: env var. Skip dev placeholders (e.g. "sk-ant-...") so
        # the dispatch/fallback chain moves to a keyed provider instead of
        # 401-ing — makes default->Claude (Fix 4) safe when
        # ANTHROPIC_API_KEY is still a placeholder. No-op for real keys.
        env_fn = _ENV_KEYS.get(provider_name)
        env_key = env_fn() if env_fn else None
        if _looks_like_placeholder_key(env_key):
            return []
        return [env_key]

    # Backward compat alias
    def get_api_key(self, session: Session, provider_name: str) -> str | None:
        return self.get_active_key(session, provider_name)

    # ------------------------------------------------------------------
    # Async variants (Track A) — additive; sync paths above unchanged.
    # ------------------------------------------------------------------

    async def get_active_key_async(
        self, session: AsyncSession, provider_name: str
    ) -> str | None:
        """Async variant of :meth:`get_active_key`.

        Identical cache + round-robin logic. The Phase-1 ``_keys_cache``
        (TTL'd resolved-candidate list) sits in front exactly as the sync
        path; only the cache-MISS DB read is awaited. Cache dict access and
        the ``_rr_counters`` round-robin remain plain sync operations
        (fine on the event loop — no I/O).
        """
        mono = time.monotonic()
        cached = _keys_cache.get(provider_name)
        if cached is not None and cached[0] > mono:
            candidates = cached[1]
        else:
            candidates = await self._resolve_key_candidates_async(
                session, provider_name
            )
            _keys_cache[provider_name] = (mono + _KEYS_CACHE_TTL_S, candidates)

        if not candidates:
            return None
        idx = _rr_counters[provider_name] % len(candidates)
        _rr_counters[provider_name] += 1
        return candidates[idx]

    async def _resolve_key_candidates_async(
        self, session: AsyncSession, provider_name: str
    ) -> list[str]:
        """Async mirror of :meth:`_resolve_key_candidates`.

        Resolves the ordered candidate key list (uncached) using awaited
        DB reads. The repository is sync-only, so the ``provider_keys``
        SELECT is issued directly here (same query as
        ``ProviderKeyRepository.list_active_by_provider``).
        Priority: provider_keys table → legacy providers.api_key_encrypted
        → env var. Decoded to plain strings so the cache holds no ORM.
        """
        result = await session.execute(
            select(ProviderKeyORM)
            .where(
                ProviderKeyORM.provider_name == provider_name,
                ProviderKeyORM.is_active == True,  # noqa: E712
            )
            .order_by(ProviderKeyORM.created_at)
        )
        rows = list(result.scalars().all())
        valid = _filter_placeholder_candidates(rows, provider_name)
        if valid:
            return valid

        # Fallback: legacy single key on providers table
        provider = (
            await session.execute(
                select(ProviderORM).where(ProviderORM.name == provider_name)
            )
        ).scalar_one_or_none()
        if provider and provider.api_key_encrypted:
            return [_decode(provider.api_key_encrypted)]

        # Fallback: env var. Skip dev placeholders (e.g. "sk-ant-...") so
        # the dispatch/fallback chain moves to a keyed provider instead of
        # 401-ing — makes default->Claude (Fix 4) safe when
        # ANTHROPIC_API_KEY is still a placeholder. No-op for real keys.
        env_fn = _ENV_KEYS.get(provider_name)
        env_key = env_fn() if env_fn else None
        if _looks_like_placeholder_key(env_key):
            return []
        return [env_key]

    # Backward compat alias (async)
    async def get_api_key_async(
        self, session: AsyncSession, provider_name: str
    ) -> str | None:
        return await self.get_active_key_async(session, provider_name)

    def providers_with_valid_keys(self, session: Session) -> set[str]:
        """Providers that have at least one active, validated key.

        ``valid`` is the status the Add & Validate flow (and the manual
        key test) writes after a successful live API call — so this is
        the set of providers whose models are actually callable.
        Untested / invalid keys, and env-var-only fallbacks that were
        never tested through the table, don't count. Used to filter the
        public model list so the UI never offers a model the gateway
        can't reach.
        """
        rows = session.execute(
            select(ProviderKeyORM.provider_name)
            .where(
                ProviderKeyORM.is_active.is_(True),
                ProviderKeyORM.last_test_status == "valid",
            )
            .distinct()
        ).scalars().all()
        return set(rows)

    async def providers_with_valid_keys_async(
        self, session: AsyncSession
    ) -> set[str]:
        """Async variant of :meth:`providers_with_valid_keys`.

        Used by the reasoning-aware router (P0) to prefer reasoning models
        whose provider has a callable key, so ``auto`` + reasoning doesn't
        elect a keyless reasoning model as the primary and dead-end on the
        provider-key check. Returns the empty set on any error so the
        caller can fall back to best-effort (unfiltered) reasoning routing.
        """
        rows = (
            await session.execute(
                select(ProviderKeyORM.provider_name)
                .where(
                    ProviderKeyORM.is_active.is_(True),
                    ProviderKeyORM.last_test_status == "valid",
                )
                .distinct()
            )
        ).scalars().all()
        return set(rows)

    # ------------------------------------------------------------------
    # Multi-key CRUD
    # ------------------------------------------------------------------

    def list_keys(self, session: Session, provider_name: str) -> list[dict]:
        """List all keys for a provider (masked, with status)."""
        repo = ProviderKeyRepository(session)
        rows = repo.list_by_provider(provider_name)

        result = []
        for r in rows:
            raw = _decode(r.api_key_encrypted)
            result.append({
                "id": r.id,
                "provider_name": r.provider_name,
                "label": r.label,
                "key_masked": _mask(raw),
                "is_active": r.is_active,
                "last_tested_at": r.last_tested_at.isoformat() if r.last_tested_at else None,
                "last_test_status": r.last_test_status or "untested",
                "last_test_latency_ms": r.last_test_latency_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return result

    def add_key(
        self, session: Session, provider_name: str, api_key: str, label: str | None = None
    ) -> ProviderKeyORM:
        """Insert a new provider key."""
        repo = ProviderKeyRepository(session)
        final_label = label or f"Key {self._next_label_num(session, provider_name)}"
        row = repo.create(
            provider_name=provider_name,
            label=final_label,
            api_key_encrypted=_encode(api_key),
        )
        clear_keys_cache()  # B5: bust the resolved-key cache
        return row

    def delete_key(self, session: Session, key_id: str) -> None:
        """Soft-delete a key (set is_active=false).

        Note: this is NOT ``repo.delete`` — the repo's ``delete`` is a
        hard-delete primitive. Public API semantics stay soft so
        existing admin flows / audit trails remain intact.
        """
        repo = ProviderKeyRepository(session)
        row = repo.get_by_id(key_id)
        if row:
            repo.update(row, is_active=False)
            clear_keys_cache()  # B5: bust the resolved-key cache

    def update_test_result(
        self, session: Session, key_id: str, status: str, latency_ms: int | None = None
    ) -> None:
        """Update the test result for a key."""
        repo = ProviderKeyRepository(session)
        repo.record_test_result(key_id, status, latency_ms)
        clear_keys_cache()  # B5: a key flipping to/from "invalid" changes candidates

    def invalidate_key_by_value(
        self, session: Session, provider_name: str, api_key: str
    ) -> str | None:
        """Mark the key whose decoded value matches ``api_key`` invalid.

        Group A / A2 — auto-invalidate on 401. When the provider rejects a
        key at dispatch time, flip its row to ``last_test_status='invalid'``
        and ``is_active=False`` so the round-robin pool stops handing it
        out, then bust the resolved-key cache so the next resolve re-reads.

        Matches on the *active* rows for the provider (the pool the
        round-robin draws from) and decodes each to compare — keys are
        stored encrypted, and the dispatch path only has the plaintext.
        Returns the invalidated key id, or None if no active row matched
        (e.g. the key came from the env/legacy fallback, which has no row).
        """
        repo = ProviderKeyRepository(session)
        for row in repo.list_active_by_provider(provider_name):
            try:
                if _decode(row.api_key_encrypted) != api_key:
                    continue
            except Exception:
                continue
            repo.update(row, is_active=False, last_test_status="invalid")
            row.last_tested_at = datetime.now(timezone.utc)
            session.flush()
            clear_keys_cache()
            logger.warning(
                "Auto-invalidated provider key %s for %s after auth failure",
                row.id,
                provider_name,
            )
            return row.id
        return None

    def _next_label_num(self, session: Session, provider_name: str) -> int:
        repo = ProviderKeyRepository(session)
        return repo.count_by_provider(provider_name) + 1

    # ------------------------------------------------------------------
    # Key validation (calls real API)
    # ------------------------------------------------------------------

    async def test_key(self, provider_name: str, api_key: str) -> dict:
        """Validate an API key by making a minimal real API call.

        Returns: { valid: bool, latency_ms: int, error: str | None }
        """
        try:
            if provider_name == "openai":
                return await self._test_openai(api_key)
            elif provider_name == "anthropic":
                return await self._test_anthropic(api_key)
            elif provider_name == "openrouter":
                return await self._test_openrouter(api_key)
            else:
                return {"valid": False, "latency_ms": 0, "error": f"Unknown provider: {provider_name}"}
        except Exception as e:
            return {"valid": False, "latency_ms": 0, "error": str(e)[:200]}

    async def _test_openai(self, api_key: str) -> dict:
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            ms = round((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                return {"valid": True, "latency_ms": ms, "error": None}
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            return {"valid": False, "latency_ms": ms, "error": err}

    async def _test_anthropic(self, api_key: str) -> dict:
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-3-haiku-20240307", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
            )
            ms = round((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                return {"valid": True, "latency_ms": ms, "error": None}
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            return {"valid": False, "latency_ms": ms, "error": err}

    async def _test_openrouter(self, api_key: str) -> dict:
        # A3: ``GET /api/v1/models`` is PUBLIC on OpenRouter — it returns 200
        # for any key (even a placeholder / garbage), so it can't validate a
        # credential. ``GET /api/v1/auth/key`` requires auth and returns the
        # key's own metadata, so it 401s on a bad key — the correct probe.
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            ms = round((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                return {"valid": True, "latency_ms": ms, "error": None}
            body = (
                resp.json()
                if resp.headers.get("content-type", "").startswith(
                    "application/json"
                )
                else {}
            )
            err = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            return {"valid": False, "latency_ms": ms, "error": err}

    # ------------------------------------------------------------------
    # Legacy compat — key status summary
    # ------------------------------------------------------------------

    def get_key_status(self, session: Session, provider_name: str) -> dict:
        """Summary of key configuration for a provider."""
        keys = self.list_keys(session, provider_name)
        active_keys = [k for k in keys if k["is_active"]]
        valid_keys = [k for k in active_keys if k["last_test_status"] == "valid"]

        # Check legacy sources too
        provider = session.execute(
            select(ProviderORM).where(ProviderORM.name == provider_name)
        ).scalar_one_or_none()
        legacy_db = bool(provider and provider.api_key_encrypted)
        env_fn = _ENV_KEYS.get(provider_name)
        env_configured = bool(env_fn and env_fn())

        total = len(active_keys) + (1 if legacy_db else 0) + (1 if env_configured and not active_keys and not legacy_db else 0)

        return {
            "provider_name": provider_name,
            "configured": total > 0,
            "total_keys": len(active_keys),
            "valid_keys": len(valid_keys),
            "source": "provider_keys" if active_keys else ("database" if legacy_db else ("environment" if env_configured else "none")),
        }

    # Legacy compat
    def set_api_key(self, session: Session, provider_name: str, api_key: str) -> ProviderORM:
        provider = session.execute(
            select(ProviderORM).where(ProviderORM.name == provider_name)
        ).scalar_one_or_none()
        if provider is None:
            provider = ProviderORM(
                provider_id=str(uuid.uuid4()),
                name=provider_name,
                enabled=True,
            )
            session.add(provider)
        provider.api_key_encrypted = _encode(api_key)
        provider.api_key_set_at = datetime.now(timezone.utc)
        session.flush()
        clear_keys_cache()  # B5: legacy key is a resolved-key candidate
        return provider
