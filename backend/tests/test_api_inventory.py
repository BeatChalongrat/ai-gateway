"""P1-01: the public and admin endpoints the inventory named exist and respond."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


class TestPublicEndpointsExist:
    def test_health(self):
        # FIX126 O8 — this class checks that documented endpoints EXIST and
        # answer. /health answers 503 with its check report when the test
        # container cannot reach Postgres (by design: orchestrators must see
        # that); both are the endpoint answering. Health VALUES are
        # test_health.py's business.
        resp = client.get("/health")
        assert resp.status_code in (200, 503)
        assert resp.json()["service"] == "secure-ai-gateway"

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
