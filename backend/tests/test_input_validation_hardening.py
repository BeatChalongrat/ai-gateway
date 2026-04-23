"""Phase 2C — Commit 15: input validation hardening (TDD).

Three policies that Phase 1.4 flagged as missing:

1. Name / title fields reject empty strings after trimming (min 1).
2. Name / title fields reject > 255 chars — return 400/422 cleanly,
   never 500 from a DB truncation / internal crash.
3. Admin PATCH / PUT endpoints reject unknown fields with a 400
   listing the offending field names, instead of silently dropping
   them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


class TestNameValidation:
    def test_empty_tenant_name_rejected(self, client):
        r = client.post(
            "/admin/tenants",
            json={"name": "", "slug": "x"},
        )
        # Either 400 (custom path) or 422 (pydantic) — both are safe.
        assert r.status_code in (400, 422), r.text
        assert r.status_code < 500

    def test_whitespace_only_name_rejected(self, client):
        r = client.post(
            "/admin/tenants",
            json={"name": "   ", "slug": "x"},
        )
        assert r.status_code in (400, 422)
        assert r.status_code < 500

    def test_too_long_tenant_name_rejected(self, client):
        r = client.post(
            "/admin/tenants",
            json={"name": "a" * 300, "slug": "x"},
        )
        assert r.status_code in (400, 422), r.text
        # Critical: must NOT be a 500 from DB truncation.
        assert r.status_code < 500

    def test_project_name_same_constraints(self, client):
        r = client.post(
            "/admin/projects",
            json={"tenant_id": "test-tenant", "name": ""},
        )
        assert r.status_code in (400, 422)
        r = client.post(
            "/admin/projects",
            json={"tenant_id": "test-tenant", "name": "n" * 300},
        )
        assert r.status_code in (400, 422)


class TestPatchUnknownFields:
    """TenantUpdate / ProjectUpdate used to silently drop unknown fields
    — a migration tool that thought it was setting ``new_column=...``
    would see a 200 response and believe the change landed. Now the
    server rejects the body so callers discover the typo.
    """

    def test_tenant_update_unknown_field_returns_400(self, client):
        r = client.put(
            "/admin/tenants/test-tenant",
            json={"name": "Renamed", "totally_made_up_field": "x"},
        )
        assert r.status_code == 400, r.text
        body = r.json()
        msg_str = str(body)
        assert "totally_made_up_field" in msg_str

    def test_project_update_unknown_field_returns_400(self, client):
        # Seed a project
        import uuid
        from app.models.project import ProjectORM
        from tests.conftest import _IntegrationSession

        db = _IntegrationSession()
        pid = f"proj-{uuid.uuid4().hex[:8]}"
        try:
            db.add(ProjectORM(
                project_id=pid, tenant_id="test-tenant",
                name="P15 test", policy_profile_id="default",
            ))
            db.commit()
        finally:
            db.close()

        try:
            r = client.put(
                f"/admin/projects/{pid}",
                json={"name": "x", "bogus": "y", "anotherbogus": 1},
            )
            assert r.status_code == 400, r.text
            body = r.json()
            msg_str = str(body)
            # BOTH unknown fields should be listed — not just the first.
            assert "bogus" in msg_str
            assert "anotherbogus" in msg_str
        finally:
            db = _IntegrationSession()
            try:
                row = db.get(ProjectORM, pid)
                if row is not None:
                    db.delete(row)
                    db.commit()
            finally:
                db.close()

    def test_known_fields_still_work(self, client):
        """Guard against over-rejecting — a body with ONLY known fields
        must still succeed."""
        r = client.put(
            "/admin/tenants/test-tenant",
            json={"name": "Renamed"},
        )
        assert r.status_code in (200, 204), r.text
