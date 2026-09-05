"""Image-route raw-egress audit itemization (Phase 10, A3).

Search and media submits already write a category-itemized audit row via
``audit_raw_egress`` (``dlp_findings_json = {"raw_egress": true,
"channel": ..., "categories": {...}}``). Before this fix the image route
only flagged its own request-log row with the light ``raw_egress_allow``
policy_action marker — no findings JSON, no per-category counts — so the
compliance view could not itemize WHAT left unmasked on the image
channel.

Pins: an image generation whose prompt carries identity PII (regex-
detectable, NER not required) writes an audit row identical in shape to
the search/media ones — ``raw_egress: true``, ``channel: media_gen``,
``categories`` populated, counts only, never values.
"""

import contextlib
import json
import uuid

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.main import app as main_app
from app.models.request_log import RequestLogORM
from app.models.tenant import TenantORM

PII_PROMPT = "draw a birthday card for somchai, phone 081-234-5678"
CLEAN_PROMPT = "a red bicycle on a sunny beach"


class _FakeResult:
    created = 1_700_000_000
    model = "gpt-image-1"
    cost_usd = 0.04
    data = [
        {"url": "https://example.com/x.png", "b64_json": None, "revised_prompt": "art"}
    ]


async def _fake_generate_image(session, **kwargs):
    return _FakeResult()


@contextlib.contextmanager
def _auth(tenant_id):
    prev = main_app.dependency_overrides.get(get_resolved_api_key)
    main_app.dependency_overrides[get_resolved_api_key] = lambda: ResolvedApiKey(
        key_id="test-key-id",
        tenant_id=tenant_id,
        project_id="test-project",
        key_prefix="gw_test_",
        role="admin",
    )
    try:
        yield
    finally:
        if prev is not None:
            main_app.dependency_overrides[get_resolved_api_key] = prev
        else:
            main_app.dependency_overrides.pop(get_resolved_api_key, None)


@pytest.fixture(autouse=True)
def _mock_image_client(monkeypatch):
    monkeypatch.setattr(settings, "dlp_enabled", True)
    monkeypatch.setattr(
        "app.services.providers.openai_images_client.generate_image", _fake_generate_image
    )
    # The image route uses the sliding-window limiter (check_window_async),
    # which the conftest autouse fixture does NOT neutralize (it patches
    # check_all/check_all_async only). Allow-all here so repeated suite
    # runs against a live dev Redis can't 429 these tests.
    from app.services.rate_limit_service import RateLimitService

    async def _allow(self, *args, **kwargs):
        return {
            "allowed": True,
            "scope": None,
            "remaining": 999,
            "reset_at": 0,
            "limit": 999,
        }

    monkeypatch.setattr(RateLimitService, "check_window_async", _allow)


async def _seed_tenant(async_db, *, media_pii_egress="allow"):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    async_db.add(
        TenantORM(tenant_id=tid, name="T", slug=tid, media_pii_egress=media_pii_egress)
    )
    await async_db.commit()
    return tid


def _post(prompt, tenant_id):
    with _auth(tenant_id):
        return TestClient(main_app).post(
            "/v1/images/generations",
            json={"prompt": prompt, "model": "gpt-image-1"},
            headers={"Authorization": "Bearer test"},
        )


async def _itemized_rows(async_db, tenant_id):
    rows = (
        await async_db.execute(
            select(RequestLogORM).where(
                RequestLogORM.tenant_id == tenant_id,
                RequestLogORM.policy_action == "raw_egress_allow",
            )
        )
    ).scalars().all()
    # The itemized audit row carries the findings JSON (the request's own
    # SUCCESS log row carries the marker but no findings JSON).
    return [r for r in rows if r.dlp_findings_json]


@pytest.mark.asyncio
async def test_image_identity_pii_writes_itemized_raw_egress_row(async_db):
    tid = await _seed_tenant(async_db)
    r = _post(PII_PROMPT, tid)
    assert r.status_code == 200, r.text

    rows = await _itemized_rows(async_db, tid)
    assert len(rows) == 1, "expected exactly one itemized raw-egress audit row"
    row = rows[0]
    assert row.provider == "media_gen"

    payload = json.loads(row.dlp_findings_json)
    assert payload["raw_egress"] is True
    assert payload["channel"] == "media_gen"
    # Categories populated with per-category COUNTS.
    assert payload["categories"], "categories must be itemized"
    assert all(
        isinstance(c, int) and c > 0 for c in payload["categories"].values()
    )
    # Counts only — never the matched values or prompt text.
    for fragment in ("081-234-5678", "somchai", "birthday"):
        assert fragment not in (row.dlp_findings_json or "")
        assert fragment not in (row.dlp_categories or "")


@pytest.mark.asyncio
async def test_image_clean_prompt_writes_no_raw_egress_row(async_db):
    tid = await _seed_tenant(async_db)
    r = _post(CLEAN_PROMPT, tid)
    assert r.status_code == 200, r.text
    assert await _itemized_rows(async_db, tid) == []
