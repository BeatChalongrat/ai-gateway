"""ReplicateVideoAdapter unit tests (Sprint 2B) — NO live calls.

httpx is mocked with respx; the spend guards are monkeypatched so these are
pure unit tests (no Redis). Covers: budget-first, refund-on-failure,
duration clamp, status mapping, size cap, and token non-leakage.
"""

import json
import logging
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.core.config import settings
import app.services.media_gen.adapters.replicate_video as rv
from app.services.media_gen.adapters.replicate_video import (
    ContentPolicyBlocked,
    ProviderAuthError,
    ReplicateVideoAdapter,
)
from app.services.media_gen.spend_guard import SpendCapExceeded

_PRED = "https://api.replicate.com/v1/models/kwaivgi/kling-v1.5-pro/predictions"
_FAKE_TOKEN = "r8_FAKE_secret_token_do_not_log_123"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(settings, "replicate_api_token", _FAKE_TOKEN)
    monkeypatch.setattr(settings, "replicate_max_video_duration_sec", 10)
    monkeypatch.setattr(settings, "replicate_webhook_base_url", "")
    monkeypatch.setattr(settings, "replicate_webhook_secret", None)
    monkeypatch.setattr(settings, "replicate_request_timeout_sec", 600)


@pytest.fixture
def budget(monkeypatch):
    calls = {"reserve": 0, "release": 0}

    async def _reserve(estimate, **kw):
        calls["reserve"] += 1
        return True

    async def _release(estimate, **kw):
        calls["release"] += 1

    monkeypatch.setattr(rv, "check_and_reserve_session_budget", _reserve)
    monkeypatch.setattr(rv, "release_session_budget", _release)
    return calls


def _meta(**kw):
    m = {
        "estimate_usd": "0.35",
        "replicate_model": "kwaivgi/kling-v1.5-pro",
        "duration_sec": 5,
    }
    m.update(kw)
    return m


@pytest.mark.asyncio
@respx.mock
async def test_submit_happy_path(budget):
    route = respx.post(_PRED).mock(
        return_value=httpx.Response(201, json={"id": "pred_abc", "status": "starting"})
    )
    res = await ReplicateVideoAdapter().submit_job("a cat", _meta())
    assert res.external_job_id == "pred_abc"
    assert route.call_count == 1
    assert budget["reserve"] == 1 and budget["release"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_submit_budget_exhausted_makes_no_http_call(monkeypatch):
    async def _reserve(estimate, **kw):
        return False

    monkeypatch.setattr(rv, "check_and_reserve_session_budget", _reserve)
    route = respx.post(_PRED).mock(return_value=httpx.Response(201, json={"id": "x"}))
    with pytest.raises(SpendCapExceeded):
        await ReplicateVideoAdapter().submit_job("a cat", _meta())
    assert route.call_count == 0  # budget check is the FIRST thing


@pytest.mark.asyncio
@respx.mock
async def test_submit_401_raises_and_refunds(budget):
    respx.post(_PRED).mock(return_value=httpx.Response(401, json={"detail": "no"}))
    with pytest.raises(ProviderAuthError):
        await ReplicateVideoAdapter().submit_job("a cat", _meta())
    assert budget["release"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_submit_422_raises_content_policy_and_refunds(budget):
    respx.post(_PRED).mock(
        return_value=httpx.Response(422, json={"detail": "policy violation"})
    )
    with pytest.raises(ContentPolicyBlocked):
        await ReplicateVideoAdapter().submit_job("a cat", _meta())
    assert budget["release"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_poll_succeeded():
    respx.get("https://api.replicate.com/v1/predictions/pred_abc").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "succeeded",
                "output": ["https://replicate.delivery/out.mp4"],
                "metrics": {"predict_time": 4.2},
            },
        )
    )
    res = await ReplicateVideoAdapter().poll_job("pred_abc")
    assert res.status == "done"
    assert res.result_url == "https://replicate.delivery/out.mp4"
    assert res.progress_pct == 100


@pytest.mark.asyncio
@respx.mock
async def test_poll_failed_truncates_logs():
    long_err = "boom " * 200  # 1000 chars
    respx.get("https://api.replicate.com/v1/predictions/pred_x").mock(
        return_value=httpx.Response(200, json={"status": "failed", "error": long_err})
    )
    res = await ReplicateVideoAdapter().poll_job("pred_x")
    assert res.status == "failed"
    assert res.error_code == "failed"
    assert res.error_message and len(res.error_message) <= 500


@pytest.mark.asyncio
@respx.mock
async def test_cancel():
    respx.post("https://api.replicate.com/v1/predictions/pred_abc/cancel").mock(
        return_value=httpx.Response(200)
    )
    assert await ReplicateVideoAdapter().cancel_job("pred_abc") is True


def test_estimate_clamps_duration():
    a = ReplicateVideoAdapter()
    mc = SimpleNamespace(cost_per_second=Decimal("0.07"))
    assert a.estimate_cost_usd({"duration_sec": 5}, mc) == Decimal("0.35")
    # 60s requested → clamped to 10 → 0.70
    assert a.estimate_cost_usd({"duration_sec": 60}, mc) == Decimal("0.70")


@pytest.mark.asyncio
@respx.mock
async def test_submit_clamps_duration_in_payload(budget):
    captured = {}

    def _resp(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "pred_clamp"})

    respx.post(_PRED).mock(side_effect=_resp)
    await ReplicateVideoAdapter().submit_job("a cat", _meta(duration_sec=60))
    assert captured["body"]["input"]["duration"] == 10  # not 60


@pytest.mark.asyncio
@respx.mock
async def test_token_never_leaks(budget, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    respx.post(_PRED).mock(return_value=httpx.Response(201, json={"id": "pred_tok"}))
    a = ReplicateVideoAdapter()
    await a.submit_job("a cat", _meta())
    out = capsys.readouterr()
    combined = out.out + out.err + caplog.text + repr(a) + repr(settings)
    assert "r8_" not in combined
    assert _FAKE_TOKEN not in combined


@pytest.mark.asyncio
@respx.mock
async def test_fetch_rejects_oversize():
    url = "https://replicate.delivery/big.mp4"
    respx.head(url).mock(
        return_value=httpx.Response(200, headers={"content-length": str(300 * 1024 * 1024)})
    )
    with pytest.raises(ValueError):
        await ReplicateVideoAdapter().fetch_result_bytes(url)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_small_ok():
    url = "https://replicate.delivery/small.mp4"
    respx.head(url).mock(
        return_value=httpx.Response(200, headers={"content-length": str(50 * 1024 * 1024)})
    )
    respx.get(url).mock(
        return_value=httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42rest")
    )
    data = await ReplicateVideoAdapter().fetch_result_bytes(url)
    assert data[4:8] == b"ftyp"
