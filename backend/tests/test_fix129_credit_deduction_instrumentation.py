"""FIX129 A2 — the credit deduction is instrumented, not guessed at.

The transaction that wedged the live system could not be reproduced (135
aborts). So every deduction now: sets its own SET LOCAL lock / idle limits
(Postgres only), counts itself in/out of an in-flight gauge, observes its
duration and lock wait, and logs one ``credit_deduction`` line with the
request id — WARNING when slow or not ok.
"""

from __future__ import annotations

import uuid

import pytest
import structlog
from sqlalchemy.orm import sessionmaker

from app.models.portal_user import PortalUserORM
from app.observability.metrics import (
    CREDIT_DEDUCTION_INFLIGHT,
    CREDIT_DEDUCTION_TOTAL,
)
from app.services.gateway import portal_credit_check as pcc
from tests.conftest import _integration_engine

_Session = sessionmaker(bind=_integration_engine)


def _user(balance: int = 1_000) -> str:
    uid = str(uuid.uuid4())
    db = _Session()
    try:
        db.add(
            PortalUserORM(
                id=uid,
                email=f"fix129-{uid[:8]}@test.local",
                password_hash="x",
                credit_balance=balance,
                credit_used=0,
                credit_quota=balance,
                tenant_id="test-tenant",
                tenant_role="member",
                token_version=1,
            )
        )
        db.commit()
    finally:
        db.close()
    return uid


def _counter(outcome: str) -> float:
    return CREDIT_DEDUCTION_TOTAL.labels(outcome=outcome)._value.get()


@pytest.mark.asyncio
async def test_deduction_logs_request_id_and_counts_outcome(async_db):
    uid = _user()
    before_ok = _counter("ok")
    with structlog.testing.capture_logs() as logs:
        await pcc.deduct_portal_credit_async(
            async_db,
            "no-such-key",
            "req-fix129-1",
            cost_usd=0.01,
            tokens_used=10,
            portal_user_id=uid,
        )
    line = [l for l in logs if l.get("event") == "credit_deduction"]
    assert len(line) == 1, logs
    assert line[0]["request_id"] == "req-fix129-1"
    assert line[0]["outcome"] == "ok"
    assert line[0]["log_level"] == "info"
    assert line[0]["credits"] >= 1
    assert "elapsed_ms" in line[0] and "lock_wait_ms" in line[0]
    assert _counter("ok") == before_ok + 1
    # The in-flight gauge is back to zero once the deduction returns.
    assert CREDIT_DEDUCTION_INFLIGHT._value.get() == 0


@pytest.mark.asyncio
async def test_slow_deduction_is_a_warning(async_db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "credit_deduction_slow_seconds", 0.0)
    uid = _user()
    before_slow = _counter("slow")
    with structlog.testing.capture_logs() as logs:
        await pcc.deduct_portal_credit_async(
            async_db, "no-such-key", "req-fix129-slow", cost_usd=0.01, portal_user_id=uid
        )
    line = [l for l in logs if l.get("event") == "credit_deduction"][0]
    assert line["log_level"] == "warning"
    assert line["slow"] is True
    assert _counter("slow") == before_slow + 1


@pytest.mark.asyncio
async def test_transaction_limits_are_set_local_on_postgres_only():
    executed: list[str] = []

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Session:
        def get_bind(self):
            return _Bind()

        async def execute(self, stmt):
            executed.append(str(stmt))

    await pcc._set_transaction_limits(_Session())
    assert any("SET LOCAL lock_timeout = 5000" in s for s in executed), executed
    assert any(
        "SET LOCAL idle_in_transaction_session_timeout = 15000" in s for s in executed
    ), executed

    executed.clear()
    _Dialect.name = "sqlite"
    await pcc._set_transaction_limits(_Session())
    assert executed == []


def test_sync_variant_is_the_streamed_turn_path_and_is_observed_too():
    """public_chat's stream finaliser calls the SYNC deduct_portal_credit."""
    uid = _user()
    before_ok = _counter("ok")
    db = _Session()
    try:
        with structlog.testing.capture_logs() as logs:
            pcc.deduct_portal_credit(
                db, "no-such-key", "req-fix129-sync", cost_usd=0.02, tokens_used=20, portal_user_id=uid
            )
        db.commit()
    finally:
        db.close()
    line = [l for l in logs if l.get("event") == "credit_deduction"]
    assert len(line) == 1, logs
    assert line[0]["request_id"] == "req-fix129-sync" and line[0]["outcome"] == "ok"
    assert line[0]["credits"] >= 1
    assert _counter("ok") == before_ok + 1


def test_sync_lock_timeout_is_named_and_counted():
    class LockNotAvailable(Exception):
        pass

    class _Session:
        def get_bind(self):
            raise RuntimeError("no bind")

    before = _counter("lock_timeout")
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(LockNotAvailable):
            with pcc._DeductionSpan("req-x", "user-x") as span:
                span.lock_start()
                raise LockNotAvailable("canceling statement due to lock timeout")
    line = [l for l in logs if l.get("event") == "credit_deduction"][0]
    assert line["outcome"] == "lock_timeout" and line["log_level"] == "warning"
    assert "LockNotAvailable" in line["error"]
    assert _counter("lock_timeout") == before + 1
    assert CREDIT_DEDUCTION_INFLIGHT._value.get() == 0


def test_sync_transaction_limits_are_set_local_on_postgres_only():
    executed: list[str] = []

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Session:
        def get_bind(self):
            return _Bind()

        def execute(self, stmt):
            executed.append(str(stmt))

    pcc._set_transaction_limits_sync(_Session())
    assert [s for s in executed if "SET LOCAL" in s] == [
        "SET LOCAL lock_timeout = 5000",
        "SET LOCAL idle_in_transaction_session_timeout = 15000",
    ]


def test_lock_timeout_is_recognised_by_sqlstate():
    class _Orig(Exception):
        sqlstate = "55P03"

    class _Wrapped(Exception):
        def __init__(self):
            super().__init__("wrapped")
            self.orig = _Orig()

    assert pcc._is_lock_timeout(_Wrapped())

    class LockNotAvailable(Exception):  # psycopg's class name, no sqlstate attr
        pass

    assert pcc._is_lock_timeout(LockNotAvailable())
    assert not pcc._is_lock_timeout(RuntimeError("x"))
