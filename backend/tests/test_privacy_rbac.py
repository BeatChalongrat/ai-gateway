"""Tests for Fix 4 — privacy RBAC + retention.

Covers:
- 4a: /admin/requests/{id} gates input_raw / output_raw and the
  matched_text key inside dlp_findings to ADMIN-tier callers.
- 4b: app.jobs.dlp_retention.run_dlp_retention_cleanup NULL-ifies raw
  columns and strips matched_text from dlp_findings_json on rows
  older than the configured retention window.
- 4c: build_dlp_findings_json includes matched_text_len so admin
  endpoints can strip matched_text without losing useful metadata.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.deps import ResolvedApiKey, require_admin
from app.api.routes.admin import _strip_raw_from_findings
from app.jobs.dlp_retention import run_dlp_retention_cleanup
from app.main import app as main_app
from app.models.request_log import RequestLogORM
from tests.conftest import _integration_engine


client = TestClient(main_app, raise_server_exceptions=False)


def _seed_request_log(role_label: str = "rbac-test") -> str:
    """Insert a synthetic request_logs row with raw + masked + findings."""
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    request_id = f"req_rbac_{role_label}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    findings = [
        {
            "direction": "input",
            "detector": "passport",
            "entity_type": "passport_number",
            "category": "identity",
            "severity": "HIGH",
            "start": 22,
            "end": 31,
            "matched_text": "AA1234567",
            "matched_text_len": 9,
            "action": "mask",
            "mask_token": "<r0>",
        }
    ]
    try:
        s.add(
            RequestLogORM(
                request_id=request_id,
                tenant_id="test-tenant",
                project_id="test-project",
                provider="openai",
                model="gpt-4o-mini",
                status="success",
                policy_action="mask",
                latency_ms=42,
                policy_profile_id="default",
                input_raw="My passport number is AA1234567",
                input_masked="My passport number is <r0>",
                output_raw="OK.",
                output_final="OK.",
                dlp_findings_json=json.dumps(findings, ensure_ascii=False),
            )
        )
        s.commit()
    finally:
        s.close()
    return request_id


def _swap_admin_role(monkeypatch, role: str) -> None:
    """Override the require_admin DI to return a key with the given role."""
    auth = ResolvedApiKey(
        key_id="rbac-test-key",
        tenant_id="test-tenant",
        project_id="test-project",
        key_prefix="gw_rbac_",
        role=role,
    )
    monkeypatch.setitem(main_app.dependency_overrides, require_admin, lambda: auth)


# ---------------------------------------------------------------------------
# 4c — matched_text_len in findings
# ---------------------------------------------------------------------------


def test_build_findings_includes_matched_text_len() -> None:
    """build_dlp_findings_json now records matched_text_len alongside matched_text."""
    from dataclasses import dataclass

    from app.domain.dlp import (
        DetectionMatch,
        EnforcementAction,
        MatchAction,
        SensitivityLevel,
    )
    from app.domain.observability import build_dlp_findings_json

    match = DetectionMatch(
        detector_name="email",
        entity_type="email_address",
        category="pii",
        start=0,
        end=11,
        matched_text="a@example.io",
        severity=SensitivityLevel.MEDIUM,
    )
    ma = MatchAction(
        match=match,
        action=EnforcementAction.MASK,
        reason_code="t",
        reason_message="t",
    )

    payload = build_dlp_findings_json([ma])
    assert payload is not None
    findings = json.loads(payload)
    assert len(findings) == 1
    f = findings[0]
    assert f["matched_text"] == "a@example.io"
    assert f["matched_text_len"] == len("a@example.io")


# ---------------------------------------------------------------------------
# 4a — RBAC gate at /admin/requests/{id}
# ---------------------------------------------------------------------------


def test_admin_role_sees_raw_columns_and_matched_text(monkeypatch) -> None:
    request_id = _seed_request_log("admin")
    _swap_admin_role(monkeypatch, "admin")

    res = client.get(f"/admin/requests/{request_id}", headers={"Authorization": "Bearer x"})
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["input_raw"] == "My passport number is AA1234567"
    assert body["output_raw"] == "OK."
    assert body["input_masked"].endswith("<r0>")
    assert body["output_final"] == "OK."
    [finding] = body["dlp_findings"]
    assert finding["matched_text"] == "AA1234567"
    assert finding["matched_text_len"] == 9
    assert finding["mask_token"] == "<r0>"


def test_lower_admin_tier_does_not_see_raw_columns(monkeypatch) -> None:
    """tenant_admin (one level below admin) loses raw + matched_text."""
    request_id = _seed_request_log("tenant")
    _swap_admin_role(monkeypatch, "tenant_admin")

    res = client.get(f"/admin/requests/{request_id}", headers={"Authorization": "Bearer x"})
    assert res.status_code == 200, res.text
    body = res.json()

    # Raw text columns gated.
    assert body["input_raw"] is None
    assert body["output_raw"] is None
    # Masked / final snapshots still visible — they contain only mask
    # tokens / restored content.
    assert "<r0>" in body["input_masked"]
    assert body["output_final"] == "OK."

    [finding] = body["dlp_findings"]
    assert "matched_text" not in finding, "raw value must not leak"
    # matched_text_len is the privacy-safe replacement.
    assert finding["matched_text_len"] == 9
    # Other metadata still present so the trace is still useful.
    assert finding["detector"] == "passport"
    assert finding["mask_token"] == "<r0>"


def test_strip_helper_backfills_matched_text_len_on_legacy_rows() -> None:
    """Rows written before Fix 4c don't carry matched_text_len. The helper
    synthesizes it from matched_text before stripping."""
    legacy = [
        {"detector": "email", "matched_text": "old@x.com", "mask_token": "<r0>"},
        {"detector": "phone", "mask_token": "<r1>"},  # no matched_text either
        "not-a-dict",
    ]
    cleaned = _strip_raw_from_findings(legacy)

    # The non-dict entry is dropped.
    assert len(cleaned) == 2
    assert "matched_text" not in cleaned[0]
    assert cleaned[0]["matched_text_len"] == len("old@x.com")
    # Entry without matched_text keeps its existing keys, no synth.
    assert "matched_text_len" not in cleaned[1]


# ---------------------------------------------------------------------------
# 4b — retention cleanup
# ---------------------------------------------------------------------------


def _seed_aged_log(days_old: int, suffix: str) -> str:
    """Insert a row backdated by N days (relies on Python-side created_at)."""
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    rid = f"req_age_{suffix}"
    findings = [
        {
            "direction": "input",
            "detector": "email",
            "entity_type": "email_address",
            "category": "pii",
            "severity": "MEDIUM",
            "matched_text": "old@example.com",
            "matched_text_len": 15,
            "action": "mask",
            "mask_token": "<r0>",
        }
    ]
    try:
        backdated = datetime.now(timezone.utc) - timedelta(days=days_old)
        s.add(
            RequestLogORM(
                request_id=rid,
                tenant_id="test-tenant",
                project_id="test-project",
                provider="openai",
                model="gpt-4o-mini",
                status="success",
                policy_action="mask",
                latency_ms=12,
                created_at=backdated,
                input_raw="email me at old@example.com",
                input_masked="email me at <r0>",
                output_raw="ok",
                output_final="ok",
                dlp_findings_json=json.dumps(findings, ensure_ascii=False),
            )
        )
        s.commit()
    finally:
        s.close()
    return rid


def _read_log(request_id: str) -> RequestLogORM:
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        return s.get(RequestLogORM, request_id)
    finally:
        s.close()


def test_retention_clears_raw_columns_for_old_rows() -> None:
    """Rows older than retention_days lose input_raw / output_raw."""
    old_id = _seed_aged_log(days_old=120, suffix="old1")
    new_id = _seed_aged_log(days_old=1, suffix="new1")

    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        stats = run_dlp_retention_cleanup(s, retention_days=90)
    finally:
        s.close()

    assert stats.dry_run is False
    assert stats.rows_with_raw_text_cleared >= 1

    old = _read_log(old_id)
    new = _read_log(new_id)

    # Old row: raw columns nulled, masked snapshots retained.
    assert old.input_raw is None
    assert old.output_raw is None
    assert old.input_masked == "email me at <r0>"
    assert old.output_final == "ok"

    # New row: untouched.
    assert new.input_raw == "email me at old@example.com"
    assert new.output_raw == "ok"


def test_retention_strips_matched_text_from_findings_for_old_rows() -> None:
    """Old rows lose matched_text inside dlp_findings_json; metadata stays."""
    old_id = _seed_aged_log(days_old=120, suffix="old2")

    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        run_dlp_retention_cleanup(s, retention_days=90)
    finally:
        s.close()

    old = _read_log(old_id)
    assert old.dlp_findings_json is not None
    findings = json.loads(old.dlp_findings_json)
    assert len(findings) == 1
    f = findings[0]
    assert "matched_text" not in f
    # All non-raw metadata preserved.
    assert f["detector"] == "email"
    assert f["mask_token"] == "<r0>"
    assert f["matched_text_len"] == 15


def test_retention_dry_run_does_not_modify_rows() -> None:
    old_id = _seed_aged_log(days_old=120, suffix="old3")

    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        stats = run_dlp_retention_cleanup(s, retention_days=90, dry_run=True)
    finally:
        s.close()

    assert stats.dry_run is True
    assert stats.rows_with_raw_text_cleared >= 1

    old = _read_log(old_id)
    assert old.input_raw == "email me at old@example.com"
    assert old.output_raw == "ok"
    findings = json.loads(old.dlp_findings_json)
    assert findings[0]["matched_text"] == "old@example.com"


def test_retention_zero_days_clears_everything() -> None:
    """Passing retention_days=0 forces cleanup of all rows with raw content."""
    rid = _seed_aged_log(days_old=0, suffix="now1")

    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        run_dlp_retention_cleanup(s, retention_days=0)
    finally:
        s.close()

    row = _read_log(rid)
    assert row.input_raw is None
    assert row.output_raw is None
