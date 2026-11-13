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


@pytest.fixture(autouse=True)
def configured_db_video_key(monkeypatch):
    # Capability and the adapter now use the same DB-backed key source.
    from unittest.mock import AsyncMock
    monkeypatch.setattr("app.services.provider_key_service.ProviderKeyService.get_active_key_async", AsyncMock(return_value="test-token"))


@contextlib.contextmanager
def _auth(portal_user_id, tenant_id="test-tenant"):
    # FIX133 — every block is its own API key: the media limiter's window is
    # per key for the whole pytest process, see test_public_audio._auth.
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


@pytest.mark.asyncio
async def test_video_jobs_list_and_actions_are_owner_scoped(async_db):
    from datetime import datetime, timedelta, timezone
    from app.models.generation_job import GenerationJobORM
    owner = await _seed_user(async_db)
    stranger = await _seed_user(async_db)
    conversation = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    async_db.add(GenerationJobORM(
        id=job_id, tenant_id="test-tenant", user_id=owner, conversation_id=conversation,
        job_type="video", provider="mock", model_id="mock-video", status="pending_confirm",
        input_prompt="Test", input_metadata={}, estimated_cost_credits=1000,
        ttl_expires_at=datetime.now(timezone.utc)+timedelta(hours=1),
    ))
    await async_db.commit()
    client = TestClient(main_app)
    with _auth(stranger):
        assert client.get(f"/v1/jobs?conversation_id={conversation}").json()["jobs"] == []
        for method, path in [("get", ""), ("get", "/output"), ("post", "/cancel"), ("post", "/confirm")]:
            assert getattr(client, method)(f"/v1/jobs/{job_id}{path}").status_code == 404
    with _auth(owner):
        result = client.get(f"/v1/jobs?conversation_id={conversation}")
        assert result.status_code == 200
        assert [j["job_id"] for j in result.json()["jobs"]] == [job_id]
        assert result.json()["jobs"][0]["status"] == "pending_confirm"
        assert client.get(f"/v1/jobs?conversation_id={uuid.uuid4()}").json()["jobs"] == []
        assert client.get("/v1/jobs?conversation_id=not-a-uuid").status_code == 400
        assert client.post(f"/v1/jobs/{job_id}/cancel").json()["status"] == "cancelled"


def _body(model="mock-video-1", **kw):
    b = {"prompt": "a cat surfing a wave", "model": model, "duration_sec": 5}
    b.update(kw)
    return b


@pytest.mark.asyncio
async def test_unauthenticated_401(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    # FIX11 §1: video submit now gates on the full capability (gate AND a
    # configured key AND an owner-verified model), so set the other two signals.
    monkeypatch.setattr(settings, "replicate_api_token", "test-token")
    monkeypatch.setattr(settings, "video_model_verified", True)
    r = TestClient(main_app).post("/v1/videos/generations", json=_body())
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_model_missing_404(async_db, monkeypatch):
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    # FIX11 §1: video submit now gates on the full capability (gate AND a
    # configured key AND an owner-verified model), so set the other two signals.
    monkeypatch.setattr(settings, "replicate_api_token", "test-token")
    monkeypatch.setattr(settings, "video_model_verified", True)
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
    # FIX11 §1: video submit now gates on the full capability (gate AND a
    # configured key AND an owner-verified model), so set the other two signals.
    monkeypatch.setattr(settings, "replicate_api_token", "test-token")
    monkeypatch.setattr(settings, "video_model_verified", True)
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
    # FIX11 §1: video submit now gates on the full capability (gate AND a
    # configured key AND an owner-verified model), so set the other two signals.
    monkeypatch.setattr(settings, "replicate_api_token", "test-token")
    monkeypatch.setattr(settings, "video_model_verified", True)
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


@pytest.mark.asyncio
async def test_premium_video_waits_for_owner_confirmation(async_db, monkeypatch):
    from app.models.generation_job import GenerationJobORM
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    monkeypatch.setattr(settings, "video_model_verified", True)
    monkeypatch.setattr(settings, "video_gen_default_threshold_usd", .1)
    uid = await _seed_user(async_db)
    other = await _seed_user(async_db)
    name = "premium-" + uuid.uuid4().hex[:8]
    await _ensure_model(async_db, name, supports_video_gen=True, is_active=True, cost_per_second=Decimal("0.07"))
    await async_db.commit()
    body = _body(); body["model"] = name
    with _auth(uid):
        result = TestClient(main_app).post("/v1/videos/generations", json=body)
    assert result.status_code == 200, result.text
    job_id = result.json()["job_id"]
    assert result.json()["status"] == "pending_confirm"
    assert result.json()["requires_confirmation"] is True
    job = await async_db.get(GenerationJobORM, job_id)
    assert job.status == "pending_confirm" and job.external_job_id is None
    with _auth(other):
        denied = TestClient(main_app).post(f"/v1/jobs/{job_id}/confirm")
    assert denied.status_code == 404
    with _auth(uid):
        confirmed = TestClient(main_app).post(f"/v1/jobs/{job_id}/confirm")
        again = TestClient(main_app).post(f"/v1/jobs/{job_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == again.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_cancel_unconfirmed_video_does_not_refund_unreserved_budget(async_db, monkeypatch):
    from unittest.mock import AsyncMock
    refund = AsyncMock()
    monkeypatch.setattr("app.services.media_gen.spend_guard.release_session_budget", refund)
    monkeypatch.setattr(settings, "video_gen_enabled", True)
    monkeypatch.setattr(settings, "video_model_verified", True)
    monkeypatch.setattr(settings, "video_gen_default_threshold_usd", .1)
    uid = await _seed_user(async_db)
    name = "cancel-" + uuid.uuid4().hex[:8]
    await _ensure_model(async_db, name, supports_video_gen=True, is_active=True, cost_per_second=Decimal("0.07"))
    await async_db.commit()
    body = _body(); body["model"] = name
    with _auth(uid):
        result = TestClient(main_app).post("/v1/videos/generations", json=body)
        job_id = result.json()["job_id"]
        cancelled = TestClient(main_app).post(f"/v1/jobs/{job_id}/cancel")
        confirmed = TestClient(main_app).post(f"/v1/jobs/{job_id}/confirm")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == confirmed.json()["status"] == "cancelled"
    refund.assert_not_called()
