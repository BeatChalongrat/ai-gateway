"""FIX67 — admin validator for media_order (mock guard, known model, no upgrade)."""

import pytest

from app.api.routes.admin_system_settings import _check_media_order, _validate_media_order
from app.core.exceptions import ValidationError
from app.models.model_config import ModelConfigORM
from app.models.system_setting import SystemSettingORM


@pytest.fixture()
def seeded(db_session):
    db_session.add_all([
        ModelConfigORM(provider="openai", model="gpt-image-1", supports_image_gen=True, cost_per_input_token=5e-06, is_active=False),
        ModelConfigORM(provider="openai", model="gpt-image-1-mini", supports_image_gen=True, cost_per_input_token=2e-06, is_active=False),
        ModelConfigORM(provider="mock", model="mock-image-1", supports_image_gen=True, cost_per_input_token=0.0, is_active=False),
        ModelConfigORM(provider="openai", model="gpt-4o", supports_image_gen=False, is_active=True),
    ])
    db_session.commit()
    return db_session


def _allow_mock(db_session, on: bool):
    db_session.add(SystemSettingORM(key="media_allow_mock", value="true" if on else "false"))
    db_session.commit()


def test_format_validator():
    assert _validate_media_order("openai:gpt-image-1 , mock:mock-image-1") == "openai:gpt-image-1, mock:mock-image-1"
    assert _validate_media_order("") == ""
    with pytest.raises(ValidationError):
        _validate_media_order("nonsense")


def test_valid_order_ok(seeded):
    _allow_mock(seeded, False)
    _check_media_order("media_order:image", "openai:gpt-image-1", seeded)  # no raise


def test_mock_refused_when_disabled(seeded):
    _allow_mock(seeded, False)
    with pytest.raises(ValidationError, match="media_allow_mock"):
        _check_media_order("media_order:image", "openai:gpt-image-1, mock:mock-image-1", seeded)


def test_mock_allowed_when_enabled(seeded):
    _allow_mock(seeded, True)
    _check_media_order("media_order:image", "openai:gpt-image-1, mock:mock-image-1", seeded)  # no raise


def test_unknown_model_rejected(seeded):
    _allow_mock(seeded, False)
    with pytest.raises(ValidationError, match="not a known image model"):
        _check_media_order("media_order:image", "openai:does-not-exist", seeded)


def test_non_image_model_rejected(seeded):
    # gpt-4o exists but is not an image model.
    _allow_mock(seeded, False)
    with pytest.raises(ValidationError, match="not a known image model"):
        _check_media_order("media_order:image", "openai:gpt-4o", seeded)


def test_more_than_two_tiers_rejected(seeded):
    _allow_mock(seeded, True)
    with pytest.raises(ValidationError, match="At most"):
        _check_media_order(
            "media_order:image",
            "openai:gpt-image-1, mock:mock-image-1, openai:gpt-image-1-mini",
            seeded,
        )


def test_cost_upgrade_rejected(seeded):
    # mock (0) primary → openai (5e-6) fallback would be a cost UPGRADE.
    _allow_mock(seeded, True)
    with pytest.raises(ValidationError, match="costlier"):
        _check_media_order("media_order:image", "mock:mock-image-1, openai:gpt-image-1", seeded)
