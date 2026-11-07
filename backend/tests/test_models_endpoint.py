"""P1-05: Public models/discovery endpoint tests.

Verifies:
- /v1/models returns data from model registry
- auto model is always first
- All registry models are included
- Response shape is OpenAI-compatible
"""

from fastapi.testclient import TestClient

from app.main import app
import pytest

from app.services.providers.model_registry import get_all_candidates, list_models

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


class TestDisplayDataFields:
    """Models-catalog step 1: /v1/models carries context window + per-1M
    prices from the registry row, additively (absent when unknown).

    The provider-key filter is stubbed wide open so these assertions are
    deterministic regardless of which keys the test DB happens to hold
    (standalone runs of this file otherwise see only the ``auto`` row).
    """

    @pytest.fixture(autouse=True)
    def _all_providers_valid(self, monkeypatch):
        from app.api.routes import public_models as pm

        monkeypatch.setattr(
            pm.ProviderKeyService,
            "providers_with_valid_keys",
            lambda self, session: {
                c.provider for c in get_all_candidates()
            },
        )

    def _by_id(self):
        return {m["id"]: m for m in client.get("/v1/models").json()["data"]}

    def test_priced_candidate_exposes_per_1m_prices(self):
        by_id = self._by_id()
        checked = 0
        for c in get_all_candidates():
            row = by_id.get(c.model)
            if (
                row is None
                or (c.cost_per_input_token or 0) <= 0
                or (c.cost_per_output_token or 0) <= 0
            ):
                continue
            assert row["input_price_per_1m"] == round(
                c.cost_per_input_token * 1_000_000, 4
            )
            assert row["output_price_per_1m"] == round(
                c.cost_per_output_token * 1_000_000, 4
            )
            checked += 1
        assert checked > 0, "no priced registry candidate reached the wire"

    def test_context_window_matches_registry(self):
        by_id = self._by_id()
        checked = 0
        for c in get_all_candidates():
            row = by_id.get(c.model)
            if row is None or (c.max_context_tokens or 0) <= 0:
                continue
            assert row["context_window"] == c.max_context_tokens
            checked += 1
        assert checked > 0

    def test_auto_entry_has_no_display_fields(self):
        auto = self._by_id()["auto"]
        assert "context_window" not in auto
        assert "input_price_per_1m" not in auto
        assert "output_price_per_1m" not in auto
