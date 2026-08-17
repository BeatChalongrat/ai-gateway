"""Admin /admin/system-settings tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.system_setting import SystemSettingORM

from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _seed_settings():
    db = _Session()
    try:
        db.query(SystemSettingORM).delete()
        db.add_all([
            SystemSettingORM(
                key="free_trial_tokens", value="100000",
                description="on signup",
            ),
            SystemSettingORM(
                key="portal_signup_enabled", value="true",
                description="toggle",
            ),
            SystemSettingORM(
                key="credit_low_threshold", value="10000",
                description="warn",
            ),
            SystemSettingORM(
                key="trial_model_tier", value="budget",
                description="tier",
            ),
            SystemSettingORM(
                key="auto_default_model", value="gpt-4o-mini",
                description="auto default",
            ),
            SystemSettingORM(
                key="auto_default_fallbacks", value="",
                description="auto fallbacks",
            ),
        ])
        db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def test_get_all_settings(client):
    r = client.get(
        "/admin/system-settings",
        headers={"Authorization": "Bearer admin-test"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    keys = {row["key"] for row in rows}
    assert {
        "free_trial_tokens",
        "portal_signup_enabled",
        "credit_low_threshold",
        "trial_model_tier",
    }.issubset(keys)


def test_admin_update_setting(client):
    r = client.patch(
        "/admin/system-settings/free_trial_tokens",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "250000"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "250000"


def test_invalid_value_rejected(client):
    r = client.patch(
        "/admin/system-settings/free_trial_tokens",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "not-a-number"},
    )
    assert r.status_code in (400, 422)
    assert r.json()["error"]["code"] == "invalid_value"


def test_trial_tier_validator(client):
    r = client.patch(
        "/admin/system-settings/trial_model_tier",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "enterprise"},
    )
    assert r.status_code in (400, 422)

    r = client.patch(
        "/admin/system-settings/trial_model_tier",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "premium"},
    )
    assert r.status_code == 200
    assert r.json()["value"] == "premium"


def test_unknown_setting_not_editable(client):
    db = _Session()
    try:
        db.add(SystemSettingORM(
            key="some_future_knob", value="x", description="",
        ))
        db.commit()
    finally:
        db.close()

    r = client.patch(
        "/admin/system-settings/some_future_knob",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "y"},
    )
    assert r.status_code in (400, 422)
    assert r.json()["error"]["code"] == "setting_not_editable"


# ---------------------------------------------------------------------------
# Auto-routing defaults (B)
# ---------------------------------------------------------------------------


def test_auto_default_model_accepts_tool_capable_model(client):
    r = client.patch(
        "/admin/system-settings/auto_default_model",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "gpt-4o-mini"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "gpt-4o-mini"


def test_auto_default_model_rejects_unknown_model(client):
    r = client.patch(
        "/admin/system-settings/auto_default_model",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "gpt-9-imaginary"},
    )
    assert r.status_code in (400, 422)
    assert r.json()["error"]["code"] == "invalid_value"


def test_auto_default_model_rejects_tools_blind_model(client):
    """A model that can't call tools would silently drop memory/search."""
    r = client.patch(
        "/admin/system-settings/auto_default_model",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "abab5.5-chat"},
    )
    assert r.status_code in (400, 422)
    assert r.json()["error"]["code"] == "invalid_value"


def test_auto_fallbacks_normalized_and_validated(client):
    r = client.patch(
        "/admin/system-settings/auto_default_fallbacks",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": " gpt-4o , gpt-4o-mini ,gpt-4o "},
    )
    assert r.status_code == 200, r.text
    # Trimmed + deduped, order preserved.
    assert r.json()["value"] == "gpt-4o,gpt-4o-mini"

    r = client.patch(
        "/admin/system-settings/auto_default_fallbacks",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "gpt-4o,nope-9000"},
    )
    assert r.status_code in (400, 422)


def test_auto_default_may_not_exceed_max_tier(client):
    """min <= max: a standard-tier default under a budget ceiling is
    rejected, and naming the ceiling tells the admin what to fix."""
    r = client.patch(
        "/admin/system-settings/auto_default_model",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "gpt-4o"},          # standard, ceiling is budget
    )
    assert r.status_code in (400, 422), r.text
    assert r.json()["error"]["code"] == "invalid_value"

    # Raise the ceiling first, then the same value is accepted.
    assert client.patch(
        "/admin/system-settings/trial_model_tier",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "standard"},
    ).status_code == 200
    assert client.patch(
        "/admin/system-settings/auto_default_model",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "gpt-4o"},
    ).status_code == 200


def test_max_tier_cannot_drop_below_the_auto_default(client):
    assert client.patch(
        "/admin/system-settings/trial_model_tier",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "standard"},
    ).status_code == 200
    assert client.patch(
        "/admin/system-settings/auto_default_model",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "gpt-4o"},
    ).status_code == 200

    r = client.patch(
        "/admin/system-settings/trial_model_tier",
        headers={"Authorization": "Bearer admin-test"},
        json={"value": "budget"},
    )
    assert r.status_code in (400, 422)
