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
