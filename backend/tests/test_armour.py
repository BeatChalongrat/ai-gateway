"""FIX26 §2.2 — the armour: liveness vs readiness, the bounded stream, the
in-flight registry / admission cap.

Pure and monkeypatched — no DB, no Redis, no sidecar. The live proof
(readiness red while the service cannot serve) is in the report.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import health as health_mod
from app.core.bounded_stream import bounded_sse
from app.core.inflight import AdmissionTimeout, InflightRegistry


# ── in-flight registry ─────────────────────────────────────────────────
def test_registry_counts_ages_and_flags_stuck():
    async def run():
        reg = InflightRegistry(limit=2)
        k1 = await reg.admit(timeout_s=0.1, request_id="a")
        snap = reg.snapshot(stuck_after_s=10)
        assert (snap.count, snap.stuck, snap.available) == (1, 0, 1)
        # Backdate it: older than the bound → stuck.
        reg._started[k1] -= 11  # noqa: SLF001 — test-only clock trick
        assert reg.snapshot(stuck_after_s=10).stuck == 1
        assert reg.release(k1) is True
        assert reg.release(k1) is False  # idempotent
        assert reg.snapshot(stuck_after_s=10).count == 0

    asyncio.run(run())


def test_admission_cap_refuses_fast_when_full():
    async def run():
        reg = InflightRegistry(limit=1)
        k = await reg.admit(timeout_s=0.1)
        with pytest.raises(AdmissionTimeout):
            await reg.admit(timeout_s=0.05)   # the one slot is held → fast refusal
        reg.release(k)
        k2 = await reg.admit(timeout_s=0.1)  # freed → admitted
        reg.release(k2)

    asyncio.run(run())


# ── bounded stream ─────────────────────────────────────────────────────
async def _frames(delays):
    for d, f in delays:
        await asyncio.sleep(d)
        yield f


def _collect(gen):
    async def run():
        out = []
        async for f in gen:
            out.append(f)
        return out

    return asyncio.run(run())


def test_bounded_stream_passes_a_normal_stream_through():
    out = _collect(bounded_sse(_frames([(0, "data: a\n\n"), (0.01, "data: b\n\n"), (0, "data: [DONE]\n\n")]), idle_timeout_s=1, hard_timeout_s=5, request_id="r1"))
    assert out == ["data: a\n\n", "data: b\n\n", "data: [DONE]\n\n"]


def test_bounded_stream_idle_timeout_ends_with_error_frame_and_done():
    kinds = []
    out = _collect(bounded_sse(_frames([(0, "data: a\n\n"), (0.5, "data: never\n\n")]), idle_timeout_s=0.1, hard_timeout_s=5, request_id="r2", on_timeout=kinds.append))
    assert out[0] == "data: a\n\n"
    err = json.loads(out[1][len("data: "):])
    assert err["error"]["type"] == "stream_timeout" and err["error"]["request_id"] == "r2"
    assert "no data" in err["error"]["message"]
    assert out[-1] == "data: [DONE]\n\n" and len(out) == 3
    assert kinds == ["idle"]


def test_bounded_stream_hard_timeout_closes_inner_generator():
    closed = []

    async def slow_forever():
        try:
            while True:
                await asyncio.sleep(0.05)
                yield "data: tick\n\n"
        finally:
            closed.append(True)  # the inner generator's finally (billing/log) still runs

    kinds = []
    out = _collect(bounded_sse(slow_forever(), idle_timeout_s=1, hard_timeout_s=0.2, request_id="r3", on_timeout=kinds.append))
    assert out[-1] == "data: [DONE]\n\n"
    assert any("stream exceeded" in f for f in out)
    assert kinds == ["hard"] and closed == [True]


# ── readiness ──────────────────────────────────────────────────────────
@pytest.fixture
def ready_app(monkeypatch):
    app = FastAPI()
    app.include_router(health_mod.router)

    async def ok():
        return "ok"

    monkeypatch.setattr(health_mod, "_ready_check_db", ok)
    monkeypatch.setattr(health_mod, "_ready_check_redis", ok)
    monkeypatch.setattr(health_mod, "_ready_check_dlp", ok)
    return app


def test_live_is_constant(ready_app):
    r = TestClient(ready_app).get("/live")
    assert r.status_code == 200 and r.json()["status"] == "alive"


def test_ready_green_when_every_real_path_check_passes(ready_app):
    r = TestClient(ready_app).get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" and body["failures"] == []
    assert body["checks"]["database"] == "ok" and body["checks"]["dlp_engine"] == "ok"
    assert body["checks"]["inflight"]["stuck"] == 0


def test_ready_red_when_the_dlp_engine_cannot_scan(ready_app, monkeypatch):
    async def broken():
        raise RuntimeError("rust dlp engine unreachable")

    monkeypatch.setattr(health_mod, "_ready_check_dlp", broken)
    r = TestClient(ready_app).get("/ready")
    assert r.status_code == 503
    assert r.json()["failures"] == ["dlp_engine"]
    assert "unreachable" in r.json()["checks"]["dlp_engine"]


def test_ready_red_when_a_request_outlived_its_bound(ready_app, monkeypatch):
    from app.core import inflight

    reg = inflight.configure(8)

    async def plant():
        k = await reg.admit(timeout_s=0.1, request_id="stuck")
        reg._started[k] -= 10_000  # noqa: SLF001 — far older than any bound

    asyncio.run(plant())
    try:
        r = TestClient(ready_app).get("/ready")
        assert r.status_code == 503
        assert "stuck_requests" in r.json()["failures"]
        assert r.json()["checks"]["inflight"]["stuck"] == 1
    finally:
        inflight.configure(64)


def test_ready_probe_cannot_itself_hang(ready_app, monkeypatch):
    """A check that never returns → 503 'probe timed out' within the budget."""
    import time

    async def hangs():
        await asyncio.sleep(30)
        return "ok"

    monkeypatch.setattr(health_mod, "_ready_check_db", hangs)
    monkeypatch.setattr(health_mod.settings, "readiness_timeout_seconds", 0.3, raising=False)
    t0 = time.perf_counter()
    r = TestClient(ready_app).get("/ready")
    dt = time.perf_counter() - t0
    assert r.status_code == 503
    assert "probe_timeout" in r.json()["failures"]
    assert dt < 3.0, f"probe took {dt:.1f}s — it hung"
