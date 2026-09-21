import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


@pytest.mark.quarantine(
    reason=(
        "environment: asserts status 'ok', which needs a reachable Postgres, "
        "Redis, the active sidecars and the NER service; the unit-lane "
        "container has none of them, so /health answers 503 by design. "
        "Belongs in the integration lane."
    ),
    ticket="FIX126-O8-5",
)
def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
