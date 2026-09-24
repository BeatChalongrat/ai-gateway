"""/health — the component check that orchestrators and the ops pager read.

FIX133 — unit tests of the aggregation. Every component probe is stubbed, so
each test states what /health answers for one set of component states, and
all of them pass in the hermetic lane (no Postgres, Redis, sidecars or NER
service there). The original assertion — the LIVE stack answers 200 "ok" —
is kept as an integration test: in the unit-lane container the database is
unreachable and /health answers 503 by design (FIX126 O8-5), which is
exactly the "database down" case below.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.routes import health as health_mod
from app.main import app

client = TestClient(app)


class _FakePool:
    def size(self):
        return 5

    def checkedout(self):
        return 0

    def overflow(self):
        return 0

    def checkedin(self):
        return 5


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *_a, **_k):
        return None


class _FakeEngine:
    def __init__(self, state):
        self._state = state
        self.pool = _FakePool()

    def connect(self):
        if not self._state["db"]:
            raise ConnectionError("connection refused")
        return _FakeConn()


@pytest.fixture
def components(monkeypatch):
    """Every component probe answers healthy; a test flips what it needs.

    The route caches its last result for 10 s, so the cache is cleared first
    — otherwise a test would read the state the previous one left. The Redis
    client is the lane's in-process fake (tests/_redis_isolation.py); only
    its PING is patched, so conftest's key clean-up still works on it.
    """
    import redis

    state = {"db": True, "redis": True}

    def _ping(self):
        if not state["redis"]:
            raise ConnectionError("connection refused")
        return True

    monkeypatch.setattr(health_mod, "_health_cache", None)
    monkeypatch.setattr(health_mod, "_health_cache_ts", 0.0)
    monkeypatch.setattr(health_mod, "engine", _FakeEngine(state))
    monkeypatch.setattr(redis.Redis, "ping", _ping)
    monkeypatch.setattr(health_mod, "_probe_sidecar", lambda kind, addr, timeout=0.5: "healthy")
    monkeypatch.setattr(
        health_mod,
        "_probe_ner_service",
        lambda timeout=0.5: {
            "status": "healthy",
            "th_ner": "ready",
            "model": "th_ner=ready",
            "thai_ner_layer": True,
        },
    )
    return state


def test_health_is_200_ok_when_every_component_answers(components):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "secure-ai-gateway"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"


def test_health_is_503_unhealthy_when_the_database_is_down(components):
    components["db"] = False
    response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["database"].startswith("error:")


def test_health_is_200_degraded_when_only_redis_is_down(components):
    # Redis down is served fail-open (limits, caches), so orchestrators must
    # NOT restart the process: 200, and the word "degraded" for the operator.
    components["redis"] = False
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == "unavailable"


@pytest.mark.integration
def test_health_is_ok_on_the_live_stack():
    """Against a full stack (Postgres, Redis, the active sidecars, the NER
    service) /health answers 200 "ok" — the integration lane's check."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
