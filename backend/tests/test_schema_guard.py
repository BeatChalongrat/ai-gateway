"""FIX 3 — startup schema-drift guard.

Unit-tests the pure decision (db_rev vs code head, per mode), the action
dispatch (fail raises, upgrade runs, in-sync no-ops), the advisory-locked
autoupgrade wrapper (multi-worker safety), the env-based mode resolver, and
the Part-C error-handler mapping (undefined-column → schema_out_of_date).

None of this needs a live DB: the revision comparison is fed explicit values,
and the runner's DB/Alembic reads are injectable/faked.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.schema_guard import (
    SCHEMA_MIGRATE_LOCK_ID,
    SchemaDriftError,
    _dispatch_action,
    _locked_upgrade,
    decide_schema_action,
)

HEAD = "065"
KNOWN = {"062", "063", "064", "065"}


# ---------------------------------------------------------------------------
# decide_schema_action — the pure core
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["autoupgrade", "failclosed", "warn", "off"])
def test_in_sync_is_ok_in_every_mode(mode):
    action, _ = decide_schema_action(HEAD, HEAD, KNOWN, mode)
    assert action == "ok"


def test_behind_autoupgrade_upgrades():
    action, msg = decide_schema_action("062", HEAD, KNOWN, "autoupgrade")
    assert action == "upgrade"
    assert "062" in msg and "065" in msg


def test_behind_failclosed_fails_with_actionable_message():
    action, msg = decide_schema_action("062", HEAD, KNOWN, "failclosed")
    assert action == "fail"
    # Names the gap and the fix — a human can act in 5 seconds.
    assert "062" in msg and "065" in msg
    assert "alembic upgrade head" in msg


def test_behind_warn_proceeds_loudly():
    action, msg = decide_schema_action("062", HEAD, KNOWN, "warn")
    assert action == "warn"
    assert "062" in msg


def test_behind_off_is_ok():
    action, _ = decide_schema_action("062", HEAD, KNOWN, "off")
    assert action == "ok"


def test_empty_db_is_treated_as_behind():
    # No alembic_version row yet → needs the full upgrade.
    assert decide_schema_action(None, HEAD, KNOWN, "autoupgrade")[0] == "upgrade"
    assert decide_schema_action(None, HEAD, KNOWN, "failclosed")[0] == "fail"


def test_ahead_warns_and_proceeds():
    # DB at a revision this code image has never seen (rollback scenario).
    action, msg = decide_schema_action("099", HEAD, KNOWN, "failclosed")
    assert action == "warn"
    assert "AHEAD" in msg


def test_unrecognised_mode_fails_closed_when_behind():
    # Safety: an unknown mode must not silently disable the guard.
    assert decide_schema_action("062", HEAD, KNOWN, "banana")[0] == "fail"


def test_no_code_head_is_ok():
    assert decide_schema_action(None, None, set(), "failclosed")[0] == "ok"


# ---------------------------------------------------------------------------
# _dispatch_action — side effects
# ---------------------------------------------------------------------------


def test_dispatch_fail_raises_and_never_upgrades():
    called = []
    with pytest.raises(SchemaDriftError):
        _dispatch_action("fail", "boom", upgrade_fn=lambda: called.append(1))
    assert called == []


def test_dispatch_upgrade_runs_upgrade_fn_once():
    called = []
    _dispatch_action("upgrade", "go", upgrade_fn=lambda: called.append(1))
    assert called == [1]


@pytest.mark.parametrize("action", ["ok", "warn"])
def test_dispatch_ok_and_warn_never_upgrade(action):
    called = []
    _dispatch_action(action, "info", upgrade_fn=lambda: called.append(1))
    assert called == []


# ---------------------------------------------------------------------------
# _locked_upgrade — multi-worker advisory lock
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def execute(self, stmt, params=None):
        self._sink.append((str(stmt), params))

        class _R:  # scalar-ish, unused
            pass

        return _R()

    def commit(self):
        self._sink.append(("COMMIT", None))

    def close(self):
        self._sink.append(("CLOSE", None))


class _FakeEngine:
    def __init__(self, dialect_name, sink):
        self.dialect = SimpleNamespace(name=dialect_name)
        self._sink = sink

    def connect(self):
        return _FakeConn(self._sink)


def test_locked_upgrade_non_postgres_skips_lock(monkeypatch):
    import alembic.command as alembic_command
    import app.core.schema_guard as sg  # noqa: F401

    upgrades = []
    monkeypatch.setattr(alembic_command, "upgrade", lambda cfg, rev: upgrades.append(rev))
    sink: list = []
    engine = _FakeEngine("sqlite", sink)

    _locked_upgrade(engine, cfg=object())

    assert upgrades == ["head"]
    # No advisory-lock SQL issued on non-Postgres.
    assert not any("advisory" in s for s, _ in sink)


def test_locked_upgrade_postgres_holds_advisory_lock(monkeypatch):
    import alembic.command as alembic_command
    import app.core.schema_guard as sg  # noqa: F401

    upgrades = []
    monkeypatch.setattr(alembic_command, "upgrade", lambda cfg, rev: upgrades.append(rev))
    # Force the "not yet at head" path so the upgrade actually runs under lock.
    monkeypatch.setattr(sg, "_read_db_revision", lambda engine: "062")
    monkeypatch.setattr(sg, "_code_head_and_revs", lambda cfg: (HEAD, KNOWN))

    sink: list = []
    engine = _FakeEngine("postgresql", sink)
    _locked_upgrade(engine, cfg=object())

    sql = " ".join(s for s, _ in sink)
    assert "pg_advisory_lock" in sql          # acquired
    assert "pg_advisory_unlock" in sql        # released
    assert upgrades == ["head"]               # migration ran while held
    # Lock + unlock use the documented id.
    ids = [p.get("id") for s, p in sink if p and "advisory" in s]
    assert ids and all(i == SCHEMA_MIGRATE_LOCK_ID for i in ids)


def test_locked_upgrade_postgres_skips_when_already_head(monkeypatch):
    import alembic.command as alembic_command
    import app.core.schema_guard as sg  # noqa: F401

    upgrades = []
    monkeypatch.setattr(alembic_command, "upgrade", lambda cfg, rev: upgrades.append(rev))
    # A racing worker already migrated: under the lock we observe head.
    monkeypatch.setattr(sg, "_read_db_revision", lambda engine: HEAD)
    monkeypatch.setattr(sg, "_code_head_and_revs", lambda cfg: (HEAD, KNOWN))

    sink: list = []
    _locked_upgrade(_FakeEngine("postgresql", sink), cfg=object())

    assert upgrades == []  # no double migration
    sql = " ".join(s for s, _ in sink)
    assert "pg_advisory_unlock" in sql  # still releases the lock


# ---------------------------------------------------------------------------
# schema_drift_mode_effective — env resolver
# ---------------------------------------------------------------------------


def test_mode_resolver_defaults_by_env(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "schema_drift_mode", "", raising=False)
    monkeypatch.setattr(settings, "app_env", "development", raising=False)
    assert settings.schema_drift_mode_effective == "autoupgrade"

    monkeypatch.setattr(settings, "app_env", "production", raising=False)
    assert settings.schema_drift_mode_effective == "failclosed"


def test_mode_resolver_explicit_wins(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development", raising=False)
    monkeypatch.setattr(settings, "schema_drift_mode", "failclosed", raising=False)
    assert settings.schema_drift_mode_effective == "failclosed"


def test_mode_resolver_bad_value_is_safe(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development", raising=False)
    monkeypatch.setattr(settings, "schema_drift_mode", "nonsense", raising=False)
    # Unrecognised → failclosed, never silently disabled.
    assert settings.schema_drift_mode_effective == "failclosed"


# ---------------------------------------------------------------------------
# Part C — error-handler mapping (undefined column/table → schema_out_of_date)
# ---------------------------------------------------------------------------


def test_is_schema_out_of_date_detects_sqlstate():
    from app.middleware.error_handler import _is_schema_out_of_date

    # Direct psycopg-like error.
    undef_col = SimpleNamespace(sqlstate="42703")
    assert _is_schema_out_of_date(undef_col) is True

    # SQLAlchemy-style wrapper: ProgrammingError.orig -> psycopg error.
    wrapper = SimpleNamespace(orig=SimpleNamespace(pgcode="42P01"))
    assert _is_schema_out_of_date(wrapper) is True

    # Buried in the cause chain (__cause__ must be a real BaseException).
    class _PgError(Exception):
        def __init__(self, sqlstate):
            super().__init__(sqlstate)
            self.sqlstate = sqlstate

    outer = RuntimeError("boom")
    outer.__cause__ = _PgError("42703")
    assert _is_schema_out_of_date(outer) is True


def test_is_schema_out_of_date_negative():
    from app.middleware.error_handler import _is_schema_out_of_date

    assert _is_schema_out_of_date(ValueError("nope")) is False
    assert _is_schema_out_of_date(SimpleNamespace(sqlstate="23505")) is False  # unique violation


@pytest.mark.asyncio
async def test_handler_maps_undefined_column_to_schema_out_of_date():
    import json

    from starlette.requests import Request

    from app.middleware.error_handler import handle_unhandled_exception

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/chat/completions",
        "headers": [],
        "query_string": b"",
        "state": {},
    }
    request = Request(scope)

    exc = SimpleNamespace(sqlstate="42703")  # UndefinedColumn
    resp = await handle_unhandled_exception(request, exc)  # type: ignore[arg-type]

    assert resp.status_code == 500
    body = json.loads(bytes(resp.body))
    assert body["error"]["code"] == "schema_out_of_date"
    assert "migration" in body["error"]["message"].lower()
