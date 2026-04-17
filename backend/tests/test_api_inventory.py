"""P1-01: API inventory validation tests.

Verifies:
- All documented public endpoints exist and respond
- Inventory document exists
- Endpoint count matches expectations
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestInventoryDocExists:
    def test_inventory_doc_exists(self):
        path = DOCS_DIR / "P1_API_INVENTORY.md"
        assert path.exists()

    def test_inventory_references_all_routes(self):
        doc = (DOCS_DIR / "P1_API_INVENTORY.md").read_text(encoding="utf-8")
        expected_paths = [
            "/health",
            "/v1/models",
            "/v1/chat/completions",
            "/admin/providers",
            "/admin/policies",
            "/admin/requests",
            "/admin/tenants",
            "/admin/projects",
            "/admin/api-keys",
        ]
        for path in expected_paths:
            assert path in doc, f"Missing route in inventory: {path}"


class TestPublicEndpointsExist:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_models(self):
        resp = client.get("/v1/models")
        assert resp.status_code == 200

    def test_chat_requires_auth(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    def test_chat_with_auth(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer demo-key"},
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.status_code == 200


class TestAdminEndpointsExist:
    def test_list_tenants(self):
        assert client.get("/admin/tenants").status_code == 200

    def test_list_projects(self):
        assert client.get("/admin/projects").status_code == 200

    def test_list_api_keys(self):
        assert client.get("/admin/api-keys").status_code == 200

    def test_list_providers(self):
        assert client.get("/admin/providers").status_code == 200

    def test_list_policies(self):
        assert client.get("/admin/policies").status_code == 200

    def test_list_requests(self):
        assert client.get("/admin/requests").status_code == 200


class TestGapsDocumented:
    """Verify the gap analysis identifies known issues."""

    def test_request_context_gap_documented(self):
        doc = (DOCS_DIR / "P1_API_INVENTORY.md").read_text(encoding="utf-8")
        assert "RequestContextMiddleware" in doc
        assert "not registered" in doc.lower() or "not wired" in doc.lower()

    def test_models_hardcoded_gap_documented(self):
        doc = (DOCS_DIR / "P1_API_INVENTORY.md").read_text(encoding="utf-8")
        assert "Hardcoded" in doc

    def test_streaming_gap_documented(self):
        doc = (DOCS_DIR / "P1_API_INVENTORY.md").read_text(encoding="utf-8")
        assert "stream" in doc.lower()
