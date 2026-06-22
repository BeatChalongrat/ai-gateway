"""End-to-end mock video job processing (Sprint 2A).

Drives ``_process_job`` (the dialect-agnostic processing step) against a
claimed job and asserts it reaches ``done`` with a valid MP4 artifact and
the expected credit cost. The queued→processing CLAIM is Postgres-only
(advisory lock + SKIP LOCKED) and is verified live on the real stack;
here we start from the post-claim ``processing`` state.
"""

import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from app.services.media_gen import job_loop
from app.services.portal import credit_service


@pytest.mark.asyncio
async def test_mock_video_job_reaches_done(async_db, tmp_path, monkeypatch):
    # Fast mock timeline + isolated output dir.
    monkeypatch.setenv("MOCK_VIDEO_DONE_SECONDS", "0.5")
    monkeypatch.setenv("MOCK_VIDEO_HALF_SECONDS", "0.2")
    monkeypatch.setenv("GENERATION_JOB_ADAPTER_POLL_SECONDS", "0.2")
    monkeypatch.setenv("MEDIA_GEN_OUTPUT_DIR", str(tmp_path))

    tid = f"t-{uuid.uuid4().hex[:8]}"
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(TenantORM(tenant_id=tid, name="T", slug=tid))
    async_db.add(PortalUserORM(id=uid, email=f"{uid}@e.com", password_hash="x"))
    await async_db.flush()

    estimated = credit_service.credits_for_cost(0.07 * 5, 1.0)  # 1,050,000
    job_id = str(uuid.uuid4())
    async_db.add(
        GenerationJobORM(
            id=job_id,
            tenant_id=tid,
            user_id=uid,
            job_type="video",
            provider="mock",
            model_id="mock-video-1",
            status="processing",  # post-claim state
            input_prompt="a cat surfing a wave",
            input_metadata={"duration_sec": 5},
            estimated_cost_credits=estimated,
            ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    await async_db.commit()

    await job_loop._process_job(async_db, job_id)

    row = await async_db.get(GenerationJobORM, job_id)
    await async_db.refresh(row)
    assert row.status == "done"
    assert row.progress_pct == 100
    assert row.completed_at is not None

    # Output artifact persisted + valid MP4 (ftyp box at bytes 4-8).
    meta = row.output_metadata
    assert meta and meta.get("output_path")
    with open(meta["output_path"], "rb") as fh:
        head = fh.read(12)
    assert head[4:8] == b"ftyp", f"not an MP4: {head!r}"
    assert meta["content_type"] == "video/mp4"
    assert meta["size"] > 0

    # Cost: cost_per_second(0.07) × duration(5) × usage_credits_per_usd.
    expected = math.ceil(0.07 * 5 * 3_000_000)
    assert row.actual_cost_credits == expected == estimated


@pytest.mark.asyncio
async def test_unknown_provider_marks_no_adapter(async_db, monkeypatch):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(TenantORM(tenant_id=tid, name="T", slug=tid))
    async_db.add(PortalUserORM(id=uid, email=f"{uid}@e.com", password_hash="x"))
    await async_db.flush()
    job_id = str(uuid.uuid4())
    async_db.add(
        GenerationJobORM(
            id=job_id,
            tenant_id=tid,
            user_id=uid,
            job_type="video",
            provider="nonexistent",
            model_id="x",
            status="processing",
            input_prompt="x",
            input_metadata={},
            estimated_cost_credits=1,
            ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    await async_db.commit()

    await job_loop._process_job(async_db, job_id)
    row = await async_db.get(GenerationJobORM, job_id)
    await async_db.refresh(row)
    assert row.status == "failed"
    assert row.error_code == "no_adapter"
