"""Search service — priority-ordered provider fallback + health record.

Tries each enabled provider for the tenant in priority order (0 first)
until one returns results. Providers currently marked 'down' are
skipped. Success/failure update the provider row's health + stats so
the admin dashboard and the skip logic have persisted signal.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decrypt_provider_key, encrypt_provider_key
from app.models.search_provider import SearchProviderORM
from app.services.search.base import (
    SearchNotConfiguredError,
    SearchResults,
    SearchUnavailableError,
)
from app.services.search.providers import get_adapter
from app.services.search.url_resolve import resolve_results

logger = structlog.get_logger(__name__)

# Per-provider hard ceiling (the adapter also sets an httpx timeout).
_PROVIDER_TIMEOUT_S = 10.0


def encode_key(plaintext: str) -> str:
    """AES-256-GCM encode a search provider key (same as provider keys)."""
    return encrypt_provider_key(plaintext, settings.effective_master_key)


def decode_key(encoded: str) -> str:
    return decrypt_provider_key(encoded, settings.effective_master_key)


class SearchService:
    async def get_enabled_providers(
        self, session: AsyncSession, tenant_id: str
    ) -> list[SearchProviderORM]:
        """Enabled providers for the tenant, primary first (priority asc)."""
        rows = (
            await session.execute(
                select(SearchProviderORM)
                .where(
                    SearchProviderORM.tenant_id == tenant_id,
                    SearchProviderORM.is_enabled.is_(True),
                )
                .order_by(SearchProviderORM.priority.asc())
            )
        ).scalars().all()
        return list(rows)

    async def search(
        self, session: AsyncSession, tenant_id: str, query: str
    ) -> SearchResults:
        """Run the query through the fallback chain.

        Raises SearchNotConfiguredError when the tenant has no enabled
        providers, SearchUnavailableError when every provider failed.
        """
        providers = await self.get_enabled_providers(session, tenant_id)
        if not providers:
            raise SearchNotConfiguredError("No search providers configured")

        errors: list[str] = []
        for pos, cfg in enumerate(providers):
            if cfg.health_status == "down":
                errors.append(f"{cfg.provider}: skipped (down)")
                continue
            start = time.monotonic()
            try:
                adapter = get_adapter(cfg.provider, decode_key(cfg.api_key_encrypted))
                results = await asyncio.wait_for(
                    adapter.search(query, int(cfg.max_results or 5)),
                    timeout=_PROVIDER_TIMEOUT_S,
                )
                latency = int((time.monotonic() - start) * 1000)
                await self._record_success(session, cfg, latency)
                # FIX126 U1 — news.google.com redirect links become the
                # publisher's URL (bounded, cached, never raises; the
                # provider's latency above excludes it).
                try:
                    results = await resolve_results(results)
                except Exception as exc:  # noqa: BLE001 — links stay as given
                    logger.warning("search_url_resolve_error", error=str(exc)[:200])
                logger.info(
                    "search_completed",
                    provider=cfg.provider,
                    tenant_id=tenant_id,
                    latency_ms=latency,
                    results_count=len(results.results),
                    fallback_position=pos,
                )
                return results
            except Exception as e:  # noqa: BLE001 — any failure → next provider
                latency = int((time.monotonic() - start) * 1000)
                await self._record_failure(session, cfg, str(e)[:500], latency)
                errors.append(f"{cfg.provider}: {str(e)[:120]}")
                logger.warning(
                    "search_provider_failed",
                    provider=cfg.provider,
                    tenant_id=tenant_id,
                    error=str(e)[:200],
                    will_try_fallback=pos < len(providers) - 1,
                )
                continue

        logger.error(
            "search_all_providers_failed", tenant_id=tenant_id, errors=errors
        )
        raise SearchUnavailableError(
            f"All search providers failed: {'; '.join(errors)}"
        )

    async def _record_success(
        self, session: AsyncSession, cfg: SearchProviderORM, latency_ms: int
    ) -> None:
        now = datetime.now(timezone.utc)
        cfg.total_queries = int(cfg.total_queries or 0) + 1
        cfg.health_status = "degraded" if latency_ms > 5000 else "healthy"
        cfg.last_health_check = now
        # Rolling average latency (simple EWMA-ish: average of old + new).
        prev = cfg.avg_latency_ms
        cfg.avg_latency_ms = latency_ms if prev is None else (prev + latency_ms) // 2
        cfg.uptime_pct = self._uptime(cfg)
        try:
            await session.flush()
        except Exception:  # noqa: BLE001 — stats are best-effort
            pass

    async def _record_failure(
        self, session: AsyncSession, cfg: SearchProviderORM, reason: str, latency_ms: int
    ) -> None:
        now = datetime.now(timezone.utc)
        cfg.total_queries = int(cfg.total_queries or 0) + 1
        cfg.total_failures = int(cfg.total_failures or 0) + 1
        cfg.health_status = "down"
        cfg.last_failure_at = now
        cfg.last_failure_reason = reason
        cfg.last_health_check = now
        cfg.uptime_pct = self._uptime(cfg)
        try:
            await session.flush()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _uptime(cfg: SearchProviderORM) -> float:
        total = int(cfg.total_queries or 0)
        if total <= 0:
            return 100.0
        ok = total - int(cfg.total_failures or 0)
        return round(max(0.0, ok) / total * 100.0, 2)

    # ------------------------------------------------------------------
    # Admin / config helpers
    # ------------------------------------------------------------------

    async def add_provider(
        self,
        session: AsyncSession,
        tenant_id: str,
        provider: str,
        api_key: str,
        *,
        priority: int = 0,
        max_results: int = 5,
        display_name: str | None = None,
    ) -> SearchProviderORM:
        row = SearchProviderORM(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            provider=provider,
            display_name=display_name,
            api_key_encrypted=encode_key(api_key),
            priority=priority,
            max_results=max_results,
        )
        session.add(row)
        await session.flush()
        return row


search_service = SearchService()
