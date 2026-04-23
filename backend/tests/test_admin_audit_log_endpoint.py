"""Phase 4 Feature 4 backend — /admin/audit-log endpoint tests."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.admin_audit_log import AdminAuditLogORM
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean_audit():
    db = _Session()
    try:
        db.query(AdminAuditLogORM).delete(synchronize_session=False)
        db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _seed(
    *,
    tenant_id: str = "tenant-a",
    actor: str = "actor-1",
    action: str = "create_api_key",
    resource_type: str = "api_key",
    resource_id: str = "key-1",
    outcome: str | None = "success",
    ts: datetime | None = None,
    details: str | None = None,
) -> AdminAuditLogORM:
    db = _Session()
    try:
        row = AdminAuditLogORM(
            actor_key_id=actor,
            tenant_id=tenant_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details,
        )
        if ts is not None:
            row.timestamp = ts
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


class TestAuditLogList:
    def test_empty_returns_zero_total(self, client):
        r = client.get(
            "/admin/audit-log",
            headers={"Authorization": "Bearer gw_test_"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_all_rows_returned_sorted_desc_by_timestamp(self, client):
        now = datetime.now(timezone.utc)
        _seed(resource_id="r-old", ts=now - timedelta(hours=2))
        _seed(resource_id="r-new", ts=now)
        _seed(resource_id="r-mid", ts=now - timedelta(hours=1))

        r = client.get(
            "/admin/audit-log",
            headers={"Authorization": "Bearer gw_test_"},
        )
        body = r.json()
        assert [row["resource_id"] for row in body["items"]] == [
            "r-new",
            "r-mid",
            "r-old",
        ]
        assert body["total"] == 3

    def test_tenant_filter_narrows_results(self, client):
        _seed(tenant_id="tenant-a", resource_id="a1")
        _seed(tenant_id="tenant-b", resource_id="b1")
        r = client.get(
            "/admin/audit-log?tenant_id=tenant-a",
            headers={"Authorization": "Bearer gw_test_"},
        )
        body = r.json()
        assert [row["resource_id"] for row in body["items"]] == ["a1"]

    def test_action_filter(self, client):
        _seed(action="create_api_key", resource_id="c1")
        _seed(action="revoke_api_key", resource_id="r1")
        r = client.get(
            "/admin/audit-log?action=revoke_api_key",
            headers={"Authorization": "Bearer gw_test_"},
        )
        assert [row["resource_id"] for row in r.json()["items"]] == ["r1"]

    def test_outcome_filter(self, client):
        _seed(outcome="success", resource_id="ok")
        _seed(outcome="denied", resource_id="no")
        r = client.get(
            "/admin/audit-log?outcome=denied",
            headers={"Authorization": "Bearer gw_test_"},
        )
        assert [row["resource_id"] for row in r.json()["items"]] == ["no"]

    def test_date_range_filter(self, client):
        now = datetime.now(timezone.utc)
        _seed(resource_id="before", ts=now - timedelta(days=10))
        _seed(resource_id="inside", ts=now - timedelta(days=2))
        _seed(resource_id="after", ts=now)
        params = {
            "date_from": (now - timedelta(days=5)).isoformat(),
            "date_to": (now - timedelta(days=1)).isoformat(),
        }
        r = client.get(
            "/admin/audit-log",
            params=params,
            headers={"Authorization": "Bearer gw_test_"},
        )
        assert [row["resource_id"] for row in r.json()["items"]] == ["inside"]

    def test_free_text_search_matches_resource_and_action(self, client):
        _seed(
            action="create_api_key",
            resource_type="api_key",
            resource_id="abc-123",
        )
        _seed(
            action="update_tenant",
            resource_type="tenant",
            resource_id="xyz-999",
        )
        r = client.get(
            "/admin/audit-log?q=abc-123",
            headers={"Authorization": "Bearer gw_test_"},
        )
        assert len(r.json()["items"]) == 1
        r = client.get(
            "/admin/audit-log?q=tenant",
            headers={"Authorization": "Bearer gw_test_"},
        )
        assert len(r.json()["items"]) == 1

    def test_pagination_respects_limit_and_offset(self, client):
        now = datetime.now(timezone.utc)
        for i in range(12):
            _seed(
                resource_id=f"r-{i:02d}",
                ts=now - timedelta(minutes=i),
            )
        page1 = client.get(
            "/admin/audit-log?limit=5&offset=0",
            headers={"Authorization": "Bearer gw_test_"},
        ).json()
        page2 = client.get(
            "/admin/audit-log?limit=5&offset=5",
            headers={"Authorization": "Bearer gw_test_"},
        ).json()
        assert page1["total"] == 12
        assert len(page1["items"]) == 5
        assert len(page2["items"]) == 5
        # No overlap between pages.
        ids_1 = {r["id"] for r in page1["items"]}
        ids_2 = {r["id"] for r in page2["items"]}
        assert ids_1.isdisjoint(ids_2)


class TestAuditLogCsvExport:
    def test_csv_download_headers_and_rows(self, client):
        _seed(resource_id="csv-1", action="create_api_key")
        _seed(resource_id="csv-2", action="revoke_api_key")
        r = client.get(
            "/admin/audit-log?format=csv",
            headers={"Authorization": "Bearer gw_test_"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "admin_audit_log.csv" in r.headers["content-disposition"]
        reader = csv.reader(io.StringIO(r.text))
        rows = list(reader)
        header = rows[0]
        assert "timestamp" in header
        assert "action" in header
        assert "resource_id" in header
        # 2 data rows.
        data_rows = rows[1:]
        assert len(data_rows) == 2
        resource_ids = {r[header.index("resource_id")] for r in data_rows}
        assert resource_ids == {"csv-1", "csv-2"}

    def test_csv_respects_filter(self, client):
        _seed(resource_id="match-1", action="create_api_key")
        _seed(resource_id="other", action="update_tenant")
        r = client.get(
            "/admin/audit-log?format=csv&action=create_api_key",
            headers={"Authorization": "Bearer gw_test_"},
        )
        reader = csv.reader(io.StringIO(r.text))
        rows = list(reader)
        assert len(rows) == 2  # header + 1 data row
        assert "match-1" in rows[1]
