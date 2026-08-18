"""Tests for the DB-backed model registry + discovery pipeline.

Covers:
- PricingFetcher cache + fail-open
- per-provider discovery filtering + pricing extraction
- sync_all upsert rules (manual override, soft-deactivate)
- ModelRegistry.reload reading from DB
- admin sync endpoint
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.domain.routing import ModelCandidate
from app.main import app as main_app
from app.models.model_config import ModelConfigORM
from app.services.discovery.pricing_fetcher import (
    LITELLM_PRICING_URL,
    PricingFetcher,
)
from app.services.discovery.provider_discovery import (
    discover_openai,
    discover_openrouter,
)
from app.services.discovery.discovery_service import ModelDiscoveryService
from app.services.providers.model_registry import ModelRegistry


# ---------------------------------------------------------------------------
# Fresh-per-test fixtures (the suite-wide integration engine is too noisy
# for these counts-and-flags assertions).
# ---------------------------------------------------------------------------

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
def _preserve_registry_singleton():
    """Restore the module-level registry after each test.

    ``ModelDiscoveryService.sync_all`` reloads
    ``app.services.providers.model_registry._registry`` against
    whatever DB session the caller passed in. Tests in this file use
    a fresh per-test SQLite DB with 1-2 rows, so without this fixture
    the singleton would end the test holding only those rows —
    breaking the *next* test (running in the same process) that
    expects the seed list.

    We snapshot ``_candidates`` and ``_model_map`` before the test
    runs and put them back afterwards. ``_lock`` is not snapshotted
    (an ``asyncio.Lock`` is bound to its event loop; reusing one
    across loops would crash).
    """
    from app.services.providers import model_registry as registry_mod
    saved_candidates = list(registry_mod._registry._candidates)
    saved_model_map = dict(registry_mod._registry._model_map)
    try:
        yield
    finally:
        registry_mod._registry._candidates = saved_candidates
        registry_mod._registry._model_map = saved_model_map


def _make_row(
    *,
    provider: str,
    model: str,
    is_manual_override: bool = False,
    is_active: bool = True,
    cost_in: float = 0.0,
    cost_out: float = 0.0,
    source: str = "auto",
    supports_tools: bool = False,
    supports_vision: bool = False,
    max_context_tokens: int = 128000,
) -> ModelConfigORM:
    return ModelConfigORM(
        provider=provider,
        model=model,
        supports_streaming=True,
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        max_context_tokens=max_context_tokens,
        cost_tier="standard",
        latency_tier="standard",
        cost_per_input_token=cost_in,
        cost_per_output_token=cost_out,
        source=source,
        is_manual_override=is_manual_override,
        is_active=is_active,
        last_synced_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. PricingFetcher
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_pricing_fetcher_caches_result():
    payload = {"gpt-4o": {"input_cost_per_token": 0.0000025}}
    route = respx.get(LITELLM_PRICING_URL).mock(
        return_value=httpx.Response(200, json=payload)
    )
    fetcher = PricingFetcher()
    first = await fetcher.fetch()
    second = await fetcher.fetch()
    assert first == second
    assert "gpt-4o" in first
    assert route.call_count == 1, "Cached fetch must not hit the network twice"


def test_manual_pricing_fills_zero_models():
    """``MANUAL_PRICING`` covers every model that shipped at 0.0/0.0
    in seed migration 037. ``get_model_pricing`` consults it as the
    third fallback (after the litellm exact + provider/model lookups
    both miss).

    Verified models — one representative pick from each provider
    that had zero-cost seed rows. ``self._cache`` is empty so the
    litellm path returns None immediately and the MANUAL_PRICING
    fallback is the only thing under test.
    """
    fetcher = PricingFetcher()
    # No fetch() called — _cache stays empty.
    grok = fetcher.get_model_pricing("grok-2-latest", "xai")
    assert grok is not None
    assert grok["input_cost_per_token"] == pytest.approx(0.000002)
    assert grok["output_cost_per_token"] == pytest.approx(0.000010)

    abab = fetcher.get_model_pricing("abab5.5-chat", "minimax")
    assert abab is not None
    assert abab["input_cost_per_token"] == pytest.approx(0.00000015)

    # Provider-qualified key (OpenRouter alias path).
    or_sonnet = fetcher.get_model_pricing(
        "anthropic/claude-3.5-sonnet", "openrouter"
    )
    assert or_sonnet is not None
    assert or_sonnet["input_cost_per_token"] == pytest.approx(0.000003)


@pytest.mark.asyncio
@respx.mock
async def test_pricing_fetcher_returns_empty_on_network_error():
    respx.get(LITELLM_PRICING_URL).mock(
        side_effect=httpx.ConnectError("simulated DNS failure")
    )
    fetcher = PricingFetcher()
    result = await fetcher.fetch()
    assert result == {}, "fetch must fail open with empty dict, not raise"


# ---------------------------------------------------------------------------
# 2. Provider discovery adapters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_openai_discovery_filters_non_chat_models():
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-4o"},
                    {"id": "text-embedding-3-small"},
                    {"id": "whisper-1"},
                    {"id": "dall-e-3"},
                ]
            },
        )
    )
    discovered = await discover_openai(api_key="sk-test")
    model_ids = {m.model for m in discovered}
    assert "gpt-4o" in model_ids
    assert "text-embedding-3-small" not in model_ids
    assert "whisper-1" not in model_ids
    assert "dall-e-3" not in model_ids


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_discovery_uses_response_pricing():
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "anthropic/claude-3.5-sonnet",
                        "context_length": 200000,
                        "pricing": {
                            "prompt": "0.000003",
                            "completion": "0.000015",
                        },
                    }
                ]
            },
        )
    )
    discovered = await discover_openrouter(api_key=None)
    assert len(discovered) == 1
    model = discovered[0]
    assert model.cost_per_input_token == pytest.approx(0.000003)
    assert model.cost_per_output_token == pytest.approx(0.000015)
    assert model.cost_from_provider is True, (
        "OpenRouter populates pricing from its own response; the "
        "orchestrator must see cost_from_provider=True so litellm does "
        "not overwrite it"
    )
    assert model.max_context_tokens == 200000


# ---------------------------------------------------------------------------
# 3. Sync orchestrator behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_skips_manual_override_rows(fresh_db_session, monkeypatch):
    # Seed a row marked as a manual override with very specific values
    # the discovery adapter would otherwise stomp.
    fresh_db_session.add(
        _make_row(
            provider="openai",
            model="gpt-4o",
            is_manual_override=True,
            cost_in=0.99,
            cost_out=1.99,
            source="manual",
        )
    )
    fresh_db_session.commit()

    # Mock discovery: openai returns gpt-4o with stock pricing; all
    # other adapters return [] so no soft-deactivations fire.
    from app.services.discovery import provider_discovery

    async def _stub_openai(_api_key):
        return [
            provider_discovery.DiscoveredModel(
                provider="openai",
                model="gpt-4o",
                cost_per_input_token=0.0000025,
                cost_per_output_token=0.000010,
            )
        ]

    async def _stub_empty(_api_key):
        return []

    patched = dict(provider_discovery.PROVIDER_DISCOVERY)
    for key in patched:
        patched[key] = _stub_empty
    patched["openai"] = _stub_openai
    monkeypatch.setattr(provider_discovery, "PROVIDER_DISCOVERY", patched)
    # Also patch the symbol the discovery_service already imported.
    from app.services.discovery import discovery_service as ds_mod
    monkeypatch.setattr(ds_mod, "PROVIDER_DISCOVERY", patched)

    # Stub pricing fetch to no-op (don't reach the network).
    async def _stub_fetch(self):
        return {}
    monkeypatch.setattr(PricingFetcher, "fetch", _stub_fetch)

    # Stub provider key lookup (the conftest auto-mock returns
    # "test-stub-key" anyway — explicit here for clarity).
    from app.services.provider_key_service import ProviderKeyService
    monkeypatch.setattr(
        ProviderKeyService,
        "get_api_key",
        lambda self, session, provider: "test-stub-key",
    )

    svc = ModelDiscoveryService()
    result = await svc.sync_all(fresh_db_session)
    assert result.skipped_override >= 1
    fresh_db_session.expire_all()
    row = (
        fresh_db_session.query(ModelConfigORM)
        .filter_by(provider="openai", model="gpt-4o")
        .one()
    )
    assert row.cost_per_input_token == pytest.approx(0.99)
    assert row.cost_per_output_token == pytest.approx(1.99)
    assert row.is_manual_override is True
    assert row.source == "manual"


def _patch_discovery(monkeypatch, openai_models):
    """Point every provider adapter at an empty stub except openai."""
    from app.services.discovery import provider_discovery
    from app.services.discovery import discovery_service as ds_mod

    async def _stub_openai(_api_key):
        return list(openai_models)

    async def _stub_empty(_api_key):
        return []

    patched = {k: _stub_empty for k in provider_discovery.PROVIDER_DISCOVERY}
    patched["openai"] = _stub_openai
    monkeypatch.setattr(provider_discovery, "PROVIDER_DISCOVERY", patched)
    monkeypatch.setattr(ds_mod, "PROVIDER_DISCOVERY", patched)

    async def _stub_fetch(self):
        return {}
    monkeypatch.setattr(PricingFetcher, "fetch", _stub_fetch)


@pytest.mark.asyncio
async def test_sync_does_not_clobber_corrected_rows_with_unknowns(
    fresh_db_session, monkeypatch
):
    """A NON-override row corrected by migration/admin survives a sync
    whose discovered entry carries only "unknown" markers: price 0.0
    (litellm miss), tools/vision False (keyword-inference miss — e.g.
    gpt-5 matches no keyword list), context == the provider-adapter
    sentinel (API didn't say). This is the gap ``is_manual_override``
    doesn't cover: migrations 068-075 corrected rows without setting
    the flag."""
    from app.services.discovery import provider_discovery

    fresh_db_session.add(
        _make_row(
            provider="openai",
            model="gpt-5-test",
            is_manual_override=False,
            cost_in=0.00000125,
            cost_out=0.00001,
            supports_tools=True,
            supports_vision=True,
            max_context_tokens=400000,
        )
    )
    fresh_db_session.commit()

    _patch_discovery(
        monkeypatch,
        [
            provider_discovery.DiscoveredModel(
                provider="openai",
                model="gpt-5-test",
                # All defaults: cost 0.0, tools/vision False, context =
                # DEFAULT_MAX_CONTEXT_TOKENS — every field "unknown".
            )
        ],
    )

    svc = ModelDiscoveryService()
    result = await svc.sync_all(fresh_db_session)
    assert result.updated >= 1

    fresh_db_session.expire_all()
    row = (
        fresh_db_session.query(ModelConfigORM)
        .filter_by(provider="openai", model="gpt-5-test")
        .one()
    )
    assert row.cost_per_input_token == pytest.approx(0.00000125)
    assert row.cost_per_output_token == pytest.approx(0.00001)
    assert row.supports_tools is True
    assert row.supports_vision is True
    assert row.max_context_tokens == 400000


@pytest.mark.asyncio
async def test_sync_still_updates_rows_with_known_values(
    fresh_db_session, monkeypatch
):
    """The guard must not freeze rows: genuine upstream data (nonzero
    prices, non-sentinel context, promoted flags) still flows."""
    from app.services.discovery import provider_discovery

    fresh_db_session.add(
        _make_row(
            provider="openai",
            model="gpt-4o-test",
            cost_in=0.001,
            cost_out=0.002,
            supports_tools=False,
            max_context_tokens=16384,
        )
    )
    fresh_db_session.commit()

    _patch_discovery(
        monkeypatch,
        [
            provider_discovery.DiscoveredModel(
                provider="openai",
                model="gpt-4o-test",
                cost_per_input_token=0.0000025,
                cost_per_output_token=0.00001,
                supports_tools=True,
                max_context_tokens=200000,
            )
        ],
    )

    svc = ModelDiscoveryService()
    result = await svc.sync_all(fresh_db_session)
    assert result.updated >= 1

    fresh_db_session.expire_all()
    row = (
        fresh_db_session.query(ModelConfigORM)
        .filter_by(provider="openai", model="gpt-4o-test")
        .one()
    )
    assert row.cost_per_input_token == pytest.approx(0.0000025)
    assert row.cost_per_output_token == pytest.approx(0.00001)
    assert row.supports_tools is True
    assert row.max_context_tokens == 200000


@pytest.mark.asyncio
async def test_sync_deactivates_missing_models(fresh_db_session, monkeypatch):
    fresh_db_session.add(
        _make_row(provider="openai", model="old-model-xyz", is_active=True)
    )
    fresh_db_session.commit()

    from app.services.discovery import provider_discovery
    from app.services.discovery import discovery_service as ds_mod

    async def _stub_empty(_api_key):
        return []

    # openai must report a NON-EMPTY list that omits ``old-model-xyz`` —
    # the soft-deactivate guard only fires for providers that successfully
    # returned models this run (a provider that returns [] is treated as a
    # transient outage and its rows are left intact). With the old all-empty
    # stub, openai was never "synced", so the vanished model was never
    # deactivated and this test could not pass.
    async def _stub_openai(_api_key):
        return [
            provider_discovery.DiscoveredModel(
                provider="openai",
                model="gpt-4o",
                cost_per_input_token=0.0000025,
                cost_per_output_token=0.000010,
            )
        ]

    patched = {key: _stub_empty for key in provider_discovery.PROVIDER_DISCOVERY}
    patched["openai"] = _stub_openai
    monkeypatch.setattr(provider_discovery, "PROVIDER_DISCOVERY", patched)
    monkeypatch.setattr(ds_mod, "PROVIDER_DISCOVERY", patched)

    async def _stub_fetch(self):
        return {}
    monkeypatch.setattr(PricingFetcher, "fetch", _stub_fetch)

    svc = ModelDiscoveryService()
    result = await svc.sync_all(fresh_db_session)
    assert result.deactivated >= 1

    fresh_db_session.expire_all()
    row = (
        fresh_db_session.query(ModelConfigORM)
        .filter_by(provider="openai", model="old-model-xyz")
        .one()
    )
    assert row.is_active is False, "Models absent from discovery must be soft-deactivated"


# ---------------------------------------------------------------------------
# 4. Registry reload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_reload_uses_db_not_hardcode(fresh_db_session):
    fresh_db_session.add_all([
        _make_row(provider="openai", model="db-only-model-1"),
        _make_row(provider="anthropic", model="db-only-model-2"),
    ])
    fresh_db_session.commit()

    registry = ModelRegistry()
    # No seed pre-population — this instance is fresh.
    await registry.reload(fresh_db_session)
    candidates = registry.get_all_candidates()
    assert len(candidates) == 2, (
        f"Expected exactly the 2 DB rows, got {len(candidates)}: "
        f"{[c.model for c in candidates]}"
    )
    models = {c.model for c in candidates}
    assert models == {"db-only-model-1", "db-only-model-2"}
    # Spot-check field mapping.
    assert isinstance(candidates[0], ModelCandidate)


# ---------------------------------------------------------------------------
# 5. Admin endpoint
# ---------------------------------------------------------------------------

def test_admin_sync_endpoint_returns_sync_result(monkeypatch):
    """POST /admin/models/discovery/sync runs the service and returns a SyncResult dict."""
    from app.api.routes import admin_model_discovery as admin_mod
    from app.services.discovery.discovery_service import SyncResult

    captured: dict = {}

    async def _stub_sync_all(self, db):
        captured["called"] = True
        return SyncResult(
            provider=None,
            inserted=3,
            updated=2,
            skipped_override=1,
            deactivated=0,
            duration_seconds=0.42,
        )

    monkeypatch.setattr(
        admin_mod._discovery_service.__class__,
        "sync_all",
        _stub_sync_all,
    )

    client = TestClient(main_app)
    resp = client.post(
        "/admin/models/discovery/sync",
        json={},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for field in (
        "provider", "inserted", "updated", "skipped_override",
        "deactivated", "errors", "duration_seconds", "synced_at",
    ):
        assert field in body, f"missing field {field} in response"
    assert body["inserted"] == 3
    assert body["updated"] == 2
    assert body["skipped_override"] == 1
    assert captured.get("called") is True
