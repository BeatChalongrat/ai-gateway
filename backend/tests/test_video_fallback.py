"""FIX67 Part 2.4 — video fallback happens only BEFORE dispatch.

The job loop's ``_submit_with_fallback`` tries the job's destination, then the
media_order fallbacks, until a provider job is submitted. A submit failure on the
primary falls to the next tier; once a submit succeeds the destination is fixed
(the caller then polls — a poll failure is terminal, never restarted).
"""

import types

import pytest
import pytest_asyncio

from app.services.media_gen.base import PollResult, SubmitResult
from app.services.media_gen.registry import register_adapter
from app.services.media_gen import job_loop
from app.services import provider_key_service as pks_mod
from app.models.system_setting import SystemSettingORM


class _FailVideoAdapter:
    provider_name = "fix67_failvid"
    supported_job_types = ["video"]

    async def submit_job(self, prompt, meta):
        raise RuntimeError("primary provider down")

    async def poll_job(self, ext):  # pragma: no cover
        return PollResult(status="failed")

    def estimate_cost_usd(self, meta, mc):  # pragma: no cover
        return 0


class _OkVideoAdapter:
    provider_name = "fix67_okvid"
    supported_job_types = ["video"]

    async def submit_job(self, prompt, meta):
        return SubmitResult(external_job_id="ext-123", initial_status="processing")

    async def poll_job(self, ext):  # pragma: no cover
        return PollResult(status="done", result_url="x")

    def estimate_cost_usd(self, meta, mc):  # pragma: no cover
        return 0


register_adapter(_FailVideoAdapter)
register_adapter(_OkVideoAdapter)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup(async_db):
    """The integration DB is shared; remove the committed media_order:video so
    it does not leak into later tests."""
    yield
    row = await async_db.get(SystemSettingORM, "media_order:video")
    if row is not None:
        await async_db.delete(row)
        await async_db.commit()


@pytest.mark.asyncio
async def test_primary_submit_fails_falls_to_next_before_dispatch(async_db, monkeypatch):
    async def _valid(self, session):
        return {"fix67_failvid", "fix67_okvid"}

    monkeypatch.setattr(pks_mod.ProviderKeyService, "providers_with_valid_keys_async", _valid)
    async_db.add(SystemSettingORM(key="media_order:video", value="fix67_failvid:fm, fix67_okvid:okm"))
    await async_db.commit()

    job = types.SimpleNamespace(
        provider="fix67_failvid", model_id="fm", job_type="video", input_prompt="a cat"
    )
    adapter, submit, meta = await job_loop._submit_with_fallback(async_db, job, {"estimate_usd": "0.1"})

    assert submit.external_job_id == "ext-123"  # the second tier dispatched
    assert job.provider == "fix67_okvid"        # attribution updated to the producer
    assert job.model_id == "okm"
    assert isinstance(adapter, _OkVideoAdapter)


@pytest.mark.asyncio
async def test_primary_ok_is_used_directly(async_db, monkeypatch):
    async def _valid(self, session):
        return {"fix67_okvid"}

    monkeypatch.setattr(pks_mod.ProviderKeyService, "providers_with_valid_keys_async", _valid)
    job = types.SimpleNamespace(
        provider="fix67_okvid", model_id="okm", job_type="video", input_prompt="a cat"
    )
    adapter, submit, meta = await job_loop._submit_with_fallback(async_db, job, {"estimate_usd": "0.1"})
    assert submit.external_job_id == "ext-123"
    assert job.provider == "fix67_okvid"  # unchanged, no fallback
