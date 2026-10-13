"""P7-01: the admin endpoints the inventory named exist and answer."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_H = {"Authorization": "Bearer admin-key"}


class TestExistingAdminEndpoints:
    def test_tenants_list(self):
        assert client.get("/admin/tenants", headers=_H).status_code == 200

    def test_projects_list(self):
        assert client.get("/admin/projects", headers=_H).status_code == 200

    def test_api_keys_list(self):
        assert client.get("/admin/api-keys", headers=_H).status_code == 200

    def test_providers_list(self):
        assert client.get("/admin/providers", headers=_H).status_code == 200

    def test_policies_list(self):
        assert client.get("/admin/policies", headers=_H).status_code == 200

    def test_requests_list(self):
        assert client.get("/admin/requests", headers=_H).status_code == 200
