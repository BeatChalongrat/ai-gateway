"""P1-05: Public models/discovery endpoint tests.

Verifies:
- /v1/models returns data from model registry
- auto model is always first
- All registry models are included
- Response shape is OpenAI-compatible
"""

from fastapi.testclient import TestClient

from app.main import app
from app.services.providers.model_registry import list_models

client = TestClient(app)


class TestModelsEndpoint:
    def test_returns_200(self):
        assert client.get("/v1/models").status_code == 200

    def test_has_data_key(self):
        body = client.get("/v1/models").json()
        assert "data" in body
        assert isinstance(body["data"], list)

    def test_auto_model_present(self):
        body = client.get("/v1/models").json()
        ids = [m["id"] for m in body["data"]]
        assert "auto" in ids

    def test_auto_is_first(self):
        body = client.get("/v1/models").json()
        assert body["data"][0]["id"] == "auto"
        assert body["data"][0]["provider"] == "gateway"

    def test_registry_models_included(self):
        body = client.get("/v1/models").json()
        response_ids = {m["id"] for m in body["data"]}
        registry_models = {m["model"] for m in list_models()}
        assert registry_models.issubset(response_ids)

    def test_model_has_provider_field(self):
        body = client.get("/v1/models").json()
        for model in body["data"]:
            assert "id" in model
            assert "provider" in model

    def test_includes_openai_models(self):
        body = client.get("/v1/models").json()
        providers = {m["provider"] for m in body["data"]}
        assert "openai" in providers

    def test_includes_anthropic_models(self):
        body = client.get("/v1/models").json()
        providers = {m["provider"] for m in body["data"]}
        assert "anthropic" in providers

    def test_more_than_three_models(self):
        """Registry should have more models than the old hardcoded 3."""
        body = client.get("/v1/models").json()
        assert len(body["data"]) > 3
