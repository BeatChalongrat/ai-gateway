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


# ---------------------------------------------------------------------------
# Key fallback: a catalog read needs ONE key that works, not a fair share
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_discovery_tries_every_key_until_one_is_accepted(fresh_db_session):
    """The live bug: green key, saved endpoint, empty catalog.

    ``sranai`` held two active keys — one left over from a previous
    endpoint, one for the box actually configured. Discovery drew a
    single key by round-robin, drew the stale one, the box answered 401,
    and the adapter's swallow-and-return-[] contract turned that into a
    silent "0 models". The draw made it a coin flip, so a retry could
    "fix" it and hide the cause again.
    """
    _seed_endpoint("http://box:8000/v1")

    seen: list[str | None] = []

    def _respond(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization")
        seen.append(auth)
        if auth == "Bearer good-key":
            return httpx.Response(200, json={"data": [{"id": "sran-chat-7b"}]})
        return httpx.Response(401, json={"error": {"message": "no"}})

    respx.get("http://box:8000/v1/models").mock(side_effect=_respond)

    svc = ModelDiscoveryService()
    models, reason = await svc._discover_with_key_fallback(
        "sranai", discover_sranai, ["stale-key", "good-key"]
    )

    assert [m.model for m in models] == ["sran-chat-7b"]
    assert reason is None
    # Both keys were actually tried, in order — the stale one first.
    assert seen == ["Bearer stale-key", "Bearer good-key"]


@pytest.mark.asyncio
@respx.mock
async def test_healthy_provider_still_makes_one_request(fresh_db_session):
    """The fallback is paid only on failure — no extra load on a good box."""
    _seed_endpoint("http://box:8000/v1")
    route = respx.get("http://box:8000/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "sran-chat-7b"}]})
    )

    svc = ModelDiscoveryService()
    models, reason = await svc._discover_with_key_fallback(
        "sranai", discover_sranai, ["good-key", "second-key", "third-key"]
    )

    assert [m.model for m in models] == ["sran-chat-7b"]
    assert reason is None
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_every_key_rejected_reports_the_reason(fresh_db_session):
    """No key works → the operator gets told WHY, not just "0 models"."""
    _seed_endpoint("http://box:8000/v1")
    respx.get("http://box:8000/v1/models").mock(
        return_value=httpx.Response(401, json={"error": {"message": "no"}})
    )

    svc = ModelDiscoveryService()
    models, reason = await svc._discover_with_key_fallback(
        "sranai", discover_sranai, ["stale-key", "also-stale"]
    )

    assert models == []
    assert reason is not None
    assert "401" in reason


@pytest.mark.asyncio
async def test_reason_from_an_earlier_attempt_is_never_reused(fresh_db_session):
    """A stale reason must not be attributed to a later, different run."""
    from app.services.discovery import provider_discovery as pd

    pd.record_discovery_failure("sranai", "reason from a previous run")

    async def _adapter(api_key):
        return [DiscoveredModel(provider="sranai", model="sran-chat-7b")]

    svc = ModelDiscoveryService()
    models, reason = await svc._discover_with_key_fallback(
        "sranai", _adapter, ["good-key"]
    )
    assert [m.model for m in models] == ["sran-chat-7b"]
    assert reason is None
    assert pd.pop_discovery_failure("sranai") is None


@pytest.mark.asyncio
@respx.mock
async def test_no_endpoint_reason_survives_to_the_sync_result(fresh_db_session, monkeypatch):
    """``SyncResult`` carries the per-provider reason the adapters swallow."""
    from app.services.discovery.pricing_fetcher import PricingFetcher
    from app.services.provider_key_service import ProviderKeyService

    async def _stub_fetch(self):
        return {}
    monkeypatch.setattr(PricingFetcher, "fetch", _stub_fetch)
    monkeypatch.setattr(
        ProviderKeyService,
        "get_all_active_keys",
        lambda self, session, provider: ["some-key"],
    )
    _seed_endpoint(None)

    result = await ModelDiscoveryService().sync_provider("sranai", fresh_db_session)

    assert result.models_found == 0
    assert result.provider_errors["sranai"] == "endpoint not configured"
    # ``errors`` keeps its old meaning — orchestrator-level failures only.
    assert result.errors == []


@pytest.mark.asyncio
@respx.mock
async def test_sync_result_reports_models_found(fresh_db_session, monkeypatch):
    from app.services.discovery.pricing_fetcher import PricingFetcher
    from app.services.provider_key_service import ProviderKeyService

    async def _stub_fetch(self):
        return {}
    monkeypatch.setattr(PricingFetcher, "fetch", _stub_fetch)
    monkeypatch.setattr(
        ProviderKeyService,
        "get_all_active_keys",
        lambda self, session, provider: ["some-key"],
    )
    _seed_endpoint("http://box:8000/v1")
    respx.get("http://box:8000/v1/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "sran-chat-7b"}, {"id": "sran-chat-70b"}]}
        )
    )

    result = await ModelDiscoveryService().sync_provider("sranai", fresh_db_session)

    assert result.models_found == 2
    assert result.inserted == 2
    assert result.provider_errors == {}
    assert result.to_dict()["models_found"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_a_200_with_nothing_usable_is_its_own_diagnosis(fresh_db_session):
    """A box that answers but lists only non-chat models is not a 401."""
    _seed_endpoint("http://box:8000/v1")
    respx.get("http://box:8000/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "text-embedding-sran"}]})
    )

    svc = ModelDiscoveryService()
    models, reason = await svc._discover_with_key_fallback(
        "sranai", discover_sranai, ["good-key"]
    )
    assert models == []
    assert reason == "the endpoint listed no chat models"


@pytest.mark.asyncio
@respx.mock
async def test_a_dead_box_is_not_retried_once_per_key(fresh_db_session):
    """Only an auth rejection is worth trying the next key for.

    A connection failure said nothing about the credential, so walking a
    three-key pool against a dead endpoint would just triple the wait
    behind the 15s-per-attempt timeout.
    """
    _seed_endpoint("http://box:8000/v1")
    route = respx.get("http://box:8000/v1/models").mock(
        side_effect=httpx.ConnectError("down")
    )

    svc = ModelDiscoveryService()
    models, reason = await svc._discover_with_key_fallback(
        "sranai", discover_sranai, ["k1", "k2", "k3"]
    )

    assert models == []
    assert reason == "could not connect to the endpoint"
    assert route.call_count == 1
