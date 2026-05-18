"""Model discovery orchestrator.

Calls every provider adapter, enriches with litellm pricing, and
upserts ``model_configs`` rows. Honors ``is_manual_override=True``
(never overwrites admin edits) and soft-deactivates models that
disappeared from the upstream APIs.

Concurrency model
-----------------
``sync_all`` is async (provider HTTP calls are awaited concurrently)
but the DB writes happen against a SYNC SQLAlchemy ``Session``. We
dispatch the upsert loop to a thread via ``asyncio.to_thread`` so the
event loop isn't blocked while psycopg executes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from sqlalchemy.orm import Session

from app.models.model_config import ModelConfigORM
from app.services.discovery.pricing_fetcher import PricingFetcher
from app.services.discovery.provider_discovery import (
    DiscoveredModel,
    PROVIDER_DISCOVERY,
)
from app.services.provider_key_service import ProviderKeyService


logger = structlog.get_logger(__name__)


@dataclass
class SyncResult:
    """Summary returned to the scheduler / admin API."""

    provider: str | None  # None = all providers
    inserted: int = 0
    updated: int = 0
    skipped_override: int = 0
    deactivated: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    synced_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped_override": self.skipped_override,
            "deactivated": self.deactivated,
            "errors": list(self.errors),
            "duration_seconds": round(self.duration_seconds, 3),
            "synced_at": self.synced_at.isoformat(),
        }


def _enrich_with_pricing(
    model: DiscoveredModel,
    pricing_fetcher: PricingFetcher,
) -> None:
    """Fill missing pricing/context fields from the litellm cache.

    Mutates ``model`` in place. Does NOT overwrite values the
    provider API already populated (notably OpenRouter, see
    ``DiscoveredModel.cost_from_provider``). Does NOT overwrite a
    non-default ``max_context_tokens`` set by the provider adapter.
    """
    if model.cost_from_provider:
        # Provider-supplied pricing is authoritative — skip litellm
        # entirely. Avoids the case where litellm lags behind a
        # price change OpenRouter already publishes.
        return

    pricing = pricing_fetcher.get_model_pricing(model.model, model.provider)
    if not pricing:
        return

    if model.cost_per_input_token == 0.0:
        cost_in = pricing.get("input_cost_per_token")
        if isinstance(cost_in, (int, float)) and cost_in >= 0:
            model.cost_per_input_token = float(cost_in)
    if model.cost_per_output_token == 0.0:
        cost_out = pricing.get("output_cost_per_token")
        if isinstance(cost_out, (int, float)) and cost_out >= 0:
            model.cost_per_output_token = float(cost_out)
    if model.max_context_tokens == 128000:
        max_tokens = pricing.get("max_tokens") or pricing.get("max_input_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            model.max_context_tokens = max_tokens


def _derive_cost_tier(price: float) -> str:
    """Same threshold helper as ``model_registry._derive_cost_tier``.

    Duplicated to avoid an import cycle — the registry module imports
    from this package indirectly via the lifespan wiring, and pulling
    the helper from there during sync risks an ImportError on cold
    startup.
    """
    if price == 0.0:
        return "standard"
    if price <= 0.0000005:
        return "budget"
    if price <= 0.000003:
        return "standard"
    return "premium"


class ModelDiscoveryService:
    """Coordinates the sync across providers, pricing, and DB."""

    def __init__(
        self,
        *,
        pricing_fetcher: PricingFetcher | None = None,
        provider_key_service: ProviderKeyService | None = None,
    ) -> None:
        self.pricing_fetcher = pricing_fetcher or PricingFetcher()
        self.provider_key_service = provider_key_service or ProviderKeyService()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def sync_all(self, db: Session) -> SyncResult:
        """Sync every configured provider into ``model_configs``.

        Returns a single aggregated ``SyncResult`` with totals across
        all providers. Errors from individual provider adapters are
        captured in ``result.errors`` and never raised — partial
        success is the design goal.
        """
        return await self._sync(db, provider_filter=None)

    async def sync_provider(
        self, provider: str, db: Session
    ) -> SyncResult:
        """Sync a single provider — used by the admin trigger."""
        return await self._sync(db, provider_filter=provider)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _sync(
        self,
        db: Session,
        *,
        provider_filter: str | None,
    ) -> SyncResult:
        start = time.perf_counter()
        result = SyncResult(provider=provider_filter)

        # 1. Refresh litellm pricing cache. Failure → empty dict;
        #    enrichment becomes a no-op for this run, DB rows keep
        #    their last-known values.
        await self.pricing_fetcher.fetch()

        # 2. Resolve which providers to hit + their API keys.
        if provider_filter is not None:
            providers = {provider_filter: PROVIDER_DISCOVERY.get(provider_filter)}
            if providers[provider_filter] is None:
                result.errors.append(
                    f"unknown provider '{provider_filter}'"
                )
                result.duration_seconds = time.perf_counter() - start
                return result
        else:
            providers = dict(PROVIDER_DISCOVERY)

        # API keys are read off the SYNC session — already on the
        # caller's thread, no need to dispatch.
        api_keys: dict[str, str | None] = {}
        for provider_name in providers:
            try:
                api_keys[provider_name] = (
                    self.provider_key_service.get_api_key(db, provider_name)
                )
            except Exception as exc:
                api_keys[provider_name] = None
                result.errors.append(
                    f"{provider_name}: key lookup failed: {exc}"
                )

        # 3. Run all provider discoveries concurrently. Each adapter
        #    swallows its own errors and returns []; we still wrap
        #    in gather(..., return_exceptions=True) as a belt-and-
        #    braces guard.
        coros = [
            adapter(api_keys.get(name))
            for name, adapter in providers.items()
        ]
        discovered_lists = await asyncio.gather(*coros, return_exceptions=True)

        seen_keys: set[tuple[str, str]] = set()
        all_discovered: list[DiscoveredModel] = []
        # Providers we have authoritative results for. A provider that
        # returned [] (missing API key, network failure, schema drift)
        # is intentionally NOT added — soft-deactivate must skip those
        # providers entirely so their seed/auto rows survive transient
        # outages instead of getting wiped to is_active=False.
        synced_providers: set[str] = set()
        for (provider_name, _), entry in zip(providers.items(), discovered_lists):
            if isinstance(entry, BaseException):
                result.errors.append(
                    f"{provider_name}: adapter raised: {entry}"
                )
                continue
            if not entry:
                continue
            synced_providers.add(provider_name)
            for model in entry:
                _enrich_with_pricing(model, self.pricing_fetcher)
                all_discovered.append(model)
                seen_keys.add((model.provider, model.model))

        # 4. Upsert + soft-deactivate in a single thread hop so the
        #    event loop doesn't block on psycopg I/O.
        def _persist() -> tuple[int, int, int, int]:
            return self._persist_results(
                db,
                discovered=all_discovered,
                seen_keys=seen_keys,
                synced_providers=synced_providers,
                provider_filter=provider_filter,
            )

        inserted, updated, skipped, deactivated = await asyncio.to_thread(_persist)
        result.inserted = inserted
        result.updated = updated
        result.skipped_override = skipped
        result.deactivated = deactivated

        # 5. Refresh the in-memory registry so the new rows are live
        #    without waiting for the next process restart. The import
        #    is local to avoid a circular dependency at module-load
        #    time (registry → discovery → registry).
        from app.services.providers.model_registry import _registry
        try:
            await _registry.reload(db)
        except Exception as exc:
            result.errors.append(f"registry reload failed: {exc}")

        result.duration_seconds = time.perf_counter() - start
        result.synced_at = datetime.now(timezone.utc)
        logger.info(
            "model_discovery_sync_complete",
            **result.to_dict(),
        )
        return result

    def _persist_results(
        self,
        db: Session,
        *,
        discovered: list[DiscoveredModel],
        seen_keys: set[tuple[str, str]],
        synced_providers: set[str],
        provider_filter: str | None,
    ) -> tuple[int, int, int, int]:
        """Apply the upsert + soft-deactivate logic.

        Returns ``(inserted, updated, skipped_override, deactivated)``.
        Commits at the end; rolls back on any unexpected error.
        """
        inserted = 0
        updated = 0
        skipped_override = 0
        deactivated = 0
        now = datetime.now(timezone.utc)

        try:
            existing = {
                (r.provider, r.model): r
                for r in db.query(ModelConfigORM).all()
            }

            # --- upsert ---
            for model in discovered:
                key = (model.provider, model.model)
                row = existing.get(key)
                if row is None:
                    db.add(
                        ModelConfigORM(
                            provider=model.provider,
                            model=model.model,
                            supports_streaming=model.supports_streaming,
                            supports_tools=model.supports_tools,
                            supports_vision=model.supports_vision,
                            max_context_tokens=model.max_context_tokens,
                            cost_tier=_derive_cost_tier(
                                model.cost_per_input_token
                            ),
                            latency_tier="standard",
                            cost_per_input_token=model.cost_per_input_token,
                            cost_per_output_token=model.cost_per_output_token,
                            source="auto",
                            is_manual_override=False,
                            is_active=True,
                            last_synced_at=now,
                        )
                    )
                    inserted += 1
                    continue

                if row.is_manual_override:
                    # Touch the timestamp so admins can see the sync
                    # *considered* this row, but don't overwrite the
                    # admin's values.
                    row.last_synced_at = now
                    skipped_override += 1
                    continue

                row.supports_streaming = model.supports_streaming
                row.supports_tools = model.supports_tools
                row.supports_vision = model.supports_vision
                row.max_context_tokens = model.max_context_tokens
                row.cost_per_input_token = model.cost_per_input_token
                row.cost_per_output_token = model.cost_per_output_token
                row.cost_tier = _derive_cost_tier(model.cost_per_input_token)
                row.is_active = True
                row.last_synced_at = now
                # Promote 'seed' rows to 'auto' once the sync has
                # confirmed them — keeps the source column meaningful.
                if row.source == "seed":
                    row.source = "auto"
                updated += 1

            # --- soft-deactivate models that vanished ---
            # Only consider rows whose provider successfully reported a
            # non-empty model list this run. A provider that returned []
            # (missing API key, network failure, schema drift) carries no
            # signal that its rows have actually disappeared upstream, so
            # leaving them active is the safe default. This guards the
            # seed catalog from being silently wiped on a fresh install
            # where keys haven't been entered yet.
            for (provider_name, model_name), row in existing.items():
                if (provider_name, model_name) in seen_keys:
                    continue
                if provider_name not in synced_providers:
                    continue
                # When syncing a single provider, only consider rows
                # belonging to that provider for deactivation. A full
                # sync considers all rows.
                if provider_filter is not None and provider_name != provider_filter:
                    continue
                if row.is_manual_override:
                    continue
                if not row.is_active:
                    continue
                row.is_active = False
                row.last_synced_at = now
                deactivated += 1

            db.commit()
        except Exception:
            db.rollback()
            raise

        return inserted, updated, skipped_override, deactivated
