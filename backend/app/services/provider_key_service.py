"""Provider API key management — multi-key with round-robin and validation.

Keys are stored in the ``provider_keys`` table and are configured EXCLUSIVELY
through the admin UI. Resolution order is ``provider_keys`` → the legacy
``providers.api_key_encrypted`` single key. There is deliberately **no
environment-variable tier**: the operators of this product are IT staff, and
"edit a file inside a container to add an API key" is the wrong interface.
A provider with no key in either table is unconfigured, and the caller raises
``ProviderUnavailableError`` rather than sending a half-configured request.

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
from app.core.exceptions import ConflictError
from app.core.security import decrypt_provider_key, encrypt_provider_key
from app.models.provider import ProviderORM
from app.models.provider_key import ProviderKeyORM
from app.repositories.provider_key_repository import ProviderKeyRepository

logger = logging.getLogger(__name__)


def _usable_rows(
    rows: list[ProviderKeyORM], provider_name: str, *, log: bool = True
) -> list[tuple[ProviderKeyORM, str]]:
    """The subset of ``rows`` that dispatch will actually send.

    A row is usable when it is neither marked ``invalid`` by a key test nor
    holding a dev placeholder value. Both filters exist because either kind
    of row would 401 silently on dispatch.

    This is the SINGLE definition of "usable" — both the resolver
    (``_resolve_key_candidates``) and the admin badge (``get_key_status``)
    call it, so the UI can never claim a key that dispatch would skip.
    Pass ``log=False`` from read-only/status paths so polling the admin page
    does not spam the log with one warning per poll.

    Returns ``(row, decoded_key)`` pairs so callers that need the row
    metadata and callers that need the key both avoid a second decode.
    """
    out: list[tuple[ProviderKeyORM, str]] = []
    for r in rows:
        if r.last_test_status == "invalid":
            continue
        key = _decode(r.api_key_encrypted)
        if _looks_like_placeholder_key(key):
            if log:
                logger.warning(
                    "Filtering placeholder key %s for provider %s from pool",
                    r.id,
                    provider_name,
                )
            continue
        out.append((r, key))
    return out


def _filter_placeholder_candidates(
    rows: list[ProviderKeyORM], provider_name: str
) -> list[str]:
    """Decoded keys for the rows dispatch will actually send.

    Thin wrapper over :func:`_usable_rows` kept for call-site readability.
    """
    return [key for _row, key in _usable_rows(rows, provider_name)]


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


# NOTE: there is intentionally no ``_ENV_KEYS`` map here any more. Provider
# keys come from the admin UI (``provider_keys``) or the legacy
# ``providers`` table — never from the environment. See the module docstring.

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


async def resolve_provider_key_async(provider_name: str) -> str | None:
    """Resolve a provider key for a caller that has no session of its own.

    The utility clients — embeddings, voice and AI review — are not part of
    the chat dispatch path and never had a request-scoped session to hand in,
    so they each read ``settings.<provider>_api_key`` directly. That made them
    a SECOND live source for the same value: an admin could rotate the OpenAI
    key in the admin UI, chat would follow it within the cache TTL, and those
    three would go on sending the stale environment value with nothing on
    screen saying so. Removing that second source is the whole point of the
    UI-owned model, so they resolve through here instead.

    Opens its own short-lived session. An async session does not check a
    connection out of the pool until a statement runs, and ``_keys_cache``
    sits in front of the only statement — so a burst of embedding calls costs
    at most one SELECT per 30s, not one per call.

    Returns ``None`` when the provider has no usable key, and also when the
    lookup itself fails: callers turn that into a clean "provider not
    configured / unavailable" error, which is the right answer either way.
    """
    from app.core.db import get_async_session_factory

    try:
        async with get_async_session_factory()() as session:
            return await ProviderKeyService().get_active_key_async(
                session, provider_name
            )
    except Exception as exc:  # noqa: BLE001 — utility path, never 500 on this
        logger.warning(
            "provider key lookup failed for %s: %s", provider_name, exc
        )
        return None


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

        Priority: provider_keys table → legacy providers.api_key_encrypted.
        No environment tier — keys are UI-configured only. Decoded to plain
        strings so the cache holds no ORM.
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

        return []

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
        Priority: provider_keys table → legacy providers.api_key_encrypted.
        No environment tier — keys are UI-configured only. Decoded to plain
        strings so the cache holds no ORM.
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

        return []

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
        """Insert a new provider key.

        Also records that this provider is now database-owned, so the
        first-boot env seed can never act on it again — see
        ``app.services.provider_env_seed``. Without this mark, an operator
        who adds a key here and later deletes it could have a value from the
        environment appear in its place on the next restart, which is exactly
        the "silently undo the operator" failure the seed rule exists to
        avoid.
        """
        from app.services.provider_env_seed import KIND_KEY, mark_provider_owned

        repo = ProviderKeyRepository(session)
        final_label = label or f"Key {self._next_label_num(session, provider_name)}"
        existing = repo.get_by_provider_and_label(provider_name, final_label)
        if existing is not None:
            if existing.is_active:
                raise ConflictError(
                    f"An active key with label '{final_label}' already exists "
                    f"for provider '{provider_name}'.",
                    code="provider_key_label_conflict",
                )
            row = repo.update(
                existing,
                api_key_encrypted=_encode(api_key),
                is_active=True,
                last_test_status="untested",
                last_tested_at=None,
                last_test_latency_ms=None,
            )
        else:
            row = repo.create(
                provider_name=provider_name,
                label=final_label,
                api_key_encrypted=_encode(api_key),
            )
        # Insert-if-absent, so the seeder's own "env-seed" reason survives
        # when this is called from the seed path.
        mark_provider_owned(session, KIND_KEY, provider_name, "admin-ui")
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

    async def test_key(
        self, provider_name: str, api_key: str, *, base_url: str | None = None
    ) -> dict:
        """Validate an API key by making a minimal real API call.

        ``base_url`` applies only to self-hosted OpenAI-compatible
        providers (``base_urls.SELF_HOSTED_PROVIDERS``): the add-key
        route passes the operator-entered endpoint so it can be probed
        BEFORE anything is persisted; the re-test route omits it and the
        stored/env endpoint is resolved instead.

        Returns: { valid: bool, latency_ms: int, error: str | None }
        """
        from app.services.providers.base_urls import SELF_HOSTED_PROVIDERS

        try:
            if provider_name == "openai":
                return await self._test_openai(api_key)
            elif provider_name == "anthropic":
                return await self._test_anthropic(api_key)
            elif provider_name == "google":
                return await self._test_google(api_key)
            elif provider_name == "openrouter":
                return await self._test_openrouter(api_key)
            elif provider_name == "replicate":
                return await self._test_replicate(api_key)
            elif provider_name == "elevenlabs":
                return await self._test_elevenlabs(api_key)
            elif provider_name in SELF_HOSTED_PROVIDERS:
                return await self._test_openai_compatible(
                    provider_name, api_key, base_url=base_url
                )
            else:
                return await self._test_registered_chat_provider(
                    provider_name, api_key
                )
        except Exception as exc:  # noqa: BLE001 - never expose secret-bearing text
            logger.error(
                "Unexpected key validation failure for provider %s (%s)",
                provider_name,
                type(exc).__name__,
            )
            return {
                "valid": False,
                "latency_ms": 0,
                "error": "internal validation error",
                "error_type": "internal",
                "provider_status": None,
            }

    @staticmethod
    def _provider_error_message(resp: httpx.Response, api_key: str) -> str:
        """Extract a short upstream message without ever echoing the key."""
        message = ""
        if resp.headers.get("content-type", "").startswith("application/json"):
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or error.get("detail") or "")
                elif isinstance(error, str):
                    message = error
                if not message:
                    message = str(body.get("message") or body.get("detail") or "")
        message = message.replace(api_key, "[redacted]").strip()
        return message[:200] or "provider returned no error message"

    async def _test_bearer_endpoint(
        self,
        provider_name: str,
        api_key: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
        use_bearer: bool = True,
    ) -> dict:
        t0 = time.perf_counter()
        headers = {"Authorization": f"Bearer {api_key}"} if use_bearer else {}
        headers.update(extra_headers or {})
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            return {
                "valid": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error": "connection timed out while validating key",
                "error_type": "timeout",
                "provider_status": None,
            }
        except httpx.RequestError:
            return {
                "valid": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error": "could not connect to provider while validating key",
                "error_type": "connection",
                "provider_status": None,
            }

        ms = round((time.perf_counter() - t0) * 1000)
        if resp.status_code == 200:
            return {
                "valid": True,
                "latency_ms": ms,
                "error": None,
                "error_type": None,
                "provider_status": 200,
            }
        message = self._provider_error_message(resp, api_key)
        kind = "authentication" if resp.status_code in (401, 403) else "provider"
        return {
            "valid": False,
            "latency_ms": ms,
            "error": f"provider returned HTTP {resp.status_code}: {message}",
            "error_type": kind,
            "provider_status": resp.status_code,
        }

    async def _test_registered_chat_provider(
        self, provider_name: str, api_key: str
    ) -> dict:
        """Probe the /models endpoint of a registered chat adapter."""
        from app.services.provider_dispatcher import ProviderDispatcher
        from app.services.providers.base_urls import resolve_base_url
        from app.services.providers.openai_compatible_adapter import (
            OpenAICompatibleAdapter,
        )

        adapter = ProviderDispatcher().adapters.get(provider_name)
        if adapter is None:
            if provider_name in {"cohere", "mistral", "perplexity"}:
                return {
                    "valid": False,
                    "latency_ms": 0,
                    "error": (
                        f"provider '{provider_name}' is listed in the catalog, "
                        "but its chat adapter is not implemented"
                    ),
                    "error_type": "not_implemented",
                    "provider_status": None,
                }
            return {
                "valid": False,
                "latency_ms": 0,
                "error": f"unknown provider: {provider_name}",
                "error_type": "unknown_provider",
                "provider_status": None,
            }
        if not isinstance(adapter, OpenAICompatibleAdapter):
            return {
                "valid": False,
                "latency_ms": 0,
                "error": f"provider '{provider_name}' has no key validation probe",
                "error_type": "not_implemented",
                "provider_status": None,
            }

        url = resolve_base_url(provider_name, adapter.base_url).rstrip("/")
        return await self._test_bearer_endpoint(
            provider_name, api_key, f"{url}/models"
        )

    async def _test_openai(self, api_key: str) -> dict:
        return await self._test_bearer_endpoint(
            "openai", api_key, "https://api.openai.com/v1/models"
        )

    async def _test_anthropic(self, api_key: str) -> dict:
        return await self._test_bearer_endpoint(
            "anthropic",
            api_key,
            "https://api.anthropic.com/v1/models",
            extra_headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            use_bearer=False,
        )

    async def _test_google(self, api_key: str) -> dict:
        return await self._test_bearer_endpoint(
            "google",
            api_key,
            "https://generativelanguage.googleapis.com/v1beta/models",
            extra_headers={"x-goog-api-key": api_key},
            use_bearer=False,
        )

    async def _test_openrouter(self, api_key: str) -> dict:
        # A3: ``GET /api/v1/models`` is PUBLIC on OpenRouter — it returns 200
        # for any key (even a placeholder / garbage), so it can't validate a
        # credential. ``GET /api/v1/key`` requires auth and returns the
        # key's own metadata, so it 401s on a bad key — the correct probe.
        return await self._test_bearer_endpoint(
            "openrouter", api_key, "https://openrouter.ai/api/v1/key"
        )

    async def _test_replicate(self, api_key: str) -> dict:
        """Validate a Replicate key via ``GET /v1/account``.

        Returns the same dict shape as the other ``_test_*`` probes
        (``{valid, latency_ms, error}``) so ``add_provider_key`` and the
        re-test route consume it unchanged.

        Token isolation: the error string is NEVER built from the key, the
        response body, or raw exception text — only static status labels —
        so a key fragment can't leak into logs or the API response.
        """
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.replicate.com/v1/account",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            ms = round((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                return {"valid": True, "latency_ms": ms, "error": None}
            if resp.status_code == 401:
                return {"valid": False, "latency_ms": ms, "error": "invalid key (HTTP 401)"}
            return {"valid": False, "latency_ms": ms, "error": f"HTTP {resp.status_code}"}
        except httpx.TimeoutException:
            return {
                "valid": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error": "timeout",
            }
        except Exception:  # noqa: BLE001 — never surface raw exception text
            return {
                "valid": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error": "connection error",
            }

    async def _test_elevenlabs(self, api_key: str) -> dict:
        """Validate an ElevenLabs key via ``GET /v1/user``.

        Same dict shape as the other ``_test_*`` probes. Error strings are
        static labels only — never the key, response body, or raw exception
        text (token-isolation rule).
        """
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.elevenlabs.io/v1/user",
                    headers={"xi-api-key": api_key},
                )
            ms = round((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                return {"valid": True, "latency_ms": ms, "error": None}
            if resp.status_code == 401:
                return {"valid": False, "latency_ms": ms, "error": "invalid key (HTTP 401)"}
            return {"valid": False, "latency_ms": ms, "error": f"HTTP {resp.status_code}"}
        except httpx.TimeoutException:
            return {
                "valid": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error": "timeout",
            }
        except Exception:  # noqa: BLE001 — never surface raw exception text
            return {
                "valid": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error": "connection error",
            }

    async def _test_openai_compatible(
        self, provider_name: str, api_key: str, *, base_url: str | None = None
    ) -> dict:
        """Probe a self-hosted OpenAI-compatible box via ``GET {base}/models``.

        Same dict shape as the other ``_test_*`` probes; error strings
        are static labels only (token-isolation rule). Note: some local
        OpenAI-compatible servers serve ``/models`` without checking the
        Authorization header, so a 200 proves reachability more than
        key validity — acceptable for an operator-owned box.
        """
        from app.services.providers.base_urls import resolve_base_url

        url = (base_url or "").strip().rstrip("/")
        if not url:
            url = resolve_base_url(provider_name, "")
        if not url:
            return {
                "valid": False,
                "latency_ms": 0,
                "error": "endpoint not configured — set the provider base URL first",
            }

        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            ms = round((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                return {"valid": True, "latency_ms": ms, "error": None}
            if resp.status_code in (401, 403):
                return {"valid": False, "latency_ms": ms, "error": f"invalid key (HTTP {resp.status_code})"}
            return {"valid": False, "latency_ms": ms, "error": f"HTTP {resp.status_code}"}
        except httpx.TimeoutException:
            return {
                "valid": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error": "timeout — endpoint unreachable",
            }
        except Exception:  # noqa: BLE001 — never surface raw exception text
            return {
                "valid": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error": "connection error — endpoint unreachable",
            }

    # ------------------------------------------------------------------
    # Legacy compat — key status summary
    # ------------------------------------------------------------------

    def get_key_status(self, session: Session, provider_name: str) -> dict:
        """Summary of key configuration for a provider.

        The counts here mean exactly what dispatch would do. ``total_keys``
        is the number of rows :meth:`_resolve_key_candidates` would actually
        put in the round-robin pool, so it excludes rows a key test marked
        ``invalid`` and rows holding a dev placeholder — both of which the
        resolver skips. Before this, a provider whose only key was invalid
        reported ``configured: true`` while every request to it failed with
        "no API key configured"; the badge now says what dispatch does.

        Rows that are excluded here are NOT hidden from the operator — the
        expanded key panel still lists every row with its test status via
        :meth:`list_keys`. Only the "is this provider callable" summary is
        filtered.

        ``source`` is ``provider_keys | database | none``. There is no
        ``environment`` value: env is not a key source (see module docstring).
        """
        keys = self.list_keys(session, provider_name)
        repo = ProviderKeyRepository(session)
        # Same filter the resolver applies, so the badge cannot claim a key
        # dispatch would skip. log=False: the admin page polls this.
        usable = _usable_rows(
            repo.list_active_by_provider(provider_name), provider_name, log=False
        )
        valid_keys = [
            row for row, _key in usable if row.last_test_status == "valid"
        ]

        # Check the legacy single-key table too
        provider = session.execute(
            select(ProviderORM).where(ProviderORM.name == provider_name)
        ).scalar_one_or_none()
        legacy_db = bool(provider and provider.api_key_encrypted)

        total = len(usable) + (1 if legacy_db else 0)

        result = {
            "provider_name": provider_name,
            "configured": total > 0,
            "total_keys": len(usable),
            "valid_keys": len(valid_keys),
            "source": (
                "provider_keys" if usable else ("database" if legacy_db else "none")
            ),
            # Rows the operator has that dispatch will NOT use (invalid or
            # placeholder). Lets the UI explain a "no key" badge on a
            # provider that visibly has key rows, instead of looking broken.
            "unusable_keys": len([k for k in keys if k["is_active"]]) - len(usable),
        }

        # Self-hosted providers expose their operator-set endpoint so the
        # admin UI can prefill the form and show an "endpoint not set"
        # state. Effective value (DB → "") — never a secret.
        from app.services.providers.base_urls import (
            SELF_HOSTED_PROVIDERS,
            resolve_base_url,
        )

        if provider_name in SELF_HOSTED_PROVIDERS:
            result["base_url"] = resolve_base_url(provider_name, "")
        return result

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
