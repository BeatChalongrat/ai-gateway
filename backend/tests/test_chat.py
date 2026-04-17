from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_chat_completion_stub():
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer demo-key"},
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] in {"openai", "anthropic"}
