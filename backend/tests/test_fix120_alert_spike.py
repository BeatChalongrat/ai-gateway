"""FIX120 — ``fallback_spike``: an alert rule over the count of rescued
turns in the window. A working fallback hides a broken primary; this is
the alarm that says the primary is broken while nobody complains.
"""

from __future__ import annotations

import pytest

from app.models.alert_rule import AlertEventORM
from app.models.request_log import RequestLogORM
from app.schemas.admin import ALERT_METRICS
from app.services.alert_evaluator import _METRIC_TO_TYPE, AlertEvaluator
from app.services.alert_service import AlertType
from tests.test_alert_evaluator import _insert_request_log, _insert_rule, session  # noqa: F401 — fixture


def test_fallback_spike_is_a_known_metric_everywhere():
    assert _METRIC_TO_TYPE["fallback_spike"] is AlertType.FALLBACK_SPIKE
    assert "fallback_spike" in ALERT_METRICS
    assert AlertType.FALLBACK_SPIKE.value == "fallback_spike"


@pytest.mark.asyncio
async def test_fires_on_rescued_turns_only(session):
    # three rescued turns (success rows with fallback_used) + one plain success
    for _ in range(3):
        row = _insert_request_log(session, status="success")
        session.query(RequestLogORM).filter(RequestLogORM.request_id == row.request_id).update({"fallback_used": True})
    _insert_request_log(session, status="success")
    session.commit()
    trigger = _insert_request_log(session, status="success")
    session.query(RequestLogORM).filter(RequestLogORM.request_id == trigger.request_id).update({"fallback_used": True})
    session.commit()
    _insert_rule(session, metric="fallback_spike", threshold=3, scope="global")

    fired = await AlertEvaluator().evaluate_after_request(session, trigger)
    assert len(fired) == 1
    event = session.query(AlertEventORM).one()
    assert event.alert_type == AlertType.FALLBACK_SPIKE.value


@pytest.mark.asyncio
async def test_does_not_fire_when_nothing_was_rescued(session):
    for _ in range(5):
        _insert_request_log(session, status="success")
    trigger = _insert_request_log(session, status="error")
    _insert_rule(session, metric="fallback_spike", threshold=1, scope="global")
    fired = await AlertEvaluator().evaluate_after_request(session, trigger)
    assert fired == []
