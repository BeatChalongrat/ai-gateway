"""Phase 2B — Commit 11: P0-9 + P0-10 reject client-supplied IDs (TDD).

Locked decision (Option A): server owns identity generation. Clients
cannot supply ``tenant_id`` when creating a tenant, or ``project_id``
when creating a project. Any request that includes those fields in
the body returns 400 Bad Request with the exact error message
``"client-supplied <field> is not permitted; server generates
identifiers"`` — explicit reject, NOT silent drop.

Note on ``tenant_id`` for projects: the project-create body DOES take
a ``tenant_id`` field because it selects the parent tenant. Rejecting
would break the flow. Only ``project_id`` (the thing the server
owns) is rejected on project create.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _seed_tenant(db_session=None):
    """Seed a tenant so project-create has a valid parent."""
    # The shared conftest already set up test-tenant in the integration
    # DB; nothing to do here. The fixture exists so the module reads
    # like a spec even when no extra seeding is needed.
    yield


class TestP0_9TenantIdRejected:
    def test_creating_tenant_with_supplied_tenant_id_returns_400(self, client):
        r = client.post(
            "/admin/tenants",
            json={
                "tenant_id": "attacker-picked-id",
                "name": "Evil Co",
                "slug": "evil",
            },
        )
        assert r.status_code == 400, r.text
        body = r.json()
        msg = str(body)
        assert "client-supplied tenant_id" in msg
        assert "server generates" in msg

    def test_creating_tenant_without_id_succeeds_with_server_id(self, client):
        r = client.post(
            "/admin/tenants",
            json={"name": "Good Co", "slug": "good-co"},
        )
        assert r.status_code in (200, 201), r.text
        body = r.json()
        # Server generated a tenant_id — it must not be empty/null.
        assert body["tenant_id"]
        assert len(body["tenant_id"]) > 0

    def test_empty_tenant_id_also_rejected(self, client):
        """A caller might try to pass ``tenant_id=""`` thinking the
        server will generate a replacement. Still reject — even the
        presence of the key is the signal we refuse."""
        r = client.post(
            "/admin/tenants",
            json={"tenant_id": "", "name": "X", "slug": "x"},
        )
        assert r.status_code == 400


class TestP0_10ProjectIdRejected:
    def test_creating_project_with_supplied_project_id_returns_400(self, client):
        r = client.post(
            "/admin/projects",
            json={
                "project_id": "attacker-picked-id",
                "tenant_id": "test-tenant",
                "name": "Evil Project",
            },
        )
        assert r.status_code == 400, r.text
        body = r.json()
        msg = str(body)
        assert "client-supplied project_id" in msg

    def test_creating_project_without_id_succeeds(self, client):
        r = client.post(
            "/admin/projects",
            json={"tenant_id": "test-tenant", "name": "Good Project"},
        )
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["project_id"]

    def test_tenant_id_on_project_create_is_still_accepted(self, client):
        """``tenant_id`` on /admin/projects is a parent-reference, not
        a client-supplied identifier for the new project. It MUST
        remain legal. This test protects that distinction."""
        r = client.post(
            "/admin/projects",
            json={"tenant_id": "test-tenant", "name": "Normal Project"},
        )
        assert r.status_code in (200, 201)
