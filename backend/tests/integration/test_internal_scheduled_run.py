"""FIX13 — internal scheduled-run authorizer.

Covers the pure decision function AND the internal-secret-gated endpoint:

  * active + funded          -> allow  (reason "ok")
  * status != "active"       -> deny   (reason "revoked")
  * zero balance             -> deny   (reason "no_balance")
  * missing / wrong-tenant   -> deny   (reason "revoked")
  * untrusted caller         -> 401    (never reaches the decision)

The endpoint authorizes an ARBITRARY (user_id, tenant_id) from the body, so
the caller identity is only used for the internal-trust gate.
"""

import contextlib
import uuid

import pytest
from starlette.testclient import TestClient

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.api.routes.internal_scheduled_run import decide_scheduled_run
from app.main import app as main_app
from app.models.portal_user import PortalUserORM


@contextlib.contextmanager
def _auth(*, trusted=True, tenant_id="test-tenant"):
    prev = main_app.dependency_overrides.get(get_resolved_api_key)
    main_app.dependency_overrides[get_resolved_api_key] = lambda: ResolvedApiKey(
        key_id="test-key-id",
        tenant_id=tenant_id,
        project_id="test-project",
        key_prefix="gw_test_",
        role="admin",
        internal_trusted=trusted,
    )
    try:
        yield
    finally:
        if prev is not None:
            main_app.dependency_overrides[get_resolved_api_key] = prev
        else:
            main_app.dependency_overrides.pop(get_resolved_api_key, None)


async def _seed(async_db, *, status="active", balance=5_000_000, tenant_id="test-tenant"):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(
        PortalUserORM(
            id=uid, email=f"{uid}@e.com", password_hash="x",
            status=status, credit_balance=balance, tenant_id=tenant_id,
        )
    )
    await async_db.commit()
    return uid


# ---- the pure decision (no I/O) -------------------------------------------

def _user(*, status="active", balance=100, tenant_id="test-tenant"):
    return PortalUserORM(
        id="u1", email="u1@e.com", password_hash="x",
        status=status, credit_balance=balance, tenant_id=tenant_id,
    )


def test_decide_active_funded_allows():
    allow, reason = decide_scheduled_run(_user(), "test-tenant")
    assert allow is True and reason == "ok"


def test_decide_non_active_is_revoked():
    allow, reason = decide_scheduled_run(_user(status="suspended"), "test-tenant")
    assert allow is False and reason == "revoked"
    # revoked outranks a zero balance — a departed user is refused first.
    allow, reason = decide_scheduled_run(
        _user(status="deleted", balance=0), "test-tenant"
    )
    assert allow is False and reason == "revoked"


def test_decide_zero_balance_is_no_balance():
    allow, reason = decide_scheduled_run(_user(balance=0), "test-tenant")
    assert allow is False and reason == "no_balance"


def test_decide_missing_or_wrong_tenant_is_revoked():
    assert decide_scheduled_run(None, "test-tenant") == (False, "revoked")
    assert decide_scheduled_run(_user(tenant_id="other"), "test-tenant") == (
        False,
        "revoked",
    )


# ---- the endpoint (internal-secret gated) ---------------------------------

@pytest.mark.asyncio
async def test_endpoint_active_funded_allows(async_db):
    uid = await _seed(async_db)
    with _auth():
        r = TestClient(main_app).post(
            "/internal/scheduled-run/authorize",
            json={"user_id": uid, "tenant_id": "test-tenant"},
        )
    assert r.status_code == 200
    assert r.json() == {"allow": True, "reason": "ok"}


@pytest.mark.asyncio
async def test_endpoint_revoked_user(async_db):
    uid = await _seed(async_db, status="suspended")
    with _auth():
        r = TestClient(main_app).post(
            "/internal/scheduled-run/authorize",
            json={"user_id": uid, "tenant_id": "test-tenant"},
        )
    assert r.status_code == 200
    assert r.json() == {"allow": False, "reason": "revoked"}


@pytest.mark.asyncio
async def test_endpoint_no_balance(async_db):
    uid = await _seed(async_db, balance=0)
    with _auth():
        r = TestClient(main_app).post(
            "/internal/scheduled-run/authorize",
            json={"user_id": uid, "tenant_id": "test-tenant"},
        )
    assert r.status_code == 200
    assert r.json() == {"allow": False, "reason": "no_balance"}


@pytest.mark.asyncio
async def test_endpoint_untrusted_caller_401(async_db):
    uid = await _seed(async_db)
    with _auth(trusted=False):
        r = TestClient(main_app).post(
            "/internal/scheduled-run/authorize",
            json={"user_id": uid, "tenant_id": "test-tenant"},
        )
    assert r.status_code == 401
