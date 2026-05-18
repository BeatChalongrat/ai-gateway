"""Tests for P0-04: Response contract stabilization.

Verifies:
- Health endpoint returns typed HealthResponse shape
- /v1/models returns OpenAI-compatible ModelListResponse shape
- Admin list endpoints return {"items": [...]} with typed entries
- Error responses on public routes follow the ErrorResponse envelope
- OpenAPI schema reflects declared response models
"""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_AH = {"Authorization": "Bearer admin-key"}  # admin auth for /admin/* endpoints


# ---------------------------------------------------------------------------
# Health endpoint contract
# ---------------------------------------------------------------------------


class TestHealthContract:
    def test_returns_200(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_shape(self):
        body = client.get("/health").json()
        assert set(body.keys()) == {"status", "service", "version"}

    def test_values(self):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["service"] == "secure-ai-gateway"
        assert body["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# /v1/models contract (OpenAI-compatible)
# ---------------------------------------------------------------------------


class TestModelsContract:
    def test_returns_200(self):
        resp = client.get("/v1/models")
        assert resp.status_code == 200

    def test_has_data_key(self):
        body = client.get("/v1/models").json()
        assert "data" in body
        assert isinstance(body["data"], list)

    def test_each_model_has_id_and_provider(self):
        models = client.get("/v1/models").json()["data"]
        for m in models:
            assert "id" in m
            assert "provider" in m

    def test_auto_model_present(self):
        models = client.get("/v1/models").json()["data"]
        ids = [m["id"] for m in models]
        assert "auto" in ids


# ---------------------------------------------------------------------------
# Admin list endpoint contracts
# ---------------------------------------------------------------------------


class TestAdminProvidersContract:
    def test_returns_200(self):
        assert client.get("/admin/providers", headers=_AH).status_code == 200

    def test_has_items_key(self):
        body = client.get("/admin/providers", headers=_AH).json()
        assert "items" in body
        assert isinstance(body["items"], list)

    def test_item_shape(self):
        item = client.get("/admin/providers", headers=_AH).json()["items"][0]
        assert "provider_name" in item
        assert "enabled" in item
        assert "models" in item


class TestAdminPoliciesContract:
    def test_returns_200(self):
        assert client.get("/admin/policies", headers=_AH).status_code == 200

    def test_has_items_key(self):
        body = client.get("/admin/policies", headers=_AH).json()
        assert "items" in body

    def test_item_shape(self):
        item = client.get("/admin/policies", headers=_AH).json()["items"][0]
        assert "profile_id" in item
        assert "name" in item
        assert "mode" in item


class TestAdminRequestsContract:
    def test_returns_200(self):
        assert client.get("/admin/requests", headers=_AH).status_code == 200

    def test_has_items_key(self):
        body = client.get("/admin/requests", headers=_AH).json()
        assert "items" in body

    def test_empty_list_is_valid(self):
        """Now DB-backed — may be empty in test environment."""
        items = client.get("/admin/requests", headers=_AH).json()["items"]
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# Error envelope on public routes
# ---------------------------------------------------------------------------


class TestPublicRouteErrorEnvelopes:
    def test_chat_401_follows_error_contract(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]
        assert "request_id" in body

    def test_chat_422_follows_error_contract(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test"},
            json={"messages": "not-a-list"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "validation_error"
        assert "errors" in body["error"]["details"]


# ---------------------------------------------------------------------------
# OpenAPI schema reflects response models
# ---------------------------------------------------------------------------


class TestOpenAPISchema:
    # FastAPI keys ``content`` by the response class's ``media_type``.
    # The project-wide UTF8JSONResponse declares its media_type with an
    # explicit charset suffix so clients see ``application/json;
    # charset=utf-8`` over the wire — match that here.
    _CONTENT_TYPE = "application/json; charset=utf-8"

    def _schema(self):
        return client.get("/openapi.json").json()

    def test_health_schema_has_response_model(self):
        schema = self._schema()
        health = schema["paths"]["/health"]["get"]["responses"]["200"]
        ref = health["content"][self._CONTENT_TYPE]["schema"]["$ref"]
        assert "HealthResponse" in ref

    def test_models_schema_has_response_model(self):
        schema = self._schema()
        models = schema["paths"]["/v1/models"]["get"]["responses"]["200"]
        ref = models["content"][self._CONTENT_TYPE]["schema"]["$ref"]
        assert "ModelListResponse" in ref

    def test_chat_schema_declares_error_responses(self):
        schema = self._schema()
        chat = schema["paths"]["/v1/chat/completions"]["post"]["responses"]
        assert "401" in chat
        assert "422" in chat
        assert "500" in chat
