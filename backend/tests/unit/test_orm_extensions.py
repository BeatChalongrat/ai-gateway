"""Tests for the Sprint 2A ORM column extensions (migration 056).

Verifies the new tenant / model_config columns are declared on the ORM
with the expected defaults, are read/write-able, and that rows without
the new fields still load (backward compat).
"""

import uuid
from decimal import Decimal

from app.models.model_config import ModelConfigORM
from app.models.tenant import TenantORM


def test_tenant_new_columns_default(db_session):
    t = TenantORM(tenant_id=str(uuid.uuid4()), name="T", slug=f"t-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.flush()
    db_session.expire(t)

    row = db_session.get(TenantORM, t.tenant_id)
    assert row.dlp_media_gen_mask_identity is False
    assert row.monthly_video_quota is None
    assert row.monthly_audio_quota is None


def test_tenant_mask_identity_override(db_session):
    t = TenantORM(
        tenant_id=str(uuid.uuid4()),
        name="Strict",
        slug=f"s-{uuid.uuid4().hex[:8]}",
        dlp_media_gen_mask_identity=True,
        monthly_video_quota=10,
    )
    db_session.add(t)
    db_session.flush()
    db_session.expire(t)
    row = db_session.get(TenantORM, t.tenant_id)
    assert row.dlp_media_gen_mask_identity is True
    assert row.monthly_video_quota == 10


def test_model_config_video_fields(db_session):
    mc = ModelConfigORM(
        provider="mock",
        model="mock-video-1",
        supports_video_gen=True,
        cost_per_second=Decimal("0.07"),
    )
    db_session.add(mc)
    db_session.flush()
    db_session.expire(mc)

    row = db_session.query(ModelConfigORM).filter_by(model="mock-video-1").one()
    assert row.supports_video_gen is True
    assert row.supports_audio_gen is False  # server_default
    assert row.supports_tts is False
    assert abs(float(row.cost_per_second) - 0.07) < 1e-6
    assert row.cost_per_character is None
    assert row.cost_per_generation is None


def test_existing_model_config_row_backward_compat(db_session):
    """A row created without any media-gen fields still loads with defaults."""
    mc = ModelConfigORM(provider="openai", model="gpt-4o")
    db_session.add(mc)
    db_session.flush()
    db_session.expire(mc)
    row = db_session.query(ModelConfigORM).filter_by(model="gpt-4o").one()
    assert row.supports_video_gen is False
    assert row.cost_per_second is None
