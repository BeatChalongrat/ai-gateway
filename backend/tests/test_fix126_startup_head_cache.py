"""FIX126 O3 — the startup schema guard no longer re-reads every migration.

Measured on the dev bind mount: the guard spent ~1.5 s of every start and
every ``--reload`` loading all ~90 migration modules to learn the code head
(85% of the startup hooks' time). The answer is now cached by the files'
names + mtimes + sizes. These tests pin both halves: a warm start reads no
migration module, and any change to the migration files is seen.
"""

from __future__ import annotations

import os
import textwrap

import pytest
from alembic.config import Config

from app.core import schema_guard as sg


def _write_rev(versions, rev: str, down: str | None) -> None:
    (versions / f"{rev}_r.py").write_text(
        textwrap.dedent(
            f'''
            revision = "{rev}"
            down_revision = {down!r}
            branch_labels = None
            depends_on = None


            def upgrade():
                pass


            def downgrade():
                pass
            '''
        ),
        encoding="utf-8",
    )


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    scripts = tmp_path / "migrations"
    versions = scripts / "versions"
    versions.mkdir(parents=True)
    _write_rev(versions, "001", None)
    _write_rev(versions, "002", "001")
    ini = tmp_path / "alembic.ini"
    ini.write_text("[alembic]\nscript_location = %(here)s/migrations\n", encoding="utf-8")
    c = Config(str(ini))
    c._versions = versions  # for the test only
    return c


@pytest.fixture
def load_counter(monkeypatch):
    # Counts Alembic's own full read of the migration tree (what the old
    # guard did on every start).
    from alembic.script import ScriptDirectory

    calls = {"n": 0}
    real = ScriptDirectory.from_config.__func__

    def _counting(cls, cfg, *a, **k):
        calls["n"] += 1
        return real(cls, cfg, *a, **k)

    monkeypatch.setattr(ScriptDirectory, "from_config", classmethod(_counting))
    return calls


def test_second_start_reads_no_migration_module(cfg, load_counter):
    first = sg._code_head_and_revs(cfg)
    second = sg._code_head_and_revs(cfg)
    assert first == second == ("002", {"001", "002"})
    assert load_counter["n"] == 1


def test_a_new_migration_is_seen(cfg, load_counter):
    assert sg._code_head_and_revs(cfg)[0] == "002"
    _write_rev(cfg._versions, "003", "002")
    head, known = sg._code_head_and_revs(cfg)
    assert head == "003" and "003" in known
    assert load_counter["n"] == 2


def test_an_edited_migration_is_seen(cfg, load_counter):
    sg._code_head_and_revs(cfg)
    f = cfg._versions / "002_r.py"
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    sg._code_head_and_revs(cfg)
    assert load_counter["n"] == 2


def test_a_corrupt_cache_falls_back_to_the_full_read(cfg, load_counter, tmp_path):
    sg._code_head_and_revs(cfg)
    for p in (tmp_path / "tmp").glob("ai-gateway-alembic-heads-*.json"):
        p.write_text("{not json", encoding="utf-8")
    assert sg._code_head_and_revs(cfg) == ("002", {"001", "002"})
    assert load_counter["n"] == 2


def test_the_real_tree_head_matches_alembic():
    from alembic.script import ScriptDirectory

    cfg = sg._alembic_config()
    script = ScriptDirectory.from_config(cfg)
    truth = (script.get_current_head(), {r.revision for r in script.walk_revisions()})
    assert sg._code_head_and_revs(cfg) == truth
    assert sg._code_head_and_revs(cfg) == truth  # warm
