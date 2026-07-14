"""Media artifact extension/MIME per job type (Ecosystem Fix Phase 10, A1).

Before this fix BOTH persistence paths hardcoded video: the job loop wrote
``{job_id}.mp4`` for EVERY job type, and the Replicate webhook additionally
hardcoded ``content_type: "video/mp4"`` — so a ``music`` / ``audio_tts``
result was persisted as a video file with a video MIME.

Pins the shared resolver (``resolve_output_ext_and_type``) and drives both
persistence paths with audio jobs:

* job loop + sync TTS adapter (``content_type: audio/mpeg``) → ``.mp3``;
* job loop + music adapter whose result URL ends ``.wav`` (Replicate's
  poll metadata carries NO content_type) → ``.wav`` + ``audio/*``;
* webhook + music job with an ``.mp3`` output URL → ``.mp3`` + ``audio/*``;
* the video happy path stays ``.mp4`` / ``video/mp4`` (no regression on
  the path ``test_mock_video_e2e`` covers).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.core.config import settings
from app.main import app as main_app
from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from app.services.media_gen import job_loop
from app.services.media_gen.base import (
    MediaGenAdapter,
    PollResult,
    SubmitResult,
    SyncResult,
)
from app.services.media_gen.job_loop import resolve_output_ext_and_type


# ---------------------------------------------------------------------------
# Unit: the shared resolver
# ---------------------------------------------------------------------------


class TestResolveOutputExtAndType:
    def test_adapter_content_type_wins(self):
        ext, ct = resolve_output_ext_and_type(
            "audio_tts", {"content_type": "audio/mpeg"}, None
        )
        assert ext == ".mp3"
        assert ct == "audio/mpeg"

    def test_video_meta_stays_mp4(self):
        ext, ct = resolve_output_ext_and_type(
            "video", {"content_type": "video/mp4"}, None
        )
        assert (ext, ct) == (".mp4", "video/mp4")

    def test_music_url_extension_used_when_meta_has_no_content_type(self):
        # Replicate poll metadata carries only replicate_metrics.
        ext, ct = resolve_output_ext_and_type(
            "music",
            {"replicate_metrics": {}},
            "https://replicate.delivery/pbxt/abc/out.wav?x=1",
        )
        assert ext == ".wav"
        assert ct.startswith("audio/")

    def test_music_defaults_to_audio_not_video(self):
        ext, ct = resolve_output_ext_and_type("music", None, None)
        assert ext == ".mp3"
        assert ct == "audio/mpeg"

    def test_tts_defaults_to_audio_not_video(self):
        ext, ct = resolve_output_ext_and_type("audio_tts", {}, "sync://abc123")
        assert ext == ".mp3"
        assert ct == "audio/mpeg"

    def test_video_default(self):
        ext, ct = resolve_output_ext_and_type("video", None, None)
        assert (ext, ct) == (".mp4", "video/mp4")

    def test_unknown_job_type_never_mislabels_as_video(self):
        ext, ct = resolve_output_ext_and_type("hologram", None, None)
        assert (ext, ct) == (".bin", "application/octet-stream")


# ---------------------------------------------------------------------------
# Integration: job-loop persistence per job_type
# ---------------------------------------------------------------------------


class _FakeSyncAudioAdapter(MediaGenAdapter):
    """Mimics the OpenAI/ElevenLabs TTS sync fast-path: inline bytes with
    ``content_type: audio/mpeg`` in the sync result metadata."""

    provider_name = "fake"
    supported_job_types = ["audio_tts"]

    async def submit_job(self, input_prompt, input_metadata):
        return SubmitResult(
            external_job_id=None,
            initial_status="done",
            sync_result=SyncResult(
                result_url="sync://fake",
                result_metadata={"content_type": "audio/mpeg"},
            ),
        )

    async def poll_job(self, external_job_id):  # pragma: no cover - unused
        raise NotImplementedError

    def estimate_cost_usd(self, input_metadata, model_config):
        return 0

    async def fetch_result_bytes(self, result_url):
        return b"ID3fake-mp3-bytes"


class _FakeMusicAdapter(MediaGenAdapter):
    """Mimics the Replicate music path: async result whose poll metadata has
    NO content_type; the delivery URL carries the ``.wav`` extension."""

    provider_name = "fake"
    supported_job_types = ["music"]

    async def submit_job(self, input_prompt, input_metadata):
        return SubmitResult(
            external_job_id="pred_fake",
            initial_status="processing",
            sync_result=None,
        )

    async def poll_job(self, external_job_id):
        return PollResult(
            status="done",
            progress_pct=100,
            result_url="https://replicate.delivery/pbxt/abc/out.wav",
            result_metadata={"replicate_metrics": {}},
        )

    def estimate_cost_usd(self, input_metadata, model_config):
        return 0

    async def fetch_result_bytes(self, result_url):
        return b"RIFFfake-wav-bytes"


async def _seed_job(async_db, *, job_type, provider="fake", ext_id=None):
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
            job_type=job_type,
            provider=provider,
            model_id="fake-model",
            status="processing",
            external_job_id=ext_id,
            input_prompt="a jazzy tune",
            input_metadata={},
            estimated_cost_credits=1,
            ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    await async_db.commit()
    return job_id


def _patch_adapter(monkeypatch, adapter):
    def _fake_get_adapter(provider, job_type):
        assert job_type in adapter.supported_job_types
        return adapter

    monkeypatch.setattr(job_loop, "get_adapter", _fake_get_adapter)


@pytest.mark.asyncio
async def test_tts_job_persists_audio_extension_and_mime(
    async_db, tmp_path, monkeypatch
):
    monkeypatch.setenv("MEDIA_GEN_OUTPUT_DIR", str(tmp_path))
    _patch_adapter(monkeypatch, _FakeSyncAudioAdapter())
    job_id = await _seed_job(async_db, job_type="audio_tts")

    await job_loop._process_job(async_db, job_id)

    row = await async_db.get(GenerationJobORM, job_id)
    await async_db.refresh(row)
    assert row.status == "done"
    meta = row.output_metadata
    assert meta["output_path"].endswith(".mp3")
    assert not meta["output_path"].endswith(".mp4")
    assert meta["content_type"].startswith("audio/")
    assert (tmp_path / f"{job_id}.mp3").exists()


@pytest.mark.asyncio
async def test_music_job_persists_audio_extension_and_mime(
    async_db, tmp_path, monkeypatch
):
    monkeypatch.setenv("MEDIA_GEN_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GENERATION_JOB_ADAPTER_POLL_SECONDS", "0.2")
    _patch_adapter(monkeypatch, _FakeMusicAdapter())
    job_id = await _seed_job(async_db, job_type="music", ext_id="pred_fake")

    await job_loop._process_job(async_db, job_id)

    row = await async_db.get(GenerationJobORM, job_id)
    await async_db.refresh(row)
    assert row.status == "done"
    meta = row.output_metadata
    assert meta["output_path"].endswith(".wav")
    assert not meta["output_path"].endswith(".mp4")
    assert meta["content_type"].startswith("audio/")
    assert (tmp_path / f"{job_id}.wav").exists()


# ---------------------------------------------------------------------------
# Integration: webhook persistence path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_music_job_persists_audio_not_video(
    async_db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "replicate_webhook_secret", "wh-secret")
    monkeypatch.setenv("MEDIA_GEN_OUTPUT_DIR", str(tmp_path))

    import app.api.routes.webhooks_replicate as wh_mod

    monkeypatch.setattr(
        wh_mod, "get_adapter", lambda provider, job_type: _FakeMusicAdapter()
    )

    async def _noop_commit(tenant_id, amount):
        return None

    monkeypatch.setattr(wh_mod, "commit_daily_spend", _noop_commit)

    job_id = await _seed_job(
        async_db, job_type="music", provider="replicate", ext_id="pred_music_wh"
    )

    r = TestClient(main_app).post(
        "/webhooks/replicate?secret=wh-secret",
        json={
            "id": "pred_music_wh",
            "status": "succeeded",
            "output": ["https://replicate.delivery/pbxt/abc/out.mp3"],
        },
    )
    assert r.status_code == 200, r.text

    row = await async_db.get(GenerationJobORM, job_id)
    await async_db.refresh(row)
    assert row.status == "done"
    meta = row.output_metadata
    assert meta["output_path"].endswith(".mp3")
    assert not meta["output_path"].endswith(".mp4")
    assert meta["content_type"].startswith("audio/")
    assert (tmp_path / f"{job_id}.mp3").exists()


@pytest.mark.asyncio
async def test_webhook_video_job_stays_mp4(async_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "replicate_webhook_secret", "wh-secret")
    monkeypatch.setenv("MEDIA_GEN_OUTPUT_DIR", str(tmp_path))

    import app.api.routes.webhooks_replicate as wh_mod

    class _FakeVideoAdapter(_FakeMusicAdapter):
        supported_job_types = ["video"]

        async def fetch_result_bytes(self, result_url):
            return b"\x00\x00\x00\x18ftypmp42fake"

    monkeypatch.setattr(
        wh_mod, "get_adapter", lambda provider, job_type: _FakeVideoAdapter()
    )

    async def _noop_commit(tenant_id, amount):
        return None

    monkeypatch.setattr(wh_mod, "commit_daily_spend", _noop_commit)

    job_id = await _seed_job(
        async_db, job_type="video", provider="replicate", ext_id="pred_video_wh"
    )

    r = TestClient(main_app).post(
        "/webhooks/replicate?secret=wh-secret",
        json={
            "id": "pred_video_wh",
            "status": "succeeded",
            "output": ["https://replicate.delivery/pbxt/abc/out.mp4"],
        },
    )
    assert r.status_code == 200, r.text

    row = await async_db.get(GenerationJobORM, job_id)
    await async_db.refresh(row)
    assert row.status == "done"
    meta = row.output_metadata
    assert meta["output_path"].endswith(".mp4")
    assert meta["content_type"] == "video/mp4"
