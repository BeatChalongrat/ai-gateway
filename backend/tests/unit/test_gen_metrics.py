"""Sprint 1 Batch D6 — the five gen_* media-gen metrics are exposed.

The /metrics endpoint serializes the same CollectorRegistry via
render_prometheus_exposition, so asserting against that function (and the
endpoint) proves the series appear in the scrape with zero values.
"""

from starlette.testclient import TestClient

from app.observability.metrics import render_prometheus_exposition

_GEN_METRICS = (
    b"gen_duration_seconds",
    b"gen_cost_credits",
    b"gen_failure_total",
    b"gen_jobs_active",
    b"gen_queue_depth",
)


def test_gen_metrics_in_registry_exposition():
    body, _content_type = render_prometheus_exposition()
    for name in _GEN_METRICS:
        assert name in body, f"missing metric {name!r} in exposition"


def test_gen_metrics_served_on_metrics_endpoint():
    from app.main import app as main_app

    # No context manager → lifespan (and its background loops) do not start.
    client = TestClient(main_app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.content
    for name in _GEN_METRICS:
        assert name in text, f"missing metric {name!r} at /metrics"
