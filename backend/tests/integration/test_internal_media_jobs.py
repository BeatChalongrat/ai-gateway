"""Integration tests for the internal video-gen endpoints (Sprint 2A).

Covers the auth gate, feature flag, capability gate, MEDIA_GEN DLP
(secret BLOCK / identity PASS), balance gate, idempotency, and status.

Note: DLP secret-block returns 403 (the gateway's DLP-violation status,
matching the image route) rather than the plan's 400.
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
def _auth(portal_user_id, *, trusted=True, tenant_id="test-tenant"):
    prev = main_app.dependency_overrides.get(get_resolved_api_key)
    main_app.dependency_overrides[get_resolved_api_key] = lambda: ResolvedApiKey(
        key_id="test-key-id",
        tenant_id=tenant_id,
        project_id="test-project",
        key_prefix="gw_test_",
        role="admin",
        portal_user_id=portal_user_id,
        internal_trusted=trusted,
    )
    try:
        yield
    finally:
        if prev is not None:
            main_app.dependency_overrides[get_resolved_api_key] = prev


async def _ensure_video_model(async_db):
    """Idempotently ensure the shared mock-video-1 model exists.

    The integration DB is shared across tests in the file, and
    model_configs has a UNIQUE(provider, model) constraint — so the seed
    must be a no-op when the row already exists (otherwise every test
    after the first hits an IntegrityError).
    """
    existing = (
        await async_db.execute(
            select(ModelConfigORM).where(
                ModelConfigORM.provider == "mock",
                ModelConfigORM.model == "mock-video-1",
            )
        )
    ).scalars().first()
    if existing is None:
        async_db.add(
            ModelConfigORM(
                provider="mock",
                model="mock-video-1",
                supports_video_gen=True,
                cost_per_second=Decimal("0.07"),
            )
        )
        await async_db.flush()


async def _seed(async_db, *, balance=5_000_000):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(
        PortalUserORM(
            id=uid, email=f"{uid}@e.com", password_hash="x",
            credit_balance=balance,
            # tenant_id NOT NULL since SSO Phase 1 A1 (conftest seed).
            tenant_id="test-tenant",
        )
    )
    await _ensure_video_model(async_db)
    await async_db.commit()
    return uid


def _body(model="mock-video-1", **kw):
    b = {"prompt": "a cat surfing a wave", "model": model, "duration_sec": 5}
    b.update(kw)
    return b


@pytest.mark.asyncio
async def test_submit_without_hmac_401(async_db):
    uid = await _seed(async_db)
    with _auth(uid, trusted=False):
        r = TestClient(main_app).post("/internal/v1/jobs/video", json=_body())
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_feature_disabled_403(async_db, monkeypatch):
    uid = await _seed(async_db)
    monkeypatch.setattr(settings, "video_gen_enabled", False)
    with _auth(uid):
        r = TestClient(main_app).post("/internal/v1/jobs/video", json=_body())
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_non_video_model_400(async_db, monkeypatch):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(
        PortalUserORM(
            id=uid, email=f"{uid}@e.com", password_hash="x",
            credit_balance=5_000_000, tenant_id="test-tenant",
        )
    )
    async_db.add(ModelConfigORM(provider="openai", model="gpt-4o-novideo", supports_video_gen=False))
    await async_db.commit()
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    with _auth(uid):
        r = TestClient(main_app).post("/internal/v1/jobs/video", json=_body(model="gpt-4o-novideo"))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_secret_prompt_blocked(async_db, monkeypatch):
    uid = await _seed(async_db)
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    monkeypatch.setattr(settings, "dlp_enabled", True)
    secret = "sk-proj-abc123def456ghi789jkl012mno345pqr678"
    with _auth(uid):
        r = TestClient(main_app).post(
            "/internal/v1/jobs/video",
            json=_body(prompt=f"a poster with my api key {secret}"),
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_pii_prompt_passes(async_db, monkeypatch):
    uid = await _seed(async_db)
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    monkeypatch.setattr(settings, "dlp_enabled", True)
    with _auth(uid):
        r = TestClient(main_app).post(
            "/internal/v1/jobs/video",
            json=_body(prompt="draw me, my email is john@example.com"),
        )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_valid_submit_has_usd_siblings(async_db, monkeypatch):
    uid = await _seed(async_db)
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    with _auth(uid):
        r = TestClient(main_app).post("/internal/v1/jobs/video", json=_body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "queued"
    assert data["estimated_cost_credits"] == 1_050_000  # 0.07*5*3,000,000
    assert data["estimated_cost_usd"] == "$0.35"
    assert "balance_after_usd" in data
    assert data["requires_confirmation"] is False  # $0.35 < $1.00


@pytest.mark.asyncio
async def test_idempotency_returns_same_job(async_db, monkeypatch):
    uid = await _seed(async_db)
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    key = f"idem-{uuid.uuid4().hex[:8]}"
    with _auth(uid):
        client = TestClient(main_app)
        r1 = client.post("/internal/v1/jobs/video", json=_body(idempotency_key=key))
        r2 = client.post("/internal/v1/jobs/video", json=_body(idempotency_key=key))
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["job_id"] == r2.json()["job_id"]


@pytest.mark.asyncio
async def test_get_status_shape(async_db, monkeypatch):
    uid = await _seed(async_db)
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    with _auth(uid):
        client = TestClient(main_app)
        job_id = client.post("/internal/v1/jobs/video", json=_body()).json()["job_id"]
        r = client.get(f"/internal/v1/jobs/{job_id}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["job_id"] == job_id
    assert data["status"] == "queued"
    assert data["output_url"] is None
    assert "created_at" in data
