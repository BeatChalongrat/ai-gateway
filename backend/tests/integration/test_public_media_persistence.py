"""Server-side persistence round-trip for direct API media gen (Sprint 5).

Reconciliation note: video/audio already persist server-side — the job loop
writes the artifact under MEDIA_GEN_OUTPUT_DIR and the status route exposes a
derived ``output_url`` that ``GET /v1/jobs/{id}/output`` streams. This test
proves that round-trip end-to-end through the HTTP layer (the Sprint-1 PR-1
gap was the IMAGE path, deferred this sprint — see Phase 0). It also confirms
the Sprint-5 provenance dict surfaces on the status response.
"""

import contextlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.main import app as main_app
from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from app.services.media_gen import job_loop


@contextlib.contextmanager
def _auth(portal_user_id, tenant_id):
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


@pytest.mark.asyncio
async def test_video_output_round_trips(async_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_VIDEO_DONE_SECONDS", "0.5")
    monkeypatch.setenv("MOCK_VIDEO_HALF_SECONDS", "0.2")
    monkeypatch.setenv("GENERATION_JOB_ADAPTER_POLL_SECONDS", "0.2")
    monkeypatch.setenv("MEDIA_GEN_OUTPUT_DIR", str(tmp_path))

    tid = f"t-{uuid.uuid4().hex[:8]}"
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(TenantORM(tenant_id=tid, name="T", slug=tid))
    # tenant_id NOT NULL since SSO Phase 1 A1
    async_db.add(
        PortalUserORM(id=uid, email=f"{uid}@e.com", password_hash="x", tenant_id=tid)
    )
    await async_db.flush()

    job_id = str(uuid.uuid4())
    async_db.add(
        GenerationJobORM(
            id=job_id,
            tenant_id=tid,
            user_id=uid,
            job_type="video",
            provider="mock",
            model_id="mock-video-1",
            status="processing",
            input_prompt="a cat surfing a wave",
            input_metadata={"duration_sec": 5},
            estimated_cost_credits=1_050_000,
            ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    await async_db.commit()

    # Drive the job to done (writes the artifact + provenance server-side).
    await job_loop._process_job(async_db, job_id)
    await async_db.commit()

    with _auth(uid, tenant_id=tid):
        client = TestClient(main_app)

        # Status route exposes a derived output_url + provenance.
        status = client.get(f"/v1/jobs/{job_id}")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["status"] == "done"
        assert body["output_url"] == f"/v1/jobs/{job_id}/output"
        assert body["provenance"]["provider"] == "mock"

        # output_url is fetchable and returns the persisted bytes.
        out = client.get(f"/v1/jobs/{job_id}/output")
        assert out.status_code == 200
        assert out.content[4:8] == b"ftyp"  # valid MP4 artifact


@pytest.mark.asyncio
async def test_output_404_before_done(async_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_GEN_OUTPUT_DIR", str(tmp_path))
    tid = f"t-{uuid.uuid4().hex[:8]}"
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(TenantORM(tenant_id=tid, name="T", slug=tid))
    # tenant_id NOT NULL since SSO Phase 1 A1
    async_db.add(
        PortalUserORM(id=uid, email=f"{uid}@e.com", password_hash="x", tenant_id=tid)
    )
    await async_db.flush()
    job_id = str(uuid.uuid4())
    async_db.add(
        GenerationJobORM(
            id=job_id, tenant_id=tid, user_id=uid, job_type="video",
            provider="mock", model_id="mock-video-1", status="queued",
            input_prompt="x", input_metadata={}, estimated_cost_credits=1,
            ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    await async_db.commit()
    with _auth(uid, tenant_id=tid):
        out = TestClient(main_app).get(f"/v1/jobs/{job_id}/output")
    assert out.status_code == 404
