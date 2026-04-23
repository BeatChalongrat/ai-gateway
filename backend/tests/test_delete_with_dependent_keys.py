"""Phase 2B — Commit 12: P0-11 block DELETE /admin/projects/{id} when keys exist.

Locked decision (Option B): clear 409 Conflict with the dependent key
IDs listed, so the operator knows what to clean up. No cascade (would
risk accidental data loss), no soft delete (not needed in this phase).

Before this commit, attempting to DELETE a project that had API keys
pointing at it crashed with HTTP 500 (``ForeignKeyViolation``). Now:

* project has 0 keys → 204 No Content, project row deleted
* project has >=1 keys → 409 Conflict, body contains
  ``{"error": "project has N API keys; delete them first",
     "dependent_keys": ["key-1", "key-2", ...]}``
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def _seed_project_with_keys():
    """Insert a project + 2 API keys into the shared integration DB."""
    from app.models.api_key import ApiKeyORM
    from app.models.project import ProjectORM
    from tests.conftest import _IntegrationSession

    db = _IntegrationSession()
    project_id = f"proj-{uuid.uuid4().hex[:8]}"
    key_ids = [f"key-{uuid.uuid4().hex[:8]}" for _ in range(2)]
    try:
        db.add(
            ProjectORM(
                project_id=project_id,
                tenant_id="test-tenant",
                name="P0-11 test project",
                policy_profile_id="default",
            )
        )
        for kid in key_ids:
            db.add(
                ApiKeyORM(
                    key_id=kid,
                    tenant_id="test-tenant",
                    project_id=project_id,
                    key_prefix="gw_p0_",
                    key_hash=f"h-{kid}",  # unique per key
                    name=kid,
                    status="active",
                )
            )
        db.commit()
    finally:
        db.close()

    yield project_id, key_ids

    # cleanup
    db = _IntegrationSession()
    try:
        for kid in key_ids:
            row = db.get(ApiKeyORM, kid)
            if row is not None:
                db.delete(row)
        row = db.get(ProjectORM, project_id)
        if row is not None:
            db.delete(row)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def _seed_empty_project():
    from app.models.project import ProjectORM
    from tests.conftest import _IntegrationSession

    db = _IntegrationSession()
    project_id = f"proj-empty-{uuid.uuid4().hex[:8]}"
    try:
        db.add(
            ProjectORM(
                project_id=project_id,
                tenant_id="test-tenant",
                name="P0-11 empty test project",
                policy_profile_id="default",
            )
        )
        db.commit()
    finally:
        db.close()
    yield project_id
    db = _IntegrationSession()
    try:
        row = db.get(ProjectORM, project_id)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


class TestDeleteBlocked:
    def test_delete_with_dependent_keys_returns_409(
        self, client, _seed_project_with_keys
    ):
        project_id, key_ids = _seed_project_with_keys
        r = client.delete(f"/admin/projects/{project_id}")
        assert r.status_code == 409, r.text
        body = r.json()
        # Specific error shape per locked spec.
        msg_str = str(body)
        assert "API keys" in msg_str
        assert "delete them first" in msg_str
        # The dependent_keys list must be present and contain our ids.
        # Our error handler wraps as {"error": {"code","message","details"}}.
        returned_ids = body["error"]["details"]["dependent_keys"]
        assert set(returned_ids) == set(key_ids), (
            f"expected {key_ids}, got {returned_ids}"
        )

    def test_error_body_counts_keys(self, client, _seed_project_with_keys):
        project_id, key_ids = _seed_project_with_keys
        r = client.delete(f"/admin/projects/{project_id}")
        assert r.status_code == 409
        assert str(len(key_ids)) in str(r.json())


class TestDeleteAllowed:
    def test_delete_project_with_no_keys_returns_204(
        self, client, _seed_empty_project
    ):
        project_id = _seed_empty_project
        r = client.delete(f"/admin/projects/{project_id}")
        assert r.status_code == 204

    def test_delete_is_idempotent_on_missing(self, client):
        """A non-existent project still returns 404 (NotFoundError),
        not 409 — the conflict-resolution path only fires when the
        project actually exists."""
        r = client.delete("/admin/projects/no-such-project-id")
        assert r.status_code == 404
