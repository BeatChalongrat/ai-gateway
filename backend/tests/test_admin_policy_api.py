"""Stage 7: admin policy-profile and NER settings endpoint tests.

Covers GET /admin/policy-profiles, PATCH /admin/policy-profiles/{id}/category-actions,
GET /admin/ner/status, and PATCH /admin/settings.

Uses the shared integration TestClient from conftest.py, which already
injects a mock admin auth and an in-memory SQLite database seeded with a
test tenant and project.
"""

from fastapi.testclient import TestClient

from app.main import app as main_app
from app.services.dlp import ner_status


AUTH = {"Authorization": "Bearer gw_test_dummy"}


def _client() -> TestClient:
    return TestClient(main_app)


def test_get_policy_profiles_returns_default_and_strict() -> None:
    client = _client()
    resp = client.get("/admin/policy-profiles", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["profile_id"] for item in body["items"]}
    assert {"default", "strict"} <= ids

    default = next(i for i in body["items"] if i["profile_id"] == "default")
    # Built-in DEFAULT_PROFILE marks all canonical categories as MASK.
    assert default["category_actions"]["pii"] == "mask"
    assert default["category_actions"]["network"] == "mask"
    # Canonical categories are always present, even if the built-in omits them.
    assert "ner" in default["category_actions"]


def test_patch_category_action_persists_and_reflects_in_profile() -> None:
    client = _client()
    resp = client.patch(
        "/admin/policy-profiles/default/category-actions",
        headers=AUTH,
        json={"category": "network", "action": "block"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["category_actions"]["network"] == "block"

    # Re-reading should still show the override.
    resp2 = client.get("/admin/policy-profiles", headers=AUTH)
    default = next(
        i for i in resp2.json()["items"] if i["profile_id"] == "default"
    )
    assert default["category_actions"]["network"] == "block"

    # Reset so other tests see the clean default.
    client.patch(
        "/admin/policy-profiles/default/category-actions",
        headers=AUTH,
        json={"category": "network", "action": "mask"},
    )


def test_patch_invalid_category_returns_422() -> None:
    client = _client()
    resp = client.patch(
        "/admin/policy-profiles/default/category-actions",
        headers=AUTH,
        json={"category": "definitely-not-a-real-category", "action": "mask"},
    )
    assert resp.status_code == 422


def test_patch_invalid_action_returns_422() -> None:
    client = _client()
    resp = client.patch(
        "/admin/policy-profiles/default/category-actions",
        headers=AUTH,
        json={"category": "pii", "action": "deep-fry"},
    )
    assert resp.status_code == 422


def test_get_ner_status_when_disabled_returns_not_loaded() -> None:
    client = _client()
    # Make sure NER is disabled via settings, then reset module state.
    client.patch("/admin/settings", headers=AUTH, json={"ner_enabled": False})
    ner_status.reset()

    resp = client.get("/admin/ner/status", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "not_loaded"
    assert body["ner_enabled"] is False


def test_patch_settings_ner_enabled_triggers_model_load() -> None:
    client = _client()
    # Start from a known-disabled state.
    client.patch("/admin/settings", headers=AUTH, json={"ner_enabled": False})
    ner_status.reset()

    resp = client.patch(
        "/admin/settings",
        headers=AUTH,
        json={"ner_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["ner_enabled"] is True

    # After background task runs to completion the module state should no
    # longer be ``not_loaded``. TestClient runs background tasks synchronously
    # after the response is returned.
    status = ner_status.get_status().status
    assert status in ("loading", "ready", "error")

    # Teardown: disable to keep other tests clean.
    client.patch("/admin/settings", headers=AUTH, json={"ner_enabled": False})
    ner_status.reset()
