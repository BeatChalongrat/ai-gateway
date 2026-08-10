"""Lightweight tests for the public direct-dispatch video endpoint (2A.5)."""

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
    prev = main_app.dependency_overrides.get(get_resolved_api_key)
    main_app.dependency_overrides[get_resolved_api_key] = lambda: ResolvedApiKey(
        key_id="test-key-id",
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


def _body(model="mock-video-1", **kw):
    b = {"prompt": "a cat surfing a wave", "model": model, "duration_sec": 5}
    b.update(kw)
    return b


@pytest.mark.asyncio
async def test_unauthenticated_401(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    r = TestClient(main_app).post("/v1/videos/generations", json=_body())
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_model_missing_404(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    uid = await _seed_user(async_db)
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post(
            "/v1/videos/generations", json=_body(model="does-not-exist")
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_chat_only_model_400(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    uid = await _seed_user(async_db)
    await _ensure_model(async_db, "mock-chat-novideo", supports_video_gen=False)
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post(
            "/v1/videos/generations", json=_body(model="mock-chat-novideo")
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_valid_submit_200(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    uid = await _seed_user(async_db)
    await _ensure_model(
        async_db, "mock-video-1", supports_video_gen=True, cost_per_second=Decimal("0.07")
    )
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post("/v1/videos/generations", json=_body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "queued"
    assert data["estimated_cost_usd"] == "$0.35"
    assert data["poll_url"].startswith("/v1/jobs/")
    assert "balance_after_usd" in data
    assert data["requires_confirmation"] is False
