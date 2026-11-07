"""FIX84 Part B — no mock model reaches a user-facing picker while the switch is off.

``mock`` is a test provider. The admin switch ``media_allow_mock``
(``system_settings``, default off) already stops it serving as a media
DESTINATION; FIX83 found ``mock-video-1`` (``is_active`` + ``show_in_chat``,
seeded) listed FIRST in the owner's model picker because the dedicated-media
branch of ``GET /v1/models`` never consulted that switch. The catalogue's two
axes stay what they are (``is_active`` = the API may call it, ``show_in_chat``
= the picker offers it); the switch is the third input the listing must obey.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
import uuid

from app.models.model_config import ModelConfigORM
from app.models.provider_key import ProviderKeyORM
from app.models.system_setting import SystemSettingORM
from app.services.media_gen.destinations import ALLOW_MOCK_KEY

client = TestClient(app)


def _media_row(provider: str, model: str) -> ModelConfigORM:
    return ModelConfigORM(
        provider=provider, model=model, supports_streaming=False, supports_tools=False,
        supports_vision=False, max_context_tokens=0, cost_tier="standard",
        latency_tier="standard", cost_per_input_token=0.0, cost_per_output_token=0.0,
        source="seed", is_manual_override=False, is_active=True, show_in_chat=True,
        supports_video_gen=True,
    )


def _set_allow_mock(session, value: str | None) -> None:
    row = session.get(SystemSettingORM, ALLOW_MOCK_KEY)
    if value is None:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(SystemSettingORM(key=ALLOW_MOCK_KEY, value=value))
    else:
        row.value = value
    session.commit()


@pytest.fixture()
def seeded_media_rows():
    from tests.conftest import _integration_db_session

    gen = _integration_db_session()
    session = next(gen)
    rows = [_media_row("mock", "fix84-mock-video"), _media_row("replicate", "fix84-real-video"),
            # FIX133 E6b — a real provider WITHOUT a valid key: its media
            # models must not be offered (sora-2 stayed listed after the
            # OpenAI key was turned off).
            _media_row("openai", "fix133-keyless-video"),
            ProviderKeyORM(id=str(uuid.uuid4()), provider_name="replicate", label="fix133-test",
                           api_key_encrypted="not-a-real-key", is_active=True, last_test_status="valid")]
    session.add_all(rows)
    session.commit()
    try:
        yield session
    finally:
        for r in rows:
            session.delete(r)
        _set_allow_mock(session, None)
        session.commit()
        try:
            next(gen)
        except StopIteration:
            pass


def _listed_ids() -> list[str]:
    body = client.get("/v1/models").json()
    return [m["id"] for m in body["data"]]


def test_mock_model_hidden_while_switch_is_off(seeded_media_rows):
    _set_allow_mock(seeded_media_rows, "false")
    ids = _listed_ids()
    assert "fix84-mock-video" not in ids
    # the real media model in the same branch is untouched
    assert "fix84-real-video" in ids


def test_mock_model_hidden_when_switch_is_absent(seeded_media_rows):
    # default is OFF — no row at all must behave like "false"
    _set_allow_mock(seeded_media_rows, None)
    assert "fix84-mock-video" not in _listed_ids()


def test_mock_model_listed_only_when_operator_allows_it(seeded_media_rows):
    _set_allow_mock(seeded_media_rows, "true")
    assert "fix84-mock-video" in _listed_ids()


def test_media_model_hidden_when_its_provider_has_no_valid_key(seeded_media_rows):
    """FIX133 E6b — the video/audio/tts listing is key-gated like the chat listing."""
    _set_allow_mock(seeded_media_rows, "false")
    ids = _listed_ids()
    assert "fix84-real-video" in ids  # replicate: a valid, active key was seeded
    assert "fix133-keyless-video" not in ids  # openai: no valid key
