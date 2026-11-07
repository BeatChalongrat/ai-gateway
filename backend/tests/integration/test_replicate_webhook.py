"""Replicate webhook receiver tests (Sprint 2B).

Uses a MOCK-provider job (so fetch_result_bytes returns the bundled sample
MP4 — no Replicate, no respx) + real Redis (DB 15) for the spend counters,
with get_async_redis monkeypatched to that client so the webhook's
commit/release land where the test can assert them.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
import respx
from starlette.testclient import TestClient

from app.core.config import settings
from app.main import app as main_app
from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.services.media_gen import spend_guard


@pytest_asyncio.fixture
async def rr():
    r = aioredis.from_url("redis://localhost:6379/15", decode_responses=True)
    try:
        await r.ping()
    except Exception:
        pytest.skip("dev Redis not reachable on localhost:6379")
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


@pytest.fixture(autouse=True)
def _wire(monkeypatch, tmp_path, rr):
    monkeypatch.setattr(settings, "replicate_webhook_secret", "wh-secret")
    monkeypatch.setenv("MEDIA_GEN_OUTPUT_DIR", str(tmp_path))
    # The webhook runs in the TestClient's OWN event loop, so it can't share
    # the test-loop ``rr`` client. Hand it a FRESH client per call (bound to
    # whatever loop calls it) on the same DB 15 — the test's ``rr`` reads the
    # same data.
    monkeypatch.setattr(
        "app.services._redis_pool.get_async_redis",
        lambda: aioredis.from_url("redis://localhost:6379/15", decode_responses=True),
    )


def _tenant_key():
    return spend_guard.TENANT_KEY.format(tenant_id="test-tenant", date=spend_guard._today())


def _session_key():
    return spend_guard.SESSION_KEY.format(date=spend_guard._today())


async def _insert_job(
    async_db, *, ext, status="processing", estimate="0.35", provider="mock"
):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    # tenant_id NOT NULL since SSO Phase 1 A1 (conftest seed).
    async_db.add(
        PortalUserORM(
            id=uid, email=f"{uid}@e.com", password_hash="x",
            tenant_id="test-tenant",
        )
    )
    jid = str(uuid.uuid4())
    async_db.add(
        GenerationJobORM(
            id=jid,
            tenant_id="test-tenant",
            user_id=uid,
            job_type="video",
            provider=provider,  # "mock" → fetch_result_bytes returns the bundled sample.mp4
            model_id="kwaivgi/kling-v1.5-pro",
            status=status,
            external_job_id=ext,
            input_prompt="a cat",
            input_metadata={"estimate_usd": estimate},
            estimated_cost_credits=1_050_000,
            ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    await async_db.commit()
    return jid


def _post(payload, secret="wh-secret"):
    return TestClient(main_app).post(
        f"/webhooks/replicate?secret={secret}", json=payload
    )


@pytest.mark.asyncio
async def test_wrong_secret_401(async_db):
    await _insert_job(async_db, ext="pred_a")
    r = _post({"id": "pred_a", "status": "succeeded", "output": ["http://x/o.mp4"]}, secret="WRONG")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_succeeded_finalizes_and_commits(async_db, rr, tmp_path):
    jid = await _insert_job(async_db, ext="pred_ok")
    r = _post({"id": "pred_ok", "status": "succeeded", "output": ["http://x/o.mp4"]})
    assert r.status_code == 200, r.text

    job = await async_db.get(GenerationJobORM, jid)
    await async_db.refresh(job)
    assert job.status == "done"
    assert job.actual_cost_credits == 1_050_000
    assert (tmp_path / f"{jid}.mp4").exists()
    # L2 daily counter committed with the estimate.
    assert Decimal(str(await rr.get(_tenant_key()))) == Decimal("0.35")


@pytest.mark.asyncio
async def test_idempotent_second_webhook_no_double_commit(async_db, rr):
    await _insert_job(async_db, ext="pred_dup")
    _post({"id": "pred_dup", "status": "succeeded", "output": ["http://x/o.mp4"]})
    _post({"id": "pred_dup", "status": "succeeded", "output": ["http://x/o.mp4"]})
    # Counter committed exactly once, not 0.70.
    assert Decimal(str(await rr.get(_tenant_key()))) == Decimal("0.35")


@pytest.mark.asyncio
async def test_failed_refunds_session_budget(async_db, rr):
    # Pre-seed the session counter (as if the adapter had reserved), then the
    # failed webhook must refund it.
    await rr.set(_session_key(), "1.00")
    jid = await _insert_job(async_db, ext="pred_fail")
    r = _post({"id": "pred_fail", "status": "failed", "error": "boom"})
    assert r.status_code == 200
    job = await async_db.get(GenerationJobORM, jid)
    await async_db.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "failed"
    # 1.00 - 0.35 refund = 0.65
    assert Decimal(str(await rr.get(_session_key()))) == Decimal("0.65")


@pytest.mark.asyncio
async def test_unknown_prediction_is_noop_200(async_db):
    r = _post({"id": "pred_nonexistent", "status": "succeeded", "output": ["http://x/o.mp4"]})
    assert r.status_code == 200  # NOT 404 — avoids Replicate retry storms


@pytest.mark.asyncio
@respx.mock
async def test_ssrf_output_url_is_never_fetched(async_db):
    """Phase 12 A2 — a webhook body pointing ``output`` at an internal
    address must not trigger ANY server-side HTTP request. The guard in
    ``ReplicateAdapterBase.fetch_result_bytes`` raises before a client is
    built; the webhook logs the failure and leaves the job in-flight for
    the poll fallback."""
    jid = await _insert_job(async_db, ext="pred_ssrf", provider="replicate")
    spy = respx.route().mock(
        return_value=httpx.Response(200, content=b"should-never-be-fetched")
    )
    r = _post(
        {
            "id": "pred_ssrf",
            "status": "succeeded",
            "output": ["http://169.254.169.254/latest/meta-data/"],
        }
    )
    assert r.status_code == 200  # never a retry-storm status
    assert spy.call_count == 0, "webhook fetched an internal URL (SSRF)"
    job = await async_db.get(GenerationJobORM, jid)
    await async_db.refresh(job)
    assert job.status == "processing"  # not finalized off attacker data
