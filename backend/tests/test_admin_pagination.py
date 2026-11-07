"""P7-08: Pagination tests for admin endpoints."""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db_session
from app.main import app as main_app
import app.models  # noqa: F401


@pytest.fixture()
def admin_client(sqlite_engine):
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
    client = TestClient(main_app)
    # Create 5 tenants
    for i in range(5):
        client.post("/admin/tenants", json={"name": f"T{i}", "slug": f"t-{uuid.uuid4().hex[:8]}"})
    yield client
    from tests.conftest import _integration_db_session
    main_app.dependency_overrides[get_db_session] = _integration_db_session


class TestPagination:
    def test_default_returns_all(self, admin_client):
        resp = admin_client.get("/admin/tenants")
        body = resp.json()
        assert len(body["items"]) == 5
        assert body["total"] == 5

    def test_limit_respected(self, admin_client):
        resp = admin_client.get("/admin/tenants?limit=2")
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["limit"] == 2

    def test_offset_respected(self, admin_client):
        resp = admin_client.get("/admin/tenants?limit=2&offset=3")
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["offset"] == 3

    def test_offset_past_end(self, admin_client):
        resp = admin_client.get("/admin/tenants?offset=100")
        body = resp.json()
        assert len(body["items"]) == 0
        assert body["total"] == 5

    def test_response_has_pagination_fields(self, admin_client):
        body = admin_client.get("/admin/tenants").json()
        assert "total" in body
        assert "limit" in body
        assert "offset" in body
