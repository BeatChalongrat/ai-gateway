"""The `mock` provider is hidden from the admin UI — and still works.

Two halves, and the SECOND is the one that matters. Hiding a provider from
a page is easy to do and easy to over-do: the obvious "tidy" follow-up is to
filter it out of the model registry, or to delete the adapter that nothing
appears to use any more. Either would silently break the zero-cost video
path that several integration tests submit against.

So this file pins both directions at once: the card is gone from the admin
surface, AND a mock video job still runs to completion.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from app.services.media_gen import job_loop
from app.services.portal import credit_service

client = TestClient(app)
_H = {"Authorization": "Bearer admin-key"}


# ── half 1: hidden from the admin surface ─────────────────────────────────

class TestMockHiddenFromAdmin:
    def test_providers_endpoint_has_no_mock_card(self):
        items = client.get("/admin/providers", headers=_H).json()["items"]
        names = {i["provider_name"] for i in items}
        assert "mock" not in names
        # Sanity: the list is otherwise populated, so an empty response
        # cannot pass this test by accident.
        assert "openai" in names

    def test_models_endpoint_does_not_list_mock_video(self):
        """Hiding the card while leaving the model listed is a half-change
        that reads as a bug later — both surfaces use the same filter."""
        items = client.get("/admin/models", headers=_H).json()["items"]
        assert not [i for i in items if i["provider"] == "mock"]
        assert not [i for i in items if i["model"] == "mock-video-1"]
        assert len(items) > 0

    def test_dashboard_counts_exclude_hidden_providers(self):
        """The dashboard tile must not count a provider whose card is hidden.

        Deliberately NOT asserted against the length of /providers: that
        endpoint also adds an empty card for every SELF_HOSTED_PROVIDERS entry
        (they need the endpoint form before discovery can populate them), so
        it legitimately shows more cards than the dashboard counts. That
        difference predates this filter and is not what this test is about.
        """
        from app.api.routes.admin import _visible_candidates

        dash = client.get("/admin/dashboard", headers=_H).json()
        visible = {c.provider for c in _visible_candidates()}
        assert "mock" not in visible
        assert dash["providers"] == len(visible)

    def test_filter_lives_in_the_admin_layer_not_the_registry(self, monkeypatch):
        """The filter is DISPLAY ONLY.

        Feeds a mock candidate through and asserts the admin helper drops it
        while the registry call it wraps still returns it. If someone moves
        this filter into get_all_candidates(), routing and model validation
        lose mock-video-1 and the media-gen tests break — this fails first.

        Driven by a monkeypatched registry rather than the real one so it does
        not depend on the DB having been loaded in this process.
        """
        from app.api.routes import admin as admin_routes

        class _C:
            def __init__(self, provider, model):
                self.provider, self.model = provider, model

        fake = [_C("openai", "gpt-4o"), _C("mock", "mock-video-1")]
        monkeypatch.setattr(
            "app.services.providers.model_registry.get_all_candidates",
            lambda: fake,
        )

        visible = admin_routes._visible_candidates()
        assert [c.model for c in visible] == ["gpt-4o"]
        # ...and the underlying registry was NOT mutated to achieve that.
        assert [c.model for c in fake] == ["gpt-4o", "mock-video-1"]

    def test_media_gen_registry_still_serves_mock(self):
        """The other half of "display, not function": the media-gen registry
        is a separate structure and this change must not have touched it."""
        from app.services.media_gen.registry import registered_providers

        assert "mock" in registered_providers()


# ── half 2: and it still works ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mock_video_still_submits_and_completes(async_db, tmp_path, monkeypatch):
    """The half that stops someone deleting the adapter later.

    If this fails after a "cleanup" commit, the cleanup is the bug — the
    provider is hidden from the admin UI on purpose and the capability is
    supposed to stay.
    """
    monkeypatch.setenv("MOCK_VIDEO_DONE_SECONDS", "0.5")
    monkeypatch.setenv("MOCK_VIDEO_HALF_SECONDS", "0.2")
    monkeypatch.setenv("GENERATION_JOB_ADAPTER_POLL_SECONDS", "0.2")
    monkeypatch.setenv("MEDIA_GEN_OUTPUT_DIR", str(tmp_path))

    tid = f"t-{uuid.uuid4().hex[:8]}"
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(TenantORM(tenant_id=tid, name="T", slug=tid))
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
            status="processing",  # post-claim state, as in test_mock_video_e2e
            input_prompt="a cat surfing a wave",
            input_metadata={"duration_sec": 5},
            estimated_cost_credits=credit_service.credits_for_cost(0.07 * 5, 1.0),
            ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    await async_db.commit()

    await job_loop._process_job(async_db, job_id)

    row = await async_db.get(GenerationJobORM, job_id)
    await async_db.refresh(row)
    assert row.status == "done", "the hidden provider must still produce video"
    assert row.progress_pct == 100

    meta = row.output_metadata
    assert meta and meta.get("output_path")
    with open(meta["output_path"], "rb") as fh:
        head = fh.read(12)
    assert head[4:8] == b"ftyp", f"not an MP4: {head!r}"
    assert meta["content_type"] == "video/mp4"
    assert meta["size"] > 0
