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

import base64
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.provider import ProviderORM
from app.models.provider_key import ProviderKeyORM
from app.repositories.provider_key_repository import ProviderKeyRepository


def _encode(key: str) -> str:
    return base64.b64encode(key.encode()).decode()


def _decode(encoded: str) -> str:
    return base64.b64decode(encoded.encode()).decode()


def _mask(raw_key: str) -> str:
    if len(raw_key) <= 8:
        return "••••"
    return raw_key[:4] + "••••" + raw_key[-4:]


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


class ProviderKeyService:

    # ------------------------------------------------------------------
    # Key resolution (round-robin across active valid keys)
    # ------------------------------------------------------------------

    def get_active_key(self, session: Session, provider_name: str) -> str | None:
        """Get next API key for provider via round-robin.

        Priority: provider_keys table → legacy providers.api_key_encrypted → env var.
        Skips keys where last_test_status = 'invalid'.
        """
        repo = ProviderKeyRepository(session)
        rows = repo.list_active_by_provider(provider_name)

        # Filter out keys known to be invalid
        valid_rows = [r for r in rows if r.last_test_status != "invalid"]

        if valid_rows:
            idx = _rr_counters[provider_name] % len(valid_rows)
            _rr_counters[provider_name] += 1
            return _decode(valid_rows[idx].api_key_encrypted)

        # Fallback: legacy single key on providers table
        provider = session.execute(
            select(ProviderORM).where(ProviderORM.name == provider_name)
        ).scalar_one_or_none()
        if provider and provider.api_key_encrypted:
            return _decode(provider.api_key_encrypted)

        # Fallback: env var
        env_fn = _ENV_KEYS.get(provider_name)
        return env_fn() if env_fn else None

    # Backward compat alias
    def get_api_key(self, session: Session, provider_name: str) -> str | None:
        return self.get_active_key(session, provider_name)

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
        return repo.create(
            provider_name=provider_name,
            label=final_label,
            api_key_encrypted=_encode(api_key),
        )

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

    def update_test_result(
        self, session: Session, key_id: str, status: str, latency_ms: int | None = None
    ) -> None:
        """Update the test result for a key."""
        repo = ProviderKeyRepository(session)
        repo.record_test_result(key_id, status, latency_ms)

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
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            ms = round((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                return {"valid": True, "latency_ms": ms, "error": None}
            return {"valid": False, "latency_ms": ms, "error": f"HTTP {resp.status_code}"}

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
        return provider
