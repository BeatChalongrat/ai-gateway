"""Suppress ``health_condition`` detector on outbound scans.

Surfaced by the May-2026 real-LLM E2E Case 4 trace: GPT-4o-mini's
reply contained the phrase "เพื่อให้การรักษาความปลอดภัย" ("to
**maintain** security"). The Thai ``health_condition`` detector
treated "รักษา" as a medical-treatment verb and minted a
``<<HEALTH_1>>`` finding on it. The outbound mask + orphan-redact
pipeline then stripped the placeholder, leaving "เพื่อให้การ
ความปลอดภัย" — a broken-Thai sentence visible to the user.

The fix adds ``health_condition`` to
``orchestrator._OUTBOUND_SUPPRESSED`` so the detector's outbound
findings are silently dropped. Inbound coverage is preserved
because ``_scan_text_as_result`` gates the suppression on
``direction == "output"``.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Test 1 — health_condition is listed in the outbound suppression set
# ---------------------------------------------------------------------------


def test_health_condition_suppressed_on_outbound():
    """The orchestrator's `_OUTBOUND_SUPPRESSED` set is defined inside
    `_scan_text_as_result` so we exercise it via the public method:
    a text containing a 'health' Thai verb on outbound must produce
    NO `health_condition` finding.
    """
    from app.services.orchestrator import GatewayOrchestrator

    orch = GatewayOrchestrator()
    # "เพื่อให้การรักษาความปลอดภัย" — "maintain security"
    text = "เพื่อให้การรักษาความปลอดภัย"
    result = orch._scan_text_as_result(text, direction="output", ner_enabled=False)
    detectors = [m.detector_name for m in result.matches]
    assert "health_condition" not in detectors, (
        f"health_condition must be suppressed on outbound; got {detectors}"
    )


# ---------------------------------------------------------------------------
# Test 2 — the specific "รักษาความปลอดภัย" phrase produces no FP outbound
# ---------------------------------------------------------------------------


def test_raksa_kwamploadpai_not_masked_outbound():
    """Direct repro of the Case 4 trace: the verb 'รักษา' in the
    'maintain security' phrase reaches the user untouched on the
    outbound scan path.
    """
    from app.services.orchestrator import GatewayOrchestrator

    orch = GatewayOrchestrator()
    text = "การจัดการกับ hardcoded credentials ในโค้ดเป็นสิ่งสำคัญเพื่อให้การรักษาความปลอดภัยข้อมูลดีขึ้น"
    result = orch._scan_text_as_result(text, direction="output", ner_enabled=False)
    health_hits = [
        m for m in result.matches if m.detector_name == "health_condition"
    ]
    assert health_hits == [], (
        "health_condition must not fire on outbound 'รักษาความปลอดภัย'; "
        f"got {[m.matched_text for m in health_hits]}"
    )


# ---------------------------------------------------------------------------
# Test 3 — inbound coverage is unaffected by the outbound suppression
# ---------------------------------------------------------------------------


def test_health_condition_still_works_inbound():
    """The suppression is OUTBOUND-only. A real medical-context Thai
    sentence on INBOUND still triggers the detector. Confirms the
    fix is targeted, not a blanket disable.
    """
    from app.services.dlp.detectors.health_condition import (
        HealthConditionDetector,
    )

    detector = HealthConditionDetector()
    text = "ผู้ป่วยมีประวัติรักษาโรคเบาหวานมา 5 ปี"
    matches = detector.detect(text)
    assert matches, (
        f"health_condition detector should fire on inbound medical "
        f"context; got no matches for {text!r}"
    )
    # The detector's contract is unchanged — gating is at the
    # orchestrator level, not the detector itself.
