"""P5-09: End-to-end routing integration tests.

Tests routing through real request flows with stubbed provider dispatch:
- Explicit model → correct provider
- Auto routing → default selection
- Capability mismatch → filtered
- Policy constraint → restricted
- Cost/latency preferences
- Full lifecycle: request → routing → response
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _chat(content="Hi", model="auto", **kwargs):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "metadata": kwargs.pop("metadata", {}),
        **kwargs,
    }
    return client.post("/v1/chat/completions", headers=_HEADERS, json=body)


class TestRoutingE2EExplicitModel:
    def test_openai_model_routes_to_openai(self):
        resp = _chat(model="gpt-4o-mini")
        assert resp.status_code == 200
        assert resp.json()["provider"] == "openai"

    def test_anthropic_model_routes_to_anthropic(self):
        resp = _chat(model="claude-3-5-sonnet-20241022")
        assert resp.status_code == 200
        assert resp.json()["provider"] == "anthropic"

    def test_unknown_model_still_works(self):
        resp = _chat(model="some-unknown-model")
        assert resp.status_code == 200


class TestRoutingE2EAuto:
    def test_auto_default_returns_openai(self):
        resp = _chat(model="auto")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "openai"

    def test_auto_coding_returns_anthropic(self):
        resp = _chat(model="auto", metadata={"use_case": "coding"})
        assert resp.status_code == 200
        assert resp.json()["provider"] == "anthropic"

    def test_auto_reasoning_is_served_by_a_reasoning_capable_model(self):
        # FIX35 Part C — the Auto router is provider-agnostic: ``use_case``
        # "reasoning" pins the pool to reasoning-capable models (the P0
        # unlock, kept), it no longer pins a vendor.
        from app.services.providers.model_registry import get_candidate

        resp = _chat(model="auto", metadata={"use_case": "reasoning"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] != "auto"
        from app.services.providers.model_registry import get_all_candidates

        if any(c.supports_reasoning for c in get_all_candidates() if c.provider in {"openai", "anthropic"}):
            cand = get_candidate(body["model"])
            assert cand is not None and cand.supports_reasoning, body["model"]
        # (the stub registry may carry no reasoning-capable model at all —
        #  then the router relaxes to the whole pool rather than failing)


class TestRoutingE2EConstraints:
    def test_allowed_providers_constraint(self):
        resp = _chat(
            model="auto",
            metadata={"allowed_providers": ["anthropic"]},
        )
        assert resp.status_code == 200
        assert resp.json()["provider"] == "anthropic"

    def test_blocked_models_constraint(self):
        resp = _chat(
            model="auto",
            metadata={"blocked_models": ["gpt-4o-mini"]},
        )
        assert resp.status_code == 200
        assert resp.json()["model"] != "gpt-4o-mini"


class TestRoutingE2EResponseContract:
    def test_response_includes_provider(self):
        body = _chat().json()
        assert body["provider"] in {"openai", "anthropic", "openrouter"}

    def test_response_model_is_concrete(self):
        body = _chat().json()
        assert body["model"] != "auto"
        assert len(body["model"]) > 0

    def test_response_has_policy_action(self):
        body = _chat().json()
        assert body["policy_action"] in {"allow", "mask", "redact", "block"}
