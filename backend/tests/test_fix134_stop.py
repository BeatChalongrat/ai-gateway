"""FIX134 E1 — a Stop must reach the provider at once and bill only what was delivered.

Measured live in FIX133/FIX134: on an external provider the backend logged
``stream_client_disconnected`` 36–68 s after the user's Stop — when the
provider's stream ENDED — and billed the provider's full usage (1,465
completion tokens for 36 delivered characters). On the self-hosted box the
same Stop was seen in 10 ms.

This test reproduces the real disconnect shape, not the ``task.cancel()``
shape of ``test_stream_abort_billing.py``: the app runs under a real uvicorn
(httptools) in a thread, a raw socket streams ``/v1/chat/completions``, reads a
few frames and closes. The fake provider keeps yielding on a cadence, exactly
like a model that has not finished, and records when it is told to stop.

Contract pinned here:

* the provider generator sees the cancellation within 1 s of the socket close
  (the provider HTTP stream is closed as soon as the client is gone);
* the request_log row bills the text that reached the wire before the close
  (a few frames of slack for what was in flight), never what the provider
  produced afterwards;
* exactly one ledger row (the FIX129 finalize-once invariant holds).
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid
from typing import AsyncIterator

import pytest
import uvicorn
from sqlalchemy.orm import sessionmaker

from app.domain.providers import ProviderStreamChunk
from app.main import app
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.request_log import RequestLogORM
from app.services.providers.openai_adapter import OpenAIAdapter
from tests.conftest import _integration_engine

_Session = sessionmaker(bind=_integration_engine)

# 60 chars per frame; the streaming DLP tail holds the last 120 chars, so the
# wire lags the provider by two frames.
_CHUNK = "streaming stop probe words flow onward without any stopping "
_PAYLOAD = {
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "fix134 stop probe"}],
    "metadata": {},
    "stream": True,
}
_CADENCE_S = 0.03
_FRAMES = 300  # 9 s of provider output if nobody stops it


@pytest.fixture()
def billable_user():
    db = _Session()
    user_id = str(uuid.uuid4())
    try:
        db.query(ApiKeyORM).filter(ApiKeyORM.key_id == "test-key-id").delete()
        db.add(PortalUserORM(
            id=user_id, email=f"fix134-{user_id[:8]}@test.com", password_hash="x",
            credit_balance=100_000, credit_used=0, plan="free_trial", status="active",
            tenant_id="test-tenant",
        ))
        db.flush()
        db.add(ApiKeyORM(
            key_id="test-key-id", key_prefix="gw_test_", key_hash="h_test-key-id",
            tenant_id="test-tenant", project_id="test-project", name="fix134",
            role="user", status="active", portal_user_id=user_id,
        ))
        db.commit()
    finally:
        db.close()
    yield user_id
    db = _Session()
    try:
        rids = [tx.request_id for tx in db.query(CreditTransactionORM).filter_by(user_id=user_id) if tx.request_id]
        db.query(CreditTransactionORM).filter_by(user_id=user_id).delete()
        if rids:
            db.query(RequestLogORM).filter(RequestLogORM.request_id.in_(rids)).delete(synchronize_session=False)
        db.query(ApiKeyORM).filter(ApiKeyORM.key_id == "test-key-id").delete()
        db.query(PortalUserORM).filter_by(id=user_id).delete()
        db.commit()
    finally:
        db.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    """The app under a real uvicorn in a thread (lifespan off: no boot seeds)."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning",
            http="httptools", loop="asyncio", lifespan="off",
        )
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "_Server":
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _stream_then_close(port: int, frames_to_read: int) -> tuple[str, int, float]:
    """POST the stream on a raw socket, read `frames_to_read` content frames, close.

    Returns (request_id, delivered content chars, monotonic close time)."""
    body = json.dumps(_PAYLOAD).encode()
    s = socket.create_connection(("127.0.0.1", port), timeout=15)
    s.sendall(
        b"POST /v1/chat/completions HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer demo-key\r\n"
        b"Content-Type: application/json\r\nAccept: text/event-stream\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    raw = b""
    while b"\r\n\r\n" not in raw:
        chunk = s.recv(65536)
        assert chunk, "connection closed before headers"
        raw += chunk
    head, rest = raw.split(b"\r\n\r\n", 1)
    status = head.split(b"\r\n", 1)[0]
    assert b" 200 " in status, status
    rid = ""
    for line in head.split(b"\r\n")[1:]:
        k, _, v = line.partition(b":")
        # Two X-Request-Id headers arrive (the request-context middleware's
        # uuid and the route's req_… id); request_logs is keyed by the latter.
        if k.strip().lower() == b"x-request-id" and (v.strip().startswith(b"req_") or not rid):
            rid = v.strip().decode()
    delivered = 0
    frames = 0
    buf = rest
    while frames < frames_to_read:
        chunk = s.recv(65536)
        assert chunk, "stream ended before the client stopped"
        buf += chunk
        # chunked transfer encoding: count content deltas in what arrived
        for m in buf.split(b"data: ")[1:]:
            pass
        text = buf.decode("utf-8", "replace")
        frames = 0
        delivered = 0
        for block in text.split("\n\n"):
            for line in block.split("\n"):
                if line.startswith("data: {"):
                    try:
                        d = json.loads(line[6:])
                    except Exception:
                        continue
                    content = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if content:
                        frames += 1
                        delivered += len(content)
    t_close = time.monotonic()
    s.close()
    return rid, delivered, t_close


def _log_for(request_id: str) -> RequestLogORM | None:
    db = _Session()
    try:
        return db.get(RequestLogORM, request_id)
    finally:
        db.close()


# (chunk text, seconds between chunks, chunks the provider would send if nobody stopped it).
# "slow" is a self-hosted-like cadence; "fast" is what deepseek-v4-flash does after its
# reasoning: a burst of 1–4 character deltas a few milliseconds apart (FIX134 E1 live
# captures: 286 deltas in 2 s). The fast shape is the one that lost the Stop.
_SHAPES = {
    "slow": (_CHUNK, _CADENCE_S, _FRAMES),
    "fast": ("ab ", 0.002, 30000),
}


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_stop_closes_provider_stream_at_once_and_bills_only_delivered(monkeypatch, billable_user, shape):
    chunk_text, cadence_s, frames = _SHAPES[shape]
    seen: dict = {"yielded": 0, "cancel_at": None, "ended_at": None}

    async def _spy(self, payload, messages, *, resolved_model=None, api_key=None) -> AsyncIterator[ProviderStreamChunk]:
        try:
            for _ in range(frames):
                yield ProviderStreamChunk(delta=chunk_text, model="gpt-4o-mini")
                seen["yielded"] += 1
                await asyncio.sleep(cadence_s)
            seen["ended_at"] = time.monotonic()
        except (GeneratorExit, asyncio.CancelledError):
            seen["cancel_at"] = time.monotonic()
            raise

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _spy)

    with _Server() as srv:
        rid, delivered, t_close = _stream_then_close(srv.port, frames_to_read=10)
        assert rid.startswith("req_"), rid
        assert delivered >= 9 * len(chunk_text.strip())  # the wire trims a trailing space

        deadline = time.monotonic() + frames * cadence_s + 10
        while seen["cancel_at"] is None and seen["ended_at"] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        lag = None if seen["cancel_at"] is None else seen["cancel_at"] - t_close
        assert seen["cancel_at"] is not None, (
            f"the provider generator was never told to stop; it ran to its end "
            f"({seen['yielded']} frames) — ended {seen['ended_at'] and seen['ended_at'] - t_close:.2f}s after the close"
        )
        assert lag < 1.0, f"provider stream closed {lag:.2f}s after the client disconnect (yielded {seen['yielded']})"

        # the ledger settles synchronously in the same task; give the row a moment
        row = None
        for _ in range(100):
            row = _log_for(rid)
            if row is not None:
                break
            time.sleep(0.05)
        assert row is not None, "aborted stream must persist a request_log row"
        # what reached the wire + the 120-char DLP tail + what was in flight during the
        # 1 s the contract allows (at most 1 s of provider output)
        max_billable_chars = delivered + 120 + int(len(chunk_text) * min(frames, 1.0 / cadence_s))
        assert row.completion_tokens <= max(1, max_billable_chars // 4), (
            f"billed {row.completion_tokens} completion tokens for {delivered} delivered chars "
            f"(provider yielded {seen['yielded']} frames after the client left)"
        )
        assert row.completion_tokens > 0

    db = _Session()
    try:
        # by request id: the API-key auth cache may still map the shared test key to
        # the previous parametrization's user, which is not what this test is about
        txs = db.query(CreditTransactionORM).filter_by(request_id=rid).all()
    finally:
        db.close()
    assert len(txs) == 1, "a stopped stream must write exactly one ledger row"
    assert txs[0].amount < 0
