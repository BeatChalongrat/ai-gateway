"""Provenance metadata tests (Sprint 5).

Covers the pure builder (:mod:`app.services.media_gen.provenance`) plus the
job-loop integration point that lands ``provenance`` in
``generation_jobs.output_metadata`` — built in the loop, NOT the adapter
(adapters are sealed this sprint).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from app.services.media_gen import job_loop
from app.services.media_gen.provenance import (
    PROVIDER_PROVENANCE_DEFAULTS,
    ProvenanceMetadata,
    build_provenance,
)


def test_to_dict_excludes_none_fields():
    prov = ProvenanceMetadata(provider="openai", model_id="gpt-4o-mini-tts")
    d = prov.to_dict()
    assert d == {"provider": "openai", "model_id": "gpt-4o-mini-tts"}
    # None-valued optionals must not appear (keeps the JSONB compact).
    assert "generation_seed" not in d
    assert "c2pa_embedded" not in d


def test_provider_defaults_have_expected_keys():
    for provider in ("replicate", "openai", "elevenlabs"):
        defaults = PROVIDER_PROVENANCE_DEFAULTS[provider]
        assert set(defaults) == {
            "c2pa_embedded",
            "synthid_embedded",
            "visible_watermark",
        }
    # OpenAI/ElevenLabs disclose C2PA; Replicate (model host) does not.
    assert PROVIDER_PROVENANCE_DEFAULTS["openai"]["c2pa_embedded"] is True
    assert PROVIDER_PROVENANCE_DEFAULTS["replicate"]["c2pa_embedded"] is False


def test_build_provenance_lifts_seed_and_timestamp():
    out = build_provenance(
        "replicate",
        "meta/musicgen",
        {"seed": "12345", "completed_at": "2026-06-23T00:00:00Z"},
    )
    assert out["provider"] == "replicate"
    assert out["model_id"] == "meta/musicgen"
    assert out["generation_seed"] == 12345
    assert out["provider_disclosed_at"] == "2026-06-23T00:00:00Z"
    assert out["c2pa_embedded"] is False  # from defaults


def test_build_provenance_adapter_supplied_overrides():
    # An adapter that discloses its own provenance wins field-by-field.
    out = build_provenance(
        "replicate",
        "some/model",
        {"provenance": {"c2pa_embedded": True, "visible_watermark": True}},
    )
    assert out["c2pa_embedded"] is True
    assert out["visible_watermark"] is True


def test_build_provenance_unknown_provider_minimal():
    out = build_provenance("fal", "fal/x", None)
    assert out == {"provider": "fal", "model_id": "fal/x"}


@pytest.mark.asyncio
async def test_mock_video_job_records_provenance(async_db, tmp_path, monkeypatch):
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

    await job_loop._process_job(async_db, job_id)

    row = await async_db.get(GenerationJobORM, job_id)
    await async_db.refresh(row)
    assert row.status == "done"
    prov = (row.output_metadata or {}).get("provenance")
    assert prov is not None
    assert prov["provider"] == "mock"
    assert prov["model_id"] == "mock-video-1"
    # Mock has no PROVIDER_PROVENANCE_DEFAULTS entry → minimal dict.
    assert "c2pa_embedded" not in prov
