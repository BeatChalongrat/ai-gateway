"""Billing coverage for ABNORMAL streaming exits (fix/stream-abort-billing).

The live audit (2026-08-11) proved that a real client disconnect —
which uvicorn/starlette delivers as ``asyncio.CancelledError`` at the
generator's current await point, NOT as ``GeneratorExit`` — skipped
``_finalize_stream`` entirely: no credit deduction, no
``credit_transactions`` row, no ``request_logs`` row, no log line,
while the provider had already produced (and billed us for) hundreds
of tokens. The fallback-exhausted / mid-stream-error paths ``return``
without finalizing the same way.

These tests pin the fixed contract of the ``_billing_safe_stream``
wrapper in ``public_chat.py``:

* cancel mid-stream  → partial content is billed (estimate), ledger +
  request_log written, and the CancelledError still propagates.
* normal stream      → exactly ONE deduction (the wrapper's safety net
  must no-op thanks to the ``_stream_finalized`` guard).
* mid-stream provider error after content reached the wire → billed +
  request_log with status ``provider_error``.
* cancel BEFORE any content reached the wire (and no provider usage)
  → NO charge, mirroring the non-stream failure posture.

Provider I/O is faked by monkeypatching ``OpenAIAdapter.stream_chat``
(same pattern as ``test_streaming_dlp.py``). Billing resolves through
the key-owner path: an ``api_keys`` row for the MOCK_AUTH key_id
(``test-key-id``) pointing at a fresh portal user per test.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import sessionmaker

from app.domain.providers import ProviderStreamChunk
from app.main import app
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.request_log import RequestLogORM
from app.services.providers.openai_adapter import OpenAIAdapter
from tests.conftest import _integration_engine


client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}
_Session = sessionmaker(bind=_integration_engine)

# Long enough that content clears the streaming DLP rolling tail
# (MIN_TAIL_CHARS = 120) and actually reaches the wire.
_CHUNK = "streaming billing audit words flow onward without stopping " # 60 chars

_STREAM_PAYLOAD = {
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "stream billing lifecycle test"}],
    "metadata": {},
    "stream": True,
}


@pytest.fixture()
def billable_user():
    """Portal user + api_keys row binding MOCK_AUTH's ``test-key-id``.

    With the row present, ``_resolve_billable_user`` finds the key's
    owning portal user and streaming requests become billable —
    exactly the production web-chat shape. Torn down completely so
    other suites keep seeing a non-billable test key.
    """
    db = _Session()
    user_id = str(uuid.uuid4())
    try:
        db.query(ApiKeyORM).filter(ApiKeyORM.key_id == "test-key-id").delete()
        db.add(PortalUserORM(
            id=user_id,
            email=f"stream-billing-{user_id[:8]}@test.com",
            password_hash="x",
            credit_balance=100_000,
            credit_used=0,
            plan="free_trial",
            status="active",
            tenant_id="test-tenant",
        ))
        db.flush()
        db.add(ApiKeyORM(
            key_id="test-key-id",
            key_prefix="gw_test_",
            key_hash="h_test-key-id",
            tenant_id="test-tenant",
            project_id="test-project",
            name="stream-billing-test",
            role="user",
            status="active",
            portal_user_id=user_id,
        ))
        db.commit()
    finally:
        db.close()

    yield user_id

    db = _Session()
    try:
        tx_request_ids = [
            tx.request_id
            for tx in db.query(CreditTransactionORM).filter_by(user_id=user_id)
            if tx.request_id
        ]
        db.query(CreditTransactionORM).filter_by(user_id=user_id).delete()
        if tx_request_ids:
            db.query(RequestLogORM).filter(
                RequestLogORM.request_id.in_(tx_request_ids)
            ).delete(synchronize_session=False)
        db.query(ApiKeyORM).filter(ApiKeyORM.key_id == "test-key-id").delete()
        db.query(PortalUserORM).filter_by(id=user_id).delete()
        db.commit()
    finally:
        db.close()


def _user_state(user_id: str) -> tuple[int, int, list[CreditTransactionORM]]:
    db = _Session()
    try:
        user = db.get(PortalUserORM, user_id)
        txs = (
            db.query(CreditTransactionORM)
            .filter_by(user_id=user_id)
            .order_by(CreditTransactionORM.created_at)
            .all()
        )
        return int(user.credit_balance), int(user.credit_used), txs
    finally:
        db.close()


def _log_for(request_id: str) -> RequestLogORM | None:
    db = _Session()
    try:
        return db.get(RequestLogORM, request_id)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_cancel_mid_stream_bills_partial_and_propagates(
    monkeypatch, billable_user
):
    """Client disconnect (CancelledError) mid-stream must bill the
    partial content, write the ledger + request_log, and re-raise the
    cancellation (task teardown must not be swallowed)."""
    gate = asyncio.Event()

    async def _spy(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        for _ in range(3):
            yield ProviderStreamChunk(delta=_CHUNK, model="gpt-4o-mini")
        gate.set()
        # Park forever: the test cancels the request task while the
        # generator awaits here — the exact await-point shape of a
        # real uvicorn client disconnect.
        await asyncio.Event().wait()

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _spy)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        task = asyncio.create_task(
            c.post("/v1/chat/completions", headers=_HEADERS, json=_STREAM_PAYLOAD)
        )
        await asyncio.wait_for(gate.wait(), timeout=15)
        await asyncio.sleep(0.05)  # let emitted chunks settle
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    balance, used, txs = _user_state(billable_user)
    assert len(txs) == 1, "aborted stream must write exactly one ledger row"
    tx = txs[0]
    assert tx.amount < 0, "partial stream must actually deduct"
    assert tx.type == "usage"
    assert balance == 100_000 + tx.amount
    assert used == -tx.amount

    log_row = _log_for(tx.request_id)
    assert log_row is not None, "aborted stream must persist a request_log row"
    assert log_row.completion_tokens > 0, "streamed content must be counted"
    assert log_row.status == "success"


def test_normal_stream_deducts_exactly_once(monkeypatch, billable_user):
    """The wrapper's finally-net must NOT double-charge a stream that
    already finalized on the normal path (``_stream_finalized`` guard)."""

    async def _spy(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        for i in range(3):
            yield ProviderStreamChunk(
                delta=_CHUNK,
                finish_reason="stop" if i == 2 else None,
                model="gpt-4o-mini",
            )

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _spy)

    resp = client.post(
        "/v1/chat/completions", headers=_HEADERS, json=_STREAM_PAYLOAD
    )
    assert resp.status_code == 200
    assert "[DONE]" in resp.text

    balance, used, txs = _user_state(billable_user)
    assert len(txs) == 1, "normal stream must deduct exactly once"
    assert txs[0].amount < 0
    assert balance == 100_000 + txs[0].amount
    assert used == -txs[0].amount


def test_mid_stream_error_after_content_bills_partial(
    monkeypatch, billable_user
):
    """Provider dies after content reached the wire (the
    ``first_chunk_yielded`` → emit error frame → ``return`` path): the
    emitted portion must be billed and the request_log row must record
    ``provider_error``."""

    async def _spy(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        for _ in range(3):
            yield ProviderStreamChunk(delta=_CHUNK, model="gpt-4o-mini")
        raise RuntimeError("provider connection lost mid-stream")

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _spy)

    resp = client.post(
        "/v1/chat/completions", headers=_HEADERS, json=_STREAM_PAYLOAD
    )
    assert resp.status_code == 200  # headers were already on the wire

    balance, used, txs = _user_state(billable_user)
    assert len(txs) == 1, "partial-fail stream must write one ledger row"
    tx = txs[0]
    assert tx.amount < 0, "emitted content must be billed"
    assert balance == 100_000 + tx.amount

    log_row = _log_for(tx.request_id)
    assert log_row is not None
    assert log_row.status == "provider_error"
    assert log_row.completion_tokens > 0


@pytest.mark.asyncio
async def test_cancel_before_any_content_charges_nothing(
    monkeypatch, billable_user
):
    """Cancel while the provider is still 'thinking' (nothing emitted,
    no provider usage): no charge — mirrors the non-stream failure
    posture. Guards against the fix overshooting into overcharging."""
    gate = asyncio.Event()

    async def _spy(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        gate.set()
        await asyncio.Event().wait()  # park before ever yielding
        yield ProviderStreamChunk(delta="unreached", model="gpt-4o-mini")

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _spy)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        task = asyncio.create_task(
            c.post("/v1/chat/completions", headers=_HEADERS, json=_STREAM_PAYLOAD)
        )
        await asyncio.wait_for(gate.wait(), timeout=15)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    balance, used, txs = _user_state(billable_user)
    assert txs == [], "nothing reached the wire — nothing to bill"
    assert balance == 100_000
    assert used == 0
