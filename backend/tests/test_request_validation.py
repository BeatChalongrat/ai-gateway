"""P1-06: Request validation and parameter normalization tests.

Verifies:
- Empty messages rejected (422)
- Invalid role rejected (422)
- temperature out of range rejected (422)
- max_tokens out of range rejected (422)
- top_p out of range rejected (422)
- Valid roles accepted
- Valid params accepted
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _chat(**overrides):
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": "hi"}],
    }
    body.update(overrides)
    return client.post("/v1/chat/completions", headers=_HEADERS, json=body)


class TestMessagesValidation:
    def test_empty_messages_rejected(self):
        resp = _chat(messages=[])
        assert resp.status_code == 422

    def test_missing_messages_rejected(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto"},
        )
        assert resp.status_code == 422

    def test_single_message_accepted(self):
        resp = _chat()
        assert resp.status_code == 200


class TestRoleValidation:
    def test_user_role_accepted(self):
        resp = _chat(messages=[{"role": "user", "content": "hi"}])
        assert resp.status_code == 200

    def test_system_role_accepted(self):
        resp = _chat(
            messages=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "hi"},
            ]
        )
        assert resp.status_code == 200

    def test_assistant_role_accepted(self):
        resp = _chat(
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "how are you"},
            ]
        )
        assert resp.status_code == 200

    def test_invalid_role_rejected(self):
        resp = _chat(messages=[{"role": "admin", "content": "hi"}])
        assert resp.status_code == 422

    def test_empty_role_rejected(self):
        resp = _chat(messages=[{"role": "", "content": "hi"}])
        assert resp.status_code == 422


class TestGenerationParamValidation:
    def test_temperature_negative_rejected(self):
        resp = _chat(temperature=-0.1)
        assert resp.status_code == 422

    def test_temperature_too_high_rejected(self):
        resp = _chat(temperature=2.1)
        assert resp.status_code == 422

    def test_temperature_valid_accepted(self):
        resp = _chat(temperature=0.7)
        assert resp.status_code == 200

    def test_temperature_zero_accepted(self):
        resp = _chat(temperature=0.0)
        assert resp.status_code == 200

    def test_max_tokens_zero_rejected(self):
        resp = _chat(max_tokens=0)
        assert resp.status_code == 422

    def test_max_tokens_negative_rejected(self):
        resp = _chat(max_tokens=-1)
        assert resp.status_code == 422

    def test_max_tokens_valid_accepted(self):
        resp = _chat(max_tokens=100)
        assert resp.status_code == 200

    def test_top_p_negative_rejected(self):
        resp = _chat(top_p=-0.1)
        assert resp.status_code == 422

    def test_top_p_above_one_rejected(self):
        resp = _chat(top_p=1.1)
        assert resp.status_code == 422

    def test_top_p_valid_accepted(self):
        resp = _chat(top_p=0.9)
        assert resp.status_code == 200


class TestValidationErrorFormat:
    def test_422_has_unified_envelope(self):
        resp = _chat(messages=[])
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "validation_error"

    def test_422_has_request_id(self):
        resp = _chat(messages=[])
        body = resp.json()
        assert body.get("request_id") is not None
