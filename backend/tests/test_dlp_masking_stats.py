"""FIX51 Part 1.2 — the under-answer dlp_notice LINE is gone, the numbers stay.

The owner removed the client-side "parts of your question were hidden"
sentence (OPEN_DECISIONS #62). That removal is display-only. Two things
must survive it, and these tests pin both:

* the server-side counter ``GET /admin/dlp/input-masking-stats`` keeps
  counting masked-input turns per detector — it is the only number the
  over-masking weight decision (OPEN_DECISIONS #4) has; and
* the ``dlp_notice`` SSE frame is still emitted by the streaming route,
  so a future quieter surface can render it without a server change.

Detector names are prefixed ``fx51_`` so exact-count assertions cannot
collide with rows other tests leave in the shared integration engine.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.main import app as main_app
from app.models.request_log import RequestLogORM


def _client() -> TestClient:
    return TestClient(main_app)


def _row(
    *,
    input_findings: int = 0,
    input_action: str | None = None,
    findings: list[dict] | None = None,
) -> RequestLogORM:
    return RequestLogORM(
        request_id=str(uuid.uuid4()),
        tenant_id="test-tenant",
        project_id="test-project",
        provider="mock",
        model="mock-chat-1",
        status="success",
        policy_action="allow",
        dlp_input_findings=input_findings,
        dlp_input_action=input_action,
        dlp_findings_json=json.dumps(findings) if findings is not None else None,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_counter_counts_masked_input_turns_per_detector():
    from tests.conftest import _IntegrationSession

    session = _IntegrationSession()
    try:
        # Turn 1 — question masked twice by fx51_thai_name, once by
        # fx51_phone; the output-leg fx51_email mask must NOT count.
        session.add(
            _row(
                input_findings=3,
                input_action="mask",
                findings=[
                    {"direction": "input", "action": "mask", "detector": "fx51_thai_name"},
                    {"direction": "input", "action": "mask", "detector": "fx51_thai_name"},
                    {"direction": "input", "action": "mask", "detector": "fx51_phone"},
                    {"direction": "output", "action": "mask", "detector": "fx51_email"},
                ],
            )
        )
        # Turn 2 — clean question.
        session.add(_row())
        # Turn 3 — input finding logged but action allow (no transform):
        # its detector must not be counted as masking.
        session.add(
            _row(
                input_findings=1,
                input_action="allow",
                findings=[
                    {"direction": "input", "action": "allow", "detector": "fx51_email"}
                ],
            )
        )
        session.commit()
    finally:
        session.close()

    resp = _client().get("/admin/dlp/input-masking-stats?days=7")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["turns_total"] >= 3
    assert data["turns_with_input_masking"] >= 1
    assert data["findings_by_detector"].get("fx51_thai_name", 0) == 2
    assert data["findings_by_detector"].get("fx51_phone", 0) == 1
    # One TURN carried fx51_thai_name (twice) — turns count is 1, not 2.
    assert data["turns_by_detector"].get("fx51_thai_name", 0) == 1
    # The output-leg mask and the allow-leg finding are not input masks.
    assert "fx51_email" not in data["findings_by_detector"]
    # Counts only — the response must never carry matched text.
    assert "matched_text" not in json.dumps(data)


@pytest.mark.asyncio
async def test_streaming_route_still_emits_dlp_notice_frame():
    """Transport pin: removing the LINE (web) must not remove the FRAME.

    The admin counter reads request_logs, not the frame — but the frame
    is the contract a quieter future surface builds on, and FIX50's
    chat-edge relay + store stash still forward it. If someone deletes
    the emission wholesale this fails and forces a decision.
    """
    import inspect

    from app.api.routes import public_chat

    src = inspect.getsource(public_chat)
    assert "_emit_dlp_notice" in src
    assert '"dlp_notice"' in src
