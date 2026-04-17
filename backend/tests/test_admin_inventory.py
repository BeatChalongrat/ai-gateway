"""P7-01: Admin inventory validation tests."""

from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_H = {"Authorization": "Bearer admin-key"}
DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestAdminInventoryDoc:
    def test_doc_exists(self):
        assert (DOCS_DIR / "P7_ADMIN_INVENTORY.md").exists()

    def test_documents_gaps(self):
        doc = (DOCS_DIR / "P7_ADMIN_INVENTORY.md").read_text(encoding="utf-8")
        for gap in ["GAP-A1", "GAP-A2", "GAP-A3", "GAP-A4"]:
            assert gap in doc


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
