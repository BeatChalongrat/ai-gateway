"""Stage 6 tests: alert rule CRUD + feedback endpoint."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db_session
from app.main import app as main_app
from app.models.request_log import RequestLogORM
import app.models  # noqa: F401


@pytest.fixture()
def admin_client(sqlite_engine):
    Session = sessionmaker(bind=sqlite_engine)

    def _override():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    main_app.dependency_overrides[get_db_session] = _override
    yield TestClient(main_app)
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


# ---------------------------------------------------------------------------
# Alert rule CRUD
# ---------------------------------------------------------------------------


class TestAlertCreate:
    def test_create_returns_201(self, admin_client):
        resp = admin_client.post(
            "/admin/alerts",
            json={
                "name": "High Error Rate",
                "metric": "error_rate",
                "threshold": 0.1,
                "window_minutes": 5,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "High Error Rate"
        assert body["metric"] == "error_rate"
        assert body["threshold"] == 0.1
        assert body["scope"] == "global"
        assert body["enabled"] is True
        assert "id" in body

    def test_create_with_scoped_rule(self, admin_client):
        resp = admin_client.post(
            "/admin/alerts",
            json={
                "name": "Tenant budget",
                "metric": "cost_per_hour",
                "threshold": 50.0,
                "scope": "tenant",
                "scope_id": "acme-001",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["scope"] == "tenant"
        assert resp.json()["scope_id"] == "acme-001"

    def test_rejects_unknown_metric(self, admin_client):
        resp = admin_client.post(
            "/admin/alerts",
            json={"name": "x", "metric": "bogus", "threshold": 1},
        )
        assert resp.status_code == 422  # ValidationError → 422

    def test_rejects_unknown_scope(self, admin_client):
        resp = admin_client.post(
            "/admin/alerts",
            json={
                "name": "x",
                "metric": "error_rate",
                "threshold": 1,
                "scope": "nonsense",
            },
        )
        assert resp.status_code == 422


class TestAlertList:
    def test_empty_list(self, admin_client):
        resp = admin_client.get("/admin/alerts")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_after_create(self, admin_client):
        admin_client.post(
            "/admin/alerts",
            json={"name": "a", "metric": "error_rate", "threshold": 0.1},
        )
        admin_client.post(
            "/admin/alerts",
            json={"name": "b", "metric": "requests_per_minute", "threshold": 1000},
        )
        resp = admin_client.get("/admin/alerts")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        names = {i["name"] for i in items}
        assert names == {"a", "b"}


class TestAlertDetail:
    def test_get_by_id(self, admin_client):
        created = admin_client.post(
            "/admin/alerts",
            json={"name": "x", "metric": "error_rate", "threshold": 0.1},
        ).json()
        resp = admin_client.get(f"/admin/alerts/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_missing_returns_404(self, admin_client):
        resp = admin_client.get("/admin/alerts/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestAlertUpdate:
    def test_partial_update(self, admin_client):
        created = admin_client.post(
            "/admin/alerts",
            json={"name": "x", "metric": "error_rate", "threshold": 0.1},
        ).json()
        resp = admin_client.put(
            f"/admin/alerts/{created['id']}",
            json={"threshold": 0.2, "enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["threshold"] == 0.2
        assert resp.json()["enabled"] is False
        # Unchanged fields preserved
        assert resp.json()["name"] == "x"
        assert resp.json()["metric"] == "error_rate"

    def test_update_missing_returns_404(self, admin_client):
        resp = admin_client.put(
            "/admin/alerts/00000000-0000-0000-0000-000000000000",
            json={"enabled": False},
        )
        assert resp.status_code == 404

    def test_update_rejects_bad_metric(self, admin_client):
        created = admin_client.post(
            "/admin/alerts",
            json={"name": "x", "metric": "error_rate", "threshold": 0.1},
        ).json()
        resp = admin_client.put(
            f"/admin/alerts/{created['id']}",
            json={"metric": "not-a-metric"},
        )
        assert resp.status_code == 422


class TestAlertDelete:
    def test_delete_returns_204(self, admin_client):
        created = admin_client.post(
            "/admin/alerts",
            json={"name": "x", "metric": "error_rate", "threshold": 0.1},
        ).json()
        resp = admin_client.delete(f"/admin/alerts/{created['id']}")
        assert resp.status_code == 204
        # Confirm gone
        assert admin_client.get(f"/admin/alerts/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, admin_client):
        resp = admin_client.delete("/admin/alerts/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Feedback endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def feedback_client(sqlite_engine):
    """Client that seeds a RequestLogORM row we can attach feedback to."""
    Session = sessionmaker(bind=sqlite_engine)

    # Seed a request log belonging to test-tenant (the conftest fake-auth tenant)
    session = Session()
    session.add(
        RequestLogORM(
            request_id="req_seeded_1",
            tenant_id="test-tenant",
            project_id="test-project",
            provider="openai",
            model="gpt-4o-mini",
            status="success",
            policy_action="allow",
            latency_ms=100,
        )
    )
    session.add(
        RequestLogORM(
            request_id="req_other_tenant",
            tenant_id="other-tenant",
            project_id="other-project",
            provider="openai",
            model="gpt-4o-mini",
            status="success",
            policy_action="allow",
            latency_ms=100,
        )
    )
    session.commit()
    session.close()

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
    yield TestClient(main_app)
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


_HEADERS = {"Authorization": "Bearer demo-key"}


class TestFeedbackPost:
    def test_thumbs_up_recorded(self, feedback_client):
        resp = feedback_client.post(
            "/v1/feedback",
            headers=_HEADERS,
            json={"request_id": "req_seeded_1", "rating": 1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] == "req_seeded_1"
        assert body["rating"] == 1
        assert "recorded_at" in body

    def test_thumbs_down_with_comment(self, feedback_client):
        resp = feedback_client.post(
            "/v1/feedback",
            headers=_HEADERS,
            json={
                "request_id": "req_seeded_1",
                "rating": -1,
                "comment": "wrong answer",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["rating"] == -1

    def test_rejects_invalid_rating_value(self, feedback_client):
        resp = feedback_client.post(
            "/v1/feedback",
            headers=_HEADERS,
            json={"request_id": "req_seeded_1", "rating": 5},
        )
        assert resp.status_code == 422

    def test_missing_request_returns_404(self, feedback_client):
        resp = feedback_client.post(
            "/v1/feedback",
            headers=_HEADERS,
            json={"request_id": "req_does_not_exist", "rating": 1},
        )
        assert resp.status_code == 404

    def test_cross_tenant_feedback_blocked(self, feedback_client):
        # The conftest mock auth has role="admin" (global admin) which can
        # access any tenant. To exercise the tenant isolation code path we
        # override the auth dependency with a tenant-scoped user just for
        # this test.
        from app.api.deps import ResolvedApiKey, get_resolved_api_key

        tenant_scoped = ResolvedApiKey(
            key_id="k",
            tenant_id="test-tenant",
            project_id="test-project",
            key_prefix="gw_",
            role="project_admin",
        )
        original = main_app.dependency_overrides.get(get_resolved_api_key)
        main_app.dependency_overrides[get_resolved_api_key] = lambda: tenant_scoped
        try:
            resp = feedback_client.post(
                "/v1/feedback",
                headers=_HEADERS,
                json={"request_id": "req_other_tenant", "rating": 1},
            )
        finally:
            if original is not None:
                main_app.dependency_overrides[get_resolved_api_key] = original
        assert resp.status_code == 403
