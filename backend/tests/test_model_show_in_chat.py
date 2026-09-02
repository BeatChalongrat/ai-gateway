"""FIX47 Part C — the catalogue's second axis, ``show_in_chat``.

``is_active`` decides whether the API may call a model; ``show_in_chat``
decides whether the chat picker offers it. They are independent columns
(migration 080), edited from the admin UI, and the value rides on
``/v1/models`` for chat-edge / the clients. There is no list of models in
code anywhere on this path — that is the point.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.domain.routing import ModelCandidate
from app.models.model_config import ModelConfigORM
from app.services.providers.model_registry import ModelRegistry


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _row(model: str, *, show_in_chat: bool = True, is_active: bool = True) -> ModelConfigORM:
    return ModelConfigORM(
        provider="openai", model=model, supports_streaming=True, supports_tools=True,
        supports_vision=False, max_context_tokens=128000, cost_tier="standard",
        latency_tier="standard", cost_per_input_token=0.0, cost_per_output_token=0.0,
        source="manual", is_manual_override=True, is_active=is_active, show_in_chat=show_in_chat,
    )


def test_the_column_defaults_to_shown(db):
    db.add(ModelConfigORM(provider="openai", model="m", supports_streaming=True, supports_tools=False,
                          supports_vision=False, max_context_tokens=1, cost_tier="standard",
                          latency_tier="standard", cost_per_input_token=0.0, cost_per_output_token=0.0))
    db.commit()
    assert db.query(ModelConfigORM).one().show_in_chat is True


@pytest.mark.asyncio
async def test_registry_carries_show_in_chat_and_hidden_models_stay_routable(db):
    db.add_all([_row("shown"), _row("hidden", show_in_chat=False), _row("off", is_active=False)])
    db.commit()
    reg = ModelRegistry()
    await reg.reload(db)
    by_id = {c.model: c for c in reg.get_all_candidates()}
    # is_active still gates the API pool; show_in_chat does not
    assert set(by_id) == {"shown", "hidden"}
    assert by_id["shown"].show_in_chat is True
    assert by_id["hidden"].show_in_chat is False


def test_candidate_default_is_shown():
    assert ModelCandidate(provider="p", model="m").show_in_chat is True


def test_public_models_route_serialises_the_flag(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.api.routes import public_models
    from app.services.providers import model_registry

    cands = [ModelCandidate(provider="openai", model="shown-m", show_in_chat=True),
             ModelCandidate(provider="openai", model="hidden-m", show_in_chat=False)]
    monkeypatch.setattr(public_models, "get_all_candidates", lambda: cands)
    monkeypatch.setattr(model_registry, "get_all_candidates", lambda: cands)
    monkeypatch.setattr(public_models.ProviderKeyService, "providers_with_valid_keys", lambda self, s: {"openai"})
    body = TestClient(app).get("/v1/models").json()
    flags = {m["id"]: m.get("show_in_chat") for m in body["data"]}
    assert flags["shown-m"] is True
    assert flags["hidden-m"] is False
    assert flags["auto"] is None  # the synthetic entry carries no flag


def test_admin_update_schema_accepts_show_in_chat_independently_of_is_active():
    from app.api.routes.admin_model_discovery import ModelConfigUpdate

    body = ModelConfigUpdate(show_in_chat=False)
    assert body.model_dump(exclude_unset=True) == {"show_in_chat": False}


def test_no_model_list_is_embedded_in_the_show_in_chat_path():
    # The rule the round was written under: the picker's list is data.
    import inspect

    from app.api.routes import admin_model_discovery, public_models
    from app.services.providers import model_registry

    for mod in (public_models, admin_model_discovery):
        src = inspect.getsource(mod)
        assert "show_in_chat" in src
        assert "SHOW_IN_CHAT" not in src  # no constant list
    assert "show_in_chat=getattr(row" in inspect.getsource(model_registry)
