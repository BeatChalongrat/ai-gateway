import asyncio

import httpx
import pytest

from app.middleware.timeout import TimeoutMiddleware


async def _slow_app(scope, receive, send):
    await asyncio.sleep(0.05)
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.asyncio
async def test_timeout_middleware_path_override_allows_slow_image_route():
    app = TimeoutMiddleware(
        _slow_app,
        timeout_seconds=0.01,
        path_timeouts={"/v1/images/generations": 0.2},
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/v1/images/generations")

    assert res.status_code == 200
    assert res.text == "ok"


@pytest.mark.asyncio
async def test_timeout_middleware_default_still_times_out_other_routes():
    app = TimeoutMiddleware(
        _slow_app,
        timeout_seconds=0.01,
        path_timeouts={"/v1/images/generations": 0.2},
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/v1/models")

    assert res.status_code == 504
    assert res.json()["error"]["code"] == "timeout"
