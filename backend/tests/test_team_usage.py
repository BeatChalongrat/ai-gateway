"""GET /portal/me/team/usage — per-member usage for the dashboard
Team usage section.

Covers:

1. Role gate — plain members get 403; owner and admin pass.
2. Aggregation — Strategy A (request_logs.user_id) + Strategy B
   (legacy credit_transactions join) merged without double-counting,
   sharePercent math, tokens-desc ordering.
3. Membership scoping — zero-usage members included, inactive members
   and other-tenant users excluded.
4. ``period`` filter narrows the window.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db_session, get_portal_user
from app.domain.observability import RequestRecord
from app.main import app as main_app
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.request_log import RequestLogORM
from app.models.tenant import TenantORM
from app.services.request_log_service import RequestLogService
import app.models  # noqa: F401


@pytest.fixture()
def team_env(sqlite_engine):
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
    other_tid = str(uuid.uuid4())
    owner_id, member_id, idle_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    inactive_id, outsider_id = str(uuid.uuid4()), str(uuid.uuid4())

    s.add(TenantORM(tenant_id=tid, name="T", slug=f"t-{tid[:8]}"))
    s.add(TenantORM(tenant_id=other_tid, name="O", slug=f"o-{other_tid[:8]}"))
    s.flush()
    s.add(ProjectORM(project_id=pid, tenant_id=tid, name="P"))
    s.add(PortalUserORM(
        id=owner_id, email="owner@example.com", password_hash="x",
        display_name="Team Owner", tenant_id=tid, tenant_role="owner",
    ))
    s.add(PortalUserORM(
        id=member_id, email="member@example.com", password_hash="x",
        display_name="Busy Member", tenant_id=tid, tenant_role="member",
    ))
    s.add(PortalUserORM(
        id=idle_id, email="idle@example.com", password_hash="x",
        display_name="Idle Member", tenant_id=tid, tenant_role="member",
    ))
    s.add(PortalUserORM(
        id=inactive_id, email="gone@example.com", password_hash="x",
        display_name="Removed", tenant_id=tid, tenant_role="member",
        status="suspended",
    ))
    s.add(PortalUserORM(
        id=outsider_id, email="out@example.com", password_hash="x",
        display_name="Outsider", tenant_id=other_tid, tenant_role="owner",
    ))
    s.flush()

    svc = RequestLogService()
    # Owner: Strategy A rows (600 tokens) + a legacy Strategy B row
    # (200 tokens) — merged total 800.
    svc.persist(s, RequestRecord(
        request_id="tm_a1", tenant_id=tid, project_id=pid,
        total_tokens=600, user_id=owner_id,
    ))
    svc.persist(s, RequestRecord(
        request_id="tm_b1", tenant_id=tid, project_id=pid,
        total_tokens=200,
    ))
    s.add(CreditTransactionORM(
        id=str(uuid.uuid4()), user_id=owner_id, amount=-200,
        balance_after=0, type="usage", request_id="tm_b1",
    ))
    # Owner: a post-migration billable row that ALSO has a credit tx —
    # must count once (Strategy A), not twice.
    svc.persist(s, RequestRecord(
        request_id="tm_ab", tenant_id=tid, project_id=pid,
        total_tokens=100, user_id=owner_id,
    ))
    s.add(CreditTransactionORM(
        id=str(uuid.uuid4()), user_id=owner_id, amount=-100,
        balance_after=0, type="usage", request_id="tm_ab",
    ))
    # Member: one Strategy A row.
    svc.persist(s, RequestRecord(
        request_id="tm_m1", tenant_id=tid, project_id=pid,
        total_tokens=100, user_id=member_id,
    ))
    # Suspended user and outsider both have usage — must not appear.
    svc.persist(s, RequestRecord(
        request_id="tm_x1", tenant_id=tid, project_id=pid,
        total_tokens=999, user_id=inactive_id,
    ))
    svc.persist(s, RequestRecord(
        request_id="tm_x2", tenant_id=tid, project_id=pid,
        total_tokens=999, user_id=outsider_id,
    ))
    s.commit()
    s.close()

    def _login_as(user_id):
        sess = Session()
        user = sess.get(PortalUserORM, user_id)
        sess.close()
        main_app.dependency_overrides[get_portal_user] = lambda: user
        return user

    yield TestClient(main_app), {
        "owner": owner_id, "member": member_id, "idle": idle_id,
        "login_as": _login_as, "Session": Session,
    }

    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session
    main_app.dependency_overrides.pop(get_portal_user, None)


class TestTeamUsageRoleGate:
    def test_member_gets_403(self, team_env):
        client, env = team_env
        env["login_as"](env["member"])
        resp = client.get("/portal/me/team/usage")
        assert resp.status_code == 403

    def test_owner_allowed(self, team_env):
        client, env = team_env
        env["login_as"](env["owner"])
        assert client.get("/portal/me/team/usage").status_code == 200

    def test_admin_allowed(self, team_env):
        client, env = team_env
        # Promote the member row to admin in-DB, then log in as them.
        s = env["Session"]()
        s.get(PortalUserORM, env["member"]).tenant_role = "admin"
        s.commit()
        s.close()
        env["login_as"](env["member"])
        assert client.get("/portal/me/team/usage").status_code == 200


class TestTeamUsageAggregation:
    def test_merge_share_and_ordering(self, team_env):
        client, env = team_env
        env["login_as"](env["owner"])
        body = client.get("/portal/me/team/usage").json()

        assert set(body) == {"members", "teamTotal", "memberCount"}
        # owner 900 + member 100; suspended/outsider excluded.
        assert body["teamTotal"] == 1000
        assert body["memberCount"] == 3  # owner + member + idle

        rows = body["members"]
        assert [r["userId"] for r in rows[:2]] == [env["owner"], env["member"]]

        owner = rows[0]
        # tm_a1 (600) + tm_ab (100, counted once) + tm_b1 (200 legacy).
        assert owner["totalTokens"] == 900
        assert owner["requestCount"] == 3
        assert owner["sharePercent"] == 90  # round(900/1000*100)
        assert owner["tenantRole"] == "owner"
        assert owner["displayName"] == "Team Owner"
        assert owner["lastActive"] is not None

        member = rows[1]
        assert member["totalTokens"] == 100
        assert member["sharePercent"] == 10

        idle = rows[2]
        assert idle["userId"] == env["idle"]
        assert idle["totalTokens"] == 0
        assert idle["requestCount"] == 0
        assert idle["sharePercent"] == 0
        assert idle["lastActive"] is None

    def test_period_filter(self, team_env):
        client, env = team_env
        env["login_as"](env["owner"])

        # Age the member's row out of the 7d window.
        s = env["Session"]()
        row = s.get(RequestLogORM, "tm_m1")
        row.created_at = datetime.utcnow() - timedelta(days=14)
        s.commit()
        s.close()

        body = client.get("/portal/me/team/usage?period=7d").json()
        by_id = {r["userId"]: r for r in body["members"]}
        assert by_id[env["member"]]["totalTokens"] == 0
        assert by_id[env["owner"]]["totalTokens"] == 900
        assert body["teamTotal"] == 900

        # ``all`` still sees it.
        body_all = client.get("/portal/me/team/usage?period=all").json()
        assert body_all["teamTotal"] == 1000

    def test_bad_period_rejected(self, team_env):
        client, env = team_env
        env["login_as"](env["owner"])
        assert client.get("/portal/me/team/usage?period=90d").status_code == 422
