"""Batch B — auto-discover models for the self-hosted Sran AI provider.

Covers:
- discover_sranai: endpoint resolution (skip when unset), /models parse,
  self-hosted context default, non-chat filtering
- per-provider auto-add allowlist: sranai inserts while other providers
  stay log-only under the untouched global flag
- manual-override rows are never clobbered
- cost-tier helper deduplication (registry and discovery agree)
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models.model_config import ModelConfigORM
from app.services.discovery.discovery_service import ModelDiscoveryService
from app.services.discovery.provider_discovery import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    SELF_HOSTED_DEFAULT_CONTEXT_TOKENS,
    DiscoveredModel,
    PROVIDER_DISCOVERY,
    discover_sranai,
)
from app.services.providers import base_urls


@pytest.fixture()
def fresh_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_base_url_cache():
    base_urls.clear_base_url_cache()
    yield
    base_urls.clear_base_url_cache()


def _seed_endpoint(value: str | None) -> None:
    base_urls._db_cache["sranai"] = (time.monotonic() + 30.0, value)


# ---------------------------------------------------------------------------
# discover_sranai
# ---------------------------------------------------------------------------

def test_sranai_registered_in_discovery_table():
    assert PROVIDER_DISCOVERY["sranai"] is discover_sranai


@pytest.mark.asyncio
async def test_discover_skips_quietly_without_endpoint():
    _seed_endpoint(None)
    assert await discover_sranai("some-key") == []


@pytest.mark.asyncio
@respx.mock
async def test_discover_parses_models_with_self_hosted_context_default():
    _seed_endpoint("http://box:8000/v1")
    respx.get("http://box:8000/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "sran-chat-7b"},                          # no context info
                    {"id": "sran-chat-70b", "context_length": 131072},  # reported
                    {"id": "text-embedding-sran"},                   # non-chat → filtered
                ]
            },
        )
    )
    models = await discover_sranai("sran-key")
    by_id = {m.model: m for m in models}

    assert set(by_id) == {"sran-chat-7b", "sran-chat-70b"}
    # Payload silent on context → self-hosted default, not the 128k sentinel.
    assert by_id["sran-chat-7b"].max_context_tokens == SELF_HOSTED_DEFAULT_CONTEXT_TOKENS
    # Reported context wins.
    assert by_id["sran-chat-70b"].max_context_tokens == 131072
    # Self-hosted box: per-token price genuinely zero.
    assert by_id["sran-chat-7b"].cost_per_input_token == 0.0
    assert all(m.provider == "sranai" for m in models)


@pytest.mark.asyncio
@respx.mock
async def test_discover_box_error_returns_empty():
    _seed_endpoint("http://box:8000/v1")
    respx.get("http://box:8000/v1/models").mock(side_effect=httpx.ConnectError("down"))
    assert await discover_sranai("sran-key") == []


# ---------------------------------------------------------------------------
# Per-provider auto-add allowlist
# ---------------------------------------------------------------------------

def _persist(service: ModelDiscoveryService, db, discovered):
    return service._persist_results(
        db,
        discovered=discovered,
        seen_keys={(m.provider, m.model) for m in discovered},
        synced_providers={m.provider for m in discovered},
        provider_filter=None,
    )


def test_sranai_auto_adds_while_other_providers_stay_log_only(fresh_db_session):
    from app.core.config import settings

    # Global flag untouched (False) — this is the invariant under test.
    assert settings.model_discovery_auto_add is False
    assert "sranai" in settings.model_discovery_auto_add_providers_set

    discovered = [
        DiscoveredModel(provider="sranai", model="sran-chat-7b",
                        max_context_tokens=SELF_HOSTED_DEFAULT_CONTEXT_TOKENS),
        DiscoveredModel(provider="openrouter", model="brand-new/unvetted-model"),
    ]
    inserted, updated, skipped, deactivated, discovered_new, new_models = _persist(
        ModelDiscoveryService(), fresh_db_session, discovered
    )

    assert inserted == 1                      # sranai only
    assert discovered_new == 1                # openrouter stayed log-only
    assert new_models == ["openrouter/brand-new/unvetted-model"]

    rows = fresh_db_session.query(ModelConfigORM).all()
    assert [(r.provider, r.model) for r in rows] == [("sranai", "sran-chat-7b")]
    row = rows[0]
    assert row.source == "auto"
    assert row.is_manual_override is False
    assert row.is_active is True
    assert row.max_context_tokens == SELF_HOSTED_DEFAULT_CONTEXT_TOKENS
    assert row.cost_tier == "standard"        # 0.0 → neutral standard
    assert row.cost_per_input_token == 0.0


def test_manual_override_rows_survive_discovery(fresh_db_session):
    fresh_db_session.add(
        ModelConfigORM(
            provider="sranai",
            model="sran-chat-7b",
            max_context_tokens=8192,          # admin-curated value
            cost_tier="budget",
            source="manual",
            is_manual_override=True,
            is_active=True,
        )
    )
    fresh_db_session.commit()

    discovered = [
        DiscoveredModel(provider="sranai", model="sran-chat-7b",
                        max_context_tokens=SELF_HOSTED_DEFAULT_CONTEXT_TOKENS),
    ]
    inserted, updated, skipped, *_ = _persist(
        ModelDiscoveryService(), fresh_db_session, discovered
    )

    assert inserted == 0
    assert skipped == 1
    row = fresh_db_session.query(ModelConfigORM).one()
    assert row.max_context_tokens == 8192     # untouched
    assert row.cost_tier == "budget"


def test_allowlist_env_parsing(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "model_discovery_auto_add_providers", " sranai , OtherBox ,"
    )
    assert settings.model_discovery_auto_add_providers_set == frozenset(
        {"sranai", "otherbox"}
    )


# ---------------------------------------------------------------------------
# Endpoint write must be visible to discovery within the same request
# ---------------------------------------------------------------------------

def test_set_stored_base_url_primes_resolver_before_commit(fresh_db_session):
    # The add-key route saves the endpoint (flush, no commit) and then
    # runs discovery in the SAME request. The resolver reads via its own
    # session, so the only way it can see the uncommitted value is the
    # write-through cache prime. Regression guard for the live bug where
    # discovery returned 0 models on first save.
    base_urls.set_stored_base_url(fresh_db_session, "sranai", "http://box:8000/v1")
    assert base_urls.resolve_base_url("sranai", "") == "http://box:8000/v1"


# ---------------------------------------------------------------------------
# Cost-tier dedupe
# ---------------------------------------------------------------------------

def test_cost_tier_single_source_of_truth():
    from app.services.discovery.discovery_service import _derive_cost_tier as disc
    from app.services.providers.model_registry import _derive_cost_tier as reg
    from app.services.providers.cost_tiers import derive_cost_tier

    for price in (0.0, 0.0000004, 0.0000005, 0.000001, 0.000003, 0.00001):
        assert disc(price) == reg(price) == derive_cost_tier(price)
    assert derive_cost_tier(0.0) == "standard"
    assert derive_cost_tier(0.0000004) == "budget"
    assert derive_cost_tier(0.00001) == "premium"
    # The discovery-service alias IS the shared function, not a copy.
    assert disc is derive_cost_tier
