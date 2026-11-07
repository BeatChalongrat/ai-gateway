"""Per-user token usage attribution (migration 062 sprint).

Covers the four layers the sprint touched:

1. Schema — ``request_logs.user_id`` exists and persists.
2. Attribution plumbing — ``ResolvedApiKey.log_user_id`` resolution
   order and ``RequestContext.user_id`` propagation.
3. ``GET /admin/usage/by-user`` — real aggregation (Strategy A via
   ``request_logs.user_id`` + Strategy B via ``credit_transactions``),
   merge without double-counting, filters, response shape.
4. ``GET /portal/me/usage`` — the api_key_id-join bug fix (rows are
   found via user_id + the legacy credit_transactions fallback).
"""

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import ResolvedApiKey, get_db_session, get_portal_user
from app.domain.observability import RequestRecord
from app.main import app as main_app
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.request_log import RequestLogORM
from app.models.tenant import TenantORM
from app.services.request_log_service import RequestLogService
import app.models  # noqa: F401


# ---------------------------------------------------------------------------
# Unit: resolution order + record mapping
# ---------------------------------------------------------------------------


class TestLogUserIdResolution:
    def test_key_owner_wins_over_trusted_user(self):
        auth = ResolvedApiKey(
            key_id="k1", tenant_id="t", project_id="p", key_prefix="gw_x",
            portal_user_id="trusted-user",
            key_owner_portal_user_id="owner-user",
        )
        assert auth.log_user_id == "owner-user"

    def test_trusted_user_when_key_not_portal_owned(self):
        auth = ResolvedApiKey(
            key_id="k1", tenant_id="t", project_id="p", key_prefix="gw_x",
            portal_user_id="trusted-user",
        )
        assert auth.log_user_id == "trusted-user"

    def test_none_for_admin_tenant_keys(self):
        auth = ResolvedApiKey(
            key_id="k1", tenant_id="t", project_id="p", key_prefix="gw_x",
        )
        assert auth.log_user_id is None


class TestRecordPersistsUserId:
    def test_request_record_user_id_written_to_row(self, db_session):
        tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        db_session.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
        db_session.flush()
        db_session.add(ProjectORM(project_id=pid, tenant_id=tid, name="P"))
        db_session.flush()

        svc = RequestLogService()
        svc.persist(db_session, RequestRecord(
            request_id="req_attr", tenant_id=tid, project_id=pid,
            total_tokens=10, user_id="user-abc",
        ))
        svc.persist(db_session, RequestRecord(
            request_id="req_anon", tenant_id=tid, project_id=pid,
            total_tokens=10,
        ))

        row = db_session.get(RequestLogORM, "req_attr")
        assert row.user_id == "user-abc"
        assert db_session.get(RequestLogORM, "req_anon").user_id is None


# ---------------------------------------------------------------------------
# API fixtures — one tenant/project, two portal users, mixed-era logs
# ---------------------------------------------------------------------------


@pytest.fixture()
def usage_env(sqlite_engine):
    Session = sessionmaker(bind=sqlite_engine)

    def _override():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    main_app.dependency_overrides[get_db_session] = _override

    s = Session()
    tid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    u_new, u_old = str(uuid.uuid4()), str(uuid.uuid4())
    s.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    s.flush()
    s.add(ProjectORM(project_id=pid, tenant_id=tid, name="P"))
    s.add(PortalUserORM(
        id=u_new, email="new@example.com", password_hash="x",
        display_name="New Era", tenant_id=tid,
    ))
    s.add(PortalUserORM(
        id=u_old, email="old@example.com", password_hash="x",
        display_name="Old Era", tenant_id=tid,
    ))
    s.flush()

    svc = RequestLogService()
    # Strategy A rows — post-migration, user_id stamped directly.
    svc.persist(s, RequestRecord(
        request_id="req_a1", tenant_id=tid, project_id=pid,
        prompt_tokens=60, completion_tokens=40, total_tokens=100,
        estimated_cost_usd=0.001, user_id=u_new,
    ))
    svc.persist(s, RequestRecord(
        request_id="req_a2", tenant_id=tid, project_id=pid,
        prompt_tokens=120, completion_tokens=80, total_tokens=200,
        estimated_cost_usd=0.002, user_id=u_new,
    ))
    # Strategy B row — legacy: user_id NULL, credit_transactions carries
    # the attribution.
    svc.persist(s, RequestRecord(
        request_id="req_b1", tenant_id=tid, project_id=pid,
        prompt_tokens=300, completion_tokens=100, total_tokens=400,
        estimated_cost_usd=0.004,
    ))
    s.add(CreditTransactionORM(
        id=str(uuid.uuid4()), user_id=u_old, amount=-400,
        balance_after=0, type="usage", request_id="req_b1",
    ))
    # Legacy row that ALSO has user_id (post-migration billable request)
    # — must count once via Strategy A, not twice.
    svc.persist(s, RequestRecord(
        request_id="req_ab", tenant_id=tid, project_id=pid,
        prompt_tokens=30, completion_tokens=20, total_tokens=50,
        estimated_cost_usd=0.0005, user_id=u_new,
    ))
    s.add(CreditTransactionORM(
        id=str(uuid.uuid4()), user_id=u_new, amount=-50,
        balance_after=0, type="usage", request_id="req_ab",
    ))
    # Unattributed admin-key row — excluded everywhere.
    svc.persist(s, RequestRecord(
        request_id="req_none", tenant_id=tid, project_id=pid,
        total_tokens=999, estimated_cost_usd=0.009,
    ))
    s.commit()
    s.close()

    yield TestClient(main_app), {
        "tenant_id": tid, "project_id": pid,
        "u_new": u_new, "u_old": u_old, "Session": Session,
    }

    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session
    main_app.dependency_overrides.pop(get_portal_user, None)


class TestAdminUsageByUser:
    def test_shape_and_merge(self, usage_env):
        client, env = usage_env
        body = client.get("/admin/usage/by-user").json()
        assert set(body) == {"users", "period", "totalUsers"}
        assert body["totalUsers"] == 2
        by_id = {u["userId"]: u for u in body["users"]}

        new = by_id[env["u_new"]]
        # Strategy A only (req_a1 + req_a2 + req_ab) — req_ab must not
        # double-count through its credit_transactions row.
        assert new["requestCount"] == 3
        assert new["totalTokens"] == 350
        assert new["promptTokens"] == 210
        assert new["completionTokens"] == 140
        assert new["displayName"] == "New Era"
        assert new["email"] == "new@example.com"
        assert new["lastActive"] is not None

        old = by_id[env["u_old"]]
        assert old["requestCount"] == 1
        assert old["totalTokens"] == 400

        # Sorted by tokens desc: u_old (400) first.
        assert body["users"][0]["userId"] == env["u_old"]

    def test_unattributed_rows_excluded(self, usage_env):
        client, _ = usage_env
        body = client.get("/admin/usage/by-user").json()
        total = sum(u["totalTokens"] for u in body["users"])
        assert total == 750  # 350 + 400 — the 999-token admin row absent

    def test_project_filter(self, usage_env):
        client, env = usage_env
        body = client.get(
            f"/admin/usage/by-user?projectId={env['project_id']}"
        ).json()
        assert body["totalUsers"] == 2
        body2 = client.get("/admin/usage/by-user?projectId=other").json()
        assert body2["totalUsers"] == 0

    def test_date_filter_excludes_out_of_window(self, usage_env):
        client, _ = usage_env
        past = (datetime.utcnow() - timedelta(days=90)).isoformat()
        past_end = (datetime.utcnow() - timedelta(days=60)).isoformat()
        body = client.get(
            f"/admin/usage/by-user?start_date={past}&end_date={past_end}"
        ).json()
        assert body["totalUsers"] == 0


class TestPortalMeUsage:
    def _login_as(self, env, user_id):
        Session = env["Session"]
        s = Session()
        user = s.get(PortalUserORM, user_id)
        s.close()
        main_app.dependency_overrides[get_portal_user] = lambda: user
        return user

    def test_returns_rows_for_user_id_column(self, usage_env):
        client, env = usage_env
        self._login_as(env, env["u_new"])
        body = client.get("/portal/me/usage").json()
        # req_a1, req_a2, req_ab — the bug fix means this is no longer
        # silently empty.
        assert body["total"] == 3
        ids = {r["id"] for r in body["requests"]}
        assert ids == {"req_a1", "req_a2", "req_ab"}

    def test_legacy_rows_via_credit_transactions(self, usage_env):
        client, env = usage_env
        self._login_as(env, env["u_old"])
        body = client.get("/portal/me/usage").json()
        assert body["total"] == 1
        assert body["requests"][0]["id"] == "req_b1"
