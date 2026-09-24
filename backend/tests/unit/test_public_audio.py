"""Lightweight tests for the public direct-dispatch audio endpoint (2A.5).

Exercises TTS mode (text → speech). Music mode shares the same flow.
"""

import contextlib
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.main import app as main_app
from app.models.model_config import ModelConfigORM
from app.models.portal_user import PortalUserORM


@contextlib.contextmanager
def _auth(portal_user_id, tenant_id="test-tenant"):
    # FIX133 — every block is its own API key. The media limiter keeps one
    # sliding window per key for the whole pytest process (fakeredis), so a
    # shared "test-key-id" made whichever test ran after the window was spent
    # get 429 instead of its expected status (the FIX55 known-failing family).
    key_id = f"test-key-{uuid.uuid4().hex[:8]}"
    prev = main_app.dependency_overrides.get(get_resolved_api_key)
    main_app.dependency_overrides[get_resolved_api_key] = lambda: ResolvedApiKey(
        key_id=key_id,
        tenant_id=tenant_id,
        project_id="test-project",
        key_prefix="gw_test_",
        role="admin",
        portal_user_id=portal_user_id,
    )
    try:
        yield
    finally:
        if prev is not None:
            main_app.dependency_overrides[get_resolved_api_key] = prev


async def _ensure_model(async_db, model, **attrs):
    existing = (
        await async_db.execute(
            select(ModelConfigORM).where(
                ModelConfigORM.provider == "mock", ModelConfigORM.model == model
            )
        )
    ).scalars().first()
    if existing is None:
        async_db.add(ModelConfigORM(provider="mock", model=model, **attrs))
        await async_db.flush()


async def _seed_user(async_db, balance=5_000_000):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(
        PortalUserORM(
            id=uid, email=f"{uid}@e.com", password_hash="x",
            credit_balance=balance,
            # NOT NULL since SSO Phase 1 A1 — conftest integration seed.
            tenant_id="test-tenant",
        )
    )
    await async_db.flush()
    return uid


def _body(model="mock-tts-1", **kw):
    b = {"prompt": "hello world from the gateway", "model": model, "mode": "tts"}
    b.update(kw)
    return b


@pytest.mark.asyncio
async def test_unauthenticated_401(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    r = TestClient(main_app).post("/v1/audio/generations", json=_body())
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_model_missing_404(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    uid = await _seed_user(async_db)
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post(
            "/v1/audio/generations", json=_body(model="does-not-exist")
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_non_tts_model_400(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    uid = await _seed_user(async_db)
    await _ensure_model(async_db, "mock-notts", supports_tts=False)
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post(
            "/v1/audio/generations", json=_body(model="mock-notts")
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_valid_tts_submit_200(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    uid = await _seed_user(async_db)
    await _ensure_model(
        async_db, "mock-tts-1", supports_tts=True, cost_per_character=Decimal("0.0001")
    )
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post("/v1/audio/generations", json=_body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "queued"
    assert "estimated_cost_usd" in data
    assert data["poll_url"].startswith("/v1/jobs/")


# --- Sprint 4: music mode + mode-dispatch validation -----------------------


@pytest.mark.asyncio
async def test_valid_music_submit_200(async_db, monkeypatch):
    # mode=music routes through the supports_audio_gen capability + the
    # cost_per_generation cost path; the route returns a queued job (the
    # async adapter dispatch happens in the job loop).
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    uid = await _seed_user(async_db)
    await _ensure_model(
        async_db, "mock-music-1", supports_audio_gen=True,
        cost_per_generation=Decimal("0.002"),
    )
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post(
            "/v1/audio/generations",
            json=_body(model="mock-music-1", mode="music", duration_sec=8),
        )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "queued"
    assert data["poll_url"].startswith("/v1/jobs/")


@pytest.mark.asyncio
async def test_music_mode_on_tts_only_model_400(async_db, monkeypatch):
    # mode=music against a model that only supports_tts → capability 400.
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    uid = await _seed_user(async_db)
    await _ensure_model(
        async_db, "mock-ttsonly", supports_tts=True, supports_audio_gen=False,
        cost_per_character=Decimal("0.0001"),
    )
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post(
            "/v1/audio/generations",
            json=_body(model="mock-ttsonly", mode="music"),
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_invalid_mode_400(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    uid = await _seed_user(async_db)
    await _ensure_model(async_db, "mock-tts-1", supports_tts=True,
                        cost_per_character=Decimal("0.0001"))
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post(
            "/v1/audio/generations", json=_body(model="mock-tts-1", mode="bogus")
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_rate_limit_window_belongs_to_the_auth_block_not_the_process(async_db, monkeypatch):
    """FIX133 — the per-key media window (one Redis ZSET per key, alive for the
    whole pytest process) is never shared between tests: each ``_auth`` block
    is a fresh key. With the old shared "test-key-id" the third request here
    answered 429, and in the suite whichever test ran after the window was
    spent failed (known_failing.txt, the FIX55 family)."""
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    monkeypatch.setattr(settings, "media_rate_limit_audio_per_hour", 1)
    uid = await _seed_user(async_db)
    await _ensure_model(
        async_db, "mock-tts-1", supports_tts=True, cost_per_character=Decimal("0.0001")
    )
    await async_db.commit()
    with _auth(uid):
        assert TestClient(main_app).post("/v1/audio/generations", json=_body()).status_code == 200
        assert TestClient(main_app).post("/v1/audio/generations", json=_body()).status_code == 429
    with _auth(uid):  # a new block: a new key, an unspent window
        assert TestClient(main_app).post("/v1/audio/generations", json=_body()).status_code == 200
