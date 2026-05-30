"""Phase 4 Feature 1 — usage summary aggregation tests.

Verifies /portal/me/usage/summary returns tenant-scoped totals,
time-window counts, top models, and per-project breakdown.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.request_log import RequestLogORM
from app.models.system_setting import SystemSettingORM
from app.models.tenant import TenantORM
from app.models.tenant_invite import TenantInviteORM
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    try:
        db.query(RequestLogORM).delete(synchronize_session=False)
        db.query(TenantInviteORM).delete(synchronize_session=False)
        db.query(ApiKeyORM).filter(
            ApiKeyORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(ProjectORM).filter(
            ProjectORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(CreditTransactionORM).delete(synchronize_session=False)
        db.query(PortalUserORM).delete(synchronize_session=False)
        db.query(TenantORM).filter(
            ~TenantORM.tenant_id.in_(["test-tenant"])
        ).delete(synchronize_session=False)
        db.query(SystemSettingORM).delete(synchronize_session=False)
        db.add_all(
            [
                SystemSettingORM(key="free_trial_tokens", value="100000"),
                SystemSettingORM(key="portal_signup_enabled", value="true"),
            ]
        )
        db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _signup(client, email: str) -> dict:
    r = client.post(
        "/portal/auth/signup",
        json={"email": email, "password": "Test1234!"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # HttpOnly migration: tokens are cookies. Capture, then clear the
    # shared jar so multi-user tests auth via the explicit Bearer header.
    body["access_token"] = r.cookies.get("portal_access_token")
    body["refresh_token"] = r.cookies.get("portal_refresh_token")
    client.cookies.clear()
    return body


def _seed_logs_for_tenant(
    tenant_id: str,
    project_specs: list[tuple[str, str, list[tuple[str, int, float]]]],
):
    """Bulk-insert request_log rows.

    ``project_specs`` is a list of (project_id, project_name, entries)
    where ``entries`` is [(model, tokens, age_hours), ...].
    """
    db = _Session()
    try:
        now = datetime.now(timezone.utc)
        for pid, pname, entries in project_specs:
            # Insert the project if it doesn't exist.
            if db.get(ProjectORM, pid) is None:
                db.add(
                    ProjectORM(
                        project_id=pid,
                        tenant_id=tenant_id,
                        name=pname,
                        policy_profile_id="default",
                    )
                )
            for idx, (model, tokens, age_hours) in enumerate(entries):
                db.add(
                    RequestLogORM(
                        request_id=f"r-{pid}-{idx}",
                        tenant_id=tenant_id,
                        project_id=pid,
                        provider="openai",
                        model=model,
                        status="success",
                        policy_action="allow",
                        total_tokens=tokens,
                        prompt_tokens=tokens // 2,
                        completion_tokens=tokens - tokens // 2,
                        estimated_cost_usd=tokens * 0.00001,
                        created_at=now - timedelta(hours=age_hours),
                    )
                )
        db.commit()
    finally:
        db.close()


class TestUsageSummary:
    def test_empty_tenant_returns_zeros(self, client):
        a = _signup(client, "empty@t.com")
        r = client.get(
            "/portal/me/usage/summary",
            headers={"Authorization": f"Bearer {a['access_token']}"},
        )
        assert r.status_code == 200
        body = r.json()
        # ``estimated_cost_thb`` + ``cost_display`` were added in the
        # THB display-layer pass (May 2026). USD totals stay the
        # canonical numeric; the test asserts the *full* shape.
        assert body["totals"] == {
            "requests": 0,
            "tokens": 0,
            "estimated_cost_usd": 0.0,
            "estimated_cost_thb": 0.0,
            "cost_display": "฿0.0000",
        }
        assert body["top_models"] == []
        assert body["by_project"] == []

    def test_totals_and_windows_aggregate_correctly(self, client):
        a = _signup(client, "agg@t.com")
        db = _Session()
        try:
            user = db.get(PortalUserORM, a["user"]["id"])
            tenant_id = user.tenant_id
        finally:
            db.close()
        _seed_logs_for_tenant(
            tenant_id,
            [
                (
                    "proj-1",
                    "Main",
                    [
                        # 3 entries today, 1 last week, 1 last month
                        ("gpt-4o", 100, 1),
                        ("gpt-4o", 200, 2),
                        ("claude-3", 50, 5),
                        ("gpt-4o", 80, 48),  # 2 days ago
                        ("claude-3", 40, 20 * 24),  # 20 days ago
                    ],
                ),
            ],
        )
        r = client.get(
            "/portal/me/usage/summary",
            headers={"Authorization": f"Bearer {a['access_token']}"},
        )
        body = r.json()
        assert body["totals"]["requests"] == 5
        assert body["totals"]["tokens"] == 470
        assert body["windows"]["today"] == 3
        assert body["windows"]["last_7d"] == 4
        assert body["windows"]["last_30d"] == 5

    def test_top_models_sorted_by_request_count(self, client):
        a = _signup(client, "models@t.com")
        db = _Session()
        try:
            tenant_id = db.get(PortalUserORM, a["user"]["id"]).tenant_id
        finally:
            db.close()
        _seed_logs_for_tenant(
            tenant_id,
            [
                (
                    "proj-m",
                    "Main",
                    [
                        ("gpt-4o", 10, 1),
                        ("gpt-4o", 10, 2),
                        ("gpt-4o", 10, 3),
                        ("claude-3", 10, 4),
                        ("claude-3", 10, 5),
                        ("gemini", 10, 6),
                    ],
                ),
            ],
        )
        r = client.get(
            "/portal/me/usage/summary",
            headers={"Authorization": f"Bearer {a['access_token']}"},
        )
        models = r.json()["top_models"]
        assert [m["model"] for m in models] == ["gpt-4o", "claude-3", "gemini"]
        assert models[0]["requests"] == 3

    def test_per_project_breakdown_with_friendly_names(self, client):
        a = _signup(client, "byproj@t.com")
        db = _Session()
        try:
            tenant_id = db.get(PortalUserORM, a["user"]["id"]).tenant_id
        finally:
            db.close()
        _seed_logs_for_tenant(
            tenant_id,
            [
                ("p-alpha", "Alpha", [("gpt-4o", 100, 1), ("gpt-4o", 100, 2)]),
                ("p-beta", "Beta", [("claude-3", 50, 1)]),
            ],
        )
        r = client.get(
            "/portal/me/usage/summary",
            headers={"Authorization": f"Bearer {a['access_token']}"},
        )
        rows = r.json()["by_project"]
        by_name = {row["project_name"]: row for row in rows}
        assert by_name["Alpha"]["requests"] == 2
        assert by_name["Alpha"]["tokens"] == 200
        assert by_name["Beta"]["requests"] == 1

    def test_tenant_isolation(self, client):
        """Tenant A's usage must not include tenant B's rows."""
        a = _signup(client, "a-iso@t.com")
        b = _signup(client, "b-iso@t.com")
        db = _Session()
        try:
            tid_a = db.get(PortalUserORM, a["user"]["id"]).tenant_id
            tid_b = db.get(PortalUserORM, b["user"]["id"]).tenant_id
        finally:
            db.close()
        _seed_logs_for_tenant(tid_a, [("p-a", "A", [("m", 10, 1)] * 3)])
        _seed_logs_for_tenant(tid_b, [("p-b", "B", [("m", 10, 1)] * 5)])
        r_a = client.get(
            "/portal/me/usage/summary",
            headers={"Authorization": f"Bearer {a['access_token']}"},
        ).json()
        r_b = client.get(
            "/portal/me/usage/summary",
            headers={"Authorization": f"Bearer {b['access_token']}"},
        ).json()
        assert r_a["totals"]["requests"] == 3
        assert r_b["totals"]["requests"] == 5
        # Project names shouldn't leak either.
        assert all(p["project_name"] != "B" for p in r_a["by_project"])
        assert all(p["project_name"] != "A" for p in r_b["by_project"])
