"""Hot-path integration of custom detectors into the DLP registry (Sprint 6, E).

Exercises the registry's custom-detector provider hook + the runtime
executor's safety guards (timeout, auto-disable, crash isolation, testing vs
active). Uses ``get_default_registry(ner_enabled=False)`` to keep the built-in
set fast (skips the spaCy load) — the email/regex detectors still run.
"""

import uuid

import pytest

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.custom_detector_runtime import (
    build_detector,
    drain_events,
    drain_pending_events,
    enqueue_event,
)
from app.services.dlp.custom_detector_service import CustomDetectorService
from app.models.portal_user import PortalUserORM


def _row(detection_type="regex", pattern=None, status="active", category="pii",
         severity="high", name=None):
    return {
        "id": str(uuid.uuid4()),
        "name": name or f"d-{uuid.uuid4().hex[:6]}",
        "category": category,
        "detection_type": detection_type,
        "pattern": pattern if pattern is not None else {"regex": r"SECRET-\d{4}"},
        "severity": severity,
        "default_action": "mask",
        "status": status,
        "version": 1,
    }


def test_builtins_run_without_custom():
    reg = get_default_registry(ner_enabled=False)
    matches = reg.scan_text("contact me at foo@bar.com please")
    assert any(m.detector_name == "email" for m in matches)


def test_active_custom_detector_matches():
    reg = get_default_registry(ner_enabled=False)
    det = build_detector(_row(pattern={"regex": r"SECRET-\d{4}"}))
    reg.set_custom_provider(lambda: [det])
    matches = reg.scan_text("the code is SECRET-1234 ok")
    customs = [m for m in matches if m.detector_name.startswith("custom:")]
    assert len(customs) == 1
    assert customs[0].category == "pii"
    assert customs[0].severity == SensitivityLevel.HIGH


def test_testing_status_logs_but_no_enforcement():
    drain_pending_events()  # clear queue
    reg = get_default_registry(ner_enabled=False)
    det = build_detector(_row(status="testing", pattern={"regex": r"SECRET-\d{4}"}))
    reg.set_custom_provider(lambda: [det])
    matches = reg.scan_text("the code is SECRET-1234 ok")
    # No enforced custom match...
    assert not [m for m in matches if m.detector_name.startswith("custom:")]
    # ...but the match WAS counted (event enqueued for admin review).
    events = drain_pending_events()
    assert ("match", det._id) in events


def test_timeout_caught_and_recorded():
    drain_pending_events()
    reg = get_default_registry(ner_enabled=False)
    det = build_detector(_row(pattern={"regex": r"((a)*)*$"}))
    reg.set_custom_provider(lambda: [det])
    # Catastrophic pattern on a long input → per-attempt 50ms cap trips.
    matches = reg.scan_text("a" * 500 + "!")
    assert not [m for m in matches if m.detector_name.startswith("custom:")]
    assert ("timeout", det._id) in drain_pending_events()


def test_crashed_custom_detector_isolated():
    class Boom(BaseDetector):
        name = "custom:boom"
        category = "pii"
        default_sensitivity = SensitivityLevel.LOW

        def detect(self, text):  # noqa: D401
            raise RuntimeError("kaboom")

    reg = get_default_registry(ner_enabled=False)
    reg.set_custom_provider(lambda: [Boom()])
    # Built-in email still found; no exception propagates.
    matches = reg.scan_text("foo@bar.com")
    assert any(m.detector_name == "email" for m in matches)


def test_mixed_builtin_and_custom_categories():
    reg = get_default_registry(ner_enabled=False)
    dets = [
        build_detector(_row(category="secret", pattern={"regex": r"SECRET-\d{4}"})),
        build_detector(
            _row(detection_type="keyword_list", category="content_policy",
                 pattern={"keywords": ["forbidden"], "case_sensitive": False})
        ),
    ]
    reg.set_custom_provider(lambda: [d for d in dets if d])
    matches = reg.scan_text("foo@bar.com SECRET-1234 forbidden")
    cats = {m.category for m in matches}
    assert "pii" in cats           # built-in email
    assert "secret" in cats        # custom regex
    assert "content_policy" in cats  # custom keyword


def test_semantic_detector_skipped_at_runtime():
    # Semantic custom detectors are stored/validated but not executed in S6.
    assert build_detector(_row(detection_type="semantic",
                               pattern={"entity_type": "PERSON"})) is None


@pytest.mark.asyncio
async def test_three_timeouts_auto_disable(async_db):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    # tenant_id NOT NULL since SSO Phase 1 A1 (conftest seed).
    async_db.add(
        PortalUserORM(
            id=uid, email=f"{uid}@e.com", password_hash="x",
            tenant_id="test-tenant",
        )
    )
    await async_db.flush()
    svc = CustomDetectorService(async_db, redis_client=None)
    det = await svc.create(
        name=f"d-{uuid.uuid4().hex[:6]}", description=None, category="pii",
        detection_type="regex", pattern={"regex": r"\d{4}"}, severity="medium",
        default_action="mask",
        test_cases=[{"input": "1234", "expected_match": True}],
        created_by_user_id=uid,
    )
    await svc.activate(det.id, uid)

    drain_pending_events()
    for _ in range(3):
        enqueue_event("timeout", det.id)
    await drain_events(svc)

    refreshed = await svc.get(det.id)
    assert refreshed.status == "disabled"
    assert refreshed.timeout_failures >= 3
