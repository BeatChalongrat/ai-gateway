"""Auth + rate-limit coverage for the public media endpoints (Sprint 5).

Reconciliation note: API-key auth and cross-tenant isolation already shipped
in Sprint 2A.5 (the endpoints depend on ``get_resolved_api_key`` and 404 on a
tenant mismatch). This sprint adds only the per-key RATE LIMITS, so the tests
here assert the limit (429) plus re-confirm the pre-existing auth/isolation
guarantees still hold.
"""

import contextlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.main import app as main_app
from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.services.rate_limit_service import RateLimitService


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
        else:
            main_app.dependency_overrides.pop(get_resolved_api_key, None)


async def _seed_user(async_db, balance=5_000_000):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(
        PortalUserORM(
            id=uid, email=f"{uid}@e.com", password_hash="x",
            credit_balance=balance,
            # tenant_id NOT NULL since SSO Phase 1 A1 (conftest seed).
            tenant_id="test-tenant",
        )
    )
    await async_db.flush()
    return uid


def _limited(*_a, **_k):
    # Simulate a tripped sliding window (the shape check_window_async returns).
    async def _fake(self, scope_key, limit, window_sec):
        return {"allowed": False, "limit": limit, "remaining": 0, "reset_at": 9999999999}
    return _fake


@pytest.mark.asyncio
async def test_no_auth_401(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    r = TestClient(main_app).post(
        "/v1/videos/generations", json={"prompt": "x", "model": "m"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_video_rate_limit_429(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    monkeypatch.setattr(RateLimitService, "check_window_async", _limited())
    uid = await _seed_user(async_db)
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post(
            "/v1/videos/generations", json={"prompt": "a cat", "model": "m"}
        )
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_audio_rate_limit_429(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    monkeypatch.setattr(RateLimitService, "check_window_async", _limited())
    uid = await _seed_user(async_db)
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).post(
            "/v1/audio/generations",
            json={"prompt": "hi", "model": "m", "mode": "tts"},
        )
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_jobs_poll_rate_limit_429(async_db, monkeypatch):
    monkeypatch.setattr(RateLimitService, "check_window_async", _limited())
    uid = await _seed_user(async_db)
    await async_db.commit()
    with _auth(uid):
        r = TestClient(main_app).get(f"/v1/jobs/{uuid.uuid4()}")
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_cross_tenant_job_404(async_db, monkeypatch):
    # A job owned by tenant A is invisible to tenant B's key (pre-existing
    # isolation; rate limiter passes through via fail-open in tests).
    uid = await _seed_user(async_db)
    job_id = str(uuid.uuid4())
    async_db.add(
        GenerationJobORM(
            id=job_id,
            tenant_id="tenant-A",
            user_id=uid,
            job_type="video",
            provider="mock",
            model_id="m",
            status="queued",
            input_prompt="x",
            input_metadata={},
            estimated_cost_credits=1,
            ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    await async_db.commit()
    with _auth(uid, tenant_id="tenant-B"):
        r = TestClient(main_app).get(f"/v1/jobs/{job_id}")
    assert r.status_code == 404
