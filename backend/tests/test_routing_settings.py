"""Auto-routing preference reader.

Pins the properties routing depends on: the admin's default leads the
chain, the read is cached (and invalidated on save), and every failure
path degrades to the shipped default rather than to "cheapest capable".
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.system_setting import SystemSettingORM
from app.services import routing_settings

from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    routing_settings.clear_cache()
    db = _Session()
    try:
        db.query(SystemSettingORM).delete()
        db.commit()
    finally:
        db.close()
    yield
    routing_settings.clear_cache()


def _set(**rows):
    db = _Session()
    try:
        for key, value in rows.items():
            db.merge(SystemSettingORM(key=key, value=value, description=""))
        db.commit()
    finally:
        db.close()
    routing_settings.clear_cache()


def test_parse_model_list_trims_dedupes_and_keeps_order():
    assert routing_settings.parse_model_list(" a , b ,a, ") == ["a", "b"]
    assert routing_settings.parse_model_list("") == []
    assert routing_settings.parse_model_list(None) == []


def test_default_then_fallbacks_in_order():
    _set(
        auto_default_model="gpt-4o",
        auto_default_fallbacks="gpt-4.1,gpt-4o-mini",
    )
    db = _Session()
    try:
        assert routing_settings.get_auto_preference(db) == (
            "gpt-4o", "gpt-4.1", "gpt-4o-mini",
        )
    finally:
        db.close()


def test_missing_rows_fall_back_to_shipped_default():
    db = _Session()
    try:
        pref = routing_settings.get_auto_preference(db)
    finally:
        db.close()
    assert pref == routing_settings._HARDCODED_PREFERENCE
    assert pref[0] == "gpt-4o"


def test_blanked_out_value_still_yields_a_strong_default():
    _set(auto_default_model="", auto_default_fallbacks="")
    db = _Session()
    try:
        assert routing_settings.get_auto_preference(db) == (
            routing_settings._HARDCODED_PREFERENCE
        )
    finally:
        db.close()


def test_no_session_never_raises():
    assert routing_settings.get_auto_preference(None)[0] == "gpt-4o"


def test_reads_are_cached_until_cleared():
    _set(auto_default_model="gpt-4o", auto_default_fallbacks="")
    db = _Session()
    try:
        assert routing_settings.get_auto_preference(db)[0] == "gpt-4o"
        # Write behind the cache's back — the cached value stands…
        _write = _Session()
        try:
            _write.merge(SystemSettingORM(
                key="auto_default_model", value="gpt-4.1", description="",
            ))
            _write.commit()
        finally:
            _write.close()
        assert routing_settings.get_auto_preference(db)[0] == "gpt-4o"
        # …until the save path invalidates it (what the PATCH route does).
        routing_settings.clear_cache()
        assert routing_settings.get_auto_preference(db)[0] == "gpt-4.1"
    finally:
        db.close()
