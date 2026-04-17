"""P7-05: Providers/Models visibility tests."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
_H = {"Authorization": "Bearer admin-key"}


class TestAdminProviders:
    def test_returns_200(self):
        assert client.get("/admin/providers", headers=_H).status_code == 200

    def test_providers_from_registry(self):
        items = client.get("/admin/providers", headers=_H).json()["items"]
        providers = {i["provider_name"] for i in items}
        assert "openai" in providers
        assert "anthropic" in providers

    def test_provider_has_models(self):
        items = client.get("/admin/providers", headers=_H).json()["items"]
        for item in items:
            assert isinstance(item["models"], list)
            assert len(item["models"]) > 0


class TestAdminModels:
    def test_returns_200(self):
        assert client.get("/admin/models", headers=_H).status_code == 200

    def test_has_capability_fields(self):
        items = client.get("/admin/models", headers=_H).json()["items"]
        assert len(items) > 0
        for item in items:
            assert "model" in item
            assert "provider" in item
            assert "supports_streaming" in item
            assert "cost_tier" in item
            assert "latency_tier" in item

    def test_includes_openai_models(self):
        items = client.get("/admin/models", headers=_H).json()["items"]
        openai_models = [i for i in items if i["provider"] == "openai"]
        assert len(openai_models) > 0

    def test_includes_anthropic_models(self):
        items = client.get("/admin/models", headers=_H).json()["items"]
        anthropic_models = [i for i in items if i["provider"] == "anthropic"]
        assert len(anthropic_models) > 0
