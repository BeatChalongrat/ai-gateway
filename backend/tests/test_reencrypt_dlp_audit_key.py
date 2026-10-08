"""Rotating ``DLP_AUDIT_KEY`` without losing what it protects.

The key encrypts one column — ``request_logs.input_raw_encrypted``, the prompt as typed — and a row written
under the old key does not open under the new one. ``scripts/reencrypt_dlp_audit_key.py`` rewrites those
rows. Held here: the script's key rule is the application's own; a row under the old key opens under the
new one afterwards and no longer under the old; a second run rewrites nothing; a row that opens under
neither key is counted and left, never guessed at; and the keys come from the environment by NAME — the
script has no way to be given one on a command line, and says nothing of them when they are missing.
"""

from __future__ import annotations

import pytest

from app.core import dlp_audit_crypto as app_crypto
from scripts import reencrypt_dlp_audit_key as rk

OLD = "11" * 32
NEW = "22" * 32
WORDS = "ขอซื้อเก้าอี้ 20 ตัว ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม"


def _app_seal(monkeypatch, key: str, plaintext: str) -> str:
    """A token made by the application itself with ``key`` as its DLP_AUDIT_KEY."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_audit_key", key, raising=False)
    app_crypto.reset_cache()
    try:
        return app_crypto.encrypt(plaintext)
    finally:
        app_crypto.reset_cache()


@pytest.mark.parametrize("key", [OLD, "not hex at all — a passphrase", "abcd"])
def test_the_scripts_key_is_the_applications_key(monkeypatch, key):
    token = _app_seal(monkeypatch, key, WORDS)
    assert rk.opens(rk.key_from(key), token) == WORDS
    assert rk.opens(rk.key_from(key + "x"), token) is None


def test_rows_under_the_old_key_open_under_the_new_one_and_no_longer_under_the_old(monkeypatch):
    old, new = rk.key_from(OLD), rk.key_from(NEW)
    rows = [(1, _app_seal(monkeypatch, OLD, WORDS)), (2, _app_seal(monkeypatch, OLD, "second")), (3, _app_seal(monkeypatch, NEW, "already")),
            (4, ""), (5, "bm90IGEgdG9rZW4gYXQgYWxs"), (6, _app_seal(monkeypatch, "33" * 32, "a third key's"))]
    updates, n = rk.rewrite(rows, old, new)
    assert n == {"already_new": 1, "to_rewrite": 2, "unreadable": 2, "empty": 1}
    assert [i for i, _ in updates] == [1, 2]
    by_id = dict(updates)
    assert rk.opens(new, by_id[1]) == WORDS and rk.opens(new, by_id[2]) == "second"
    assert rk.opens(old, by_id[1]) is None, "the rewritten row still opens under the key that leaked"
    # … and the application reads what the script wrote, once it runs with the new key
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_audit_key", NEW, raising=False)
    app_crypto.reset_cache()
    try:
        assert app_crypto.decrypt(by_id[1]) == WORDS
    finally:
        app_crypto.reset_cache()

    # a second run: nothing left to rewrite
    after = [(i, by_id.get(i, t)) for i, t in rows]
    again, n2 = rk.rewrite(after, old, new)
    assert again == [] and n2["to_rewrite"] == 0 and n2["already_new"] == 3 and n2["unreadable"] == 2


def test_the_keys_come_from_the_environment_by_name_and_are_never_said(monkeypatch, capsys):
    monkeypatch.delenv("DLP_AUDIT_KEY_OLD", raising=False)
    monkeypatch.setenv("DLP_AUDIT_KEY_NEW", NEW)
    assert rk.main([]) == 2
    said = capsys.readouterr()
    assert NEW not in said.out + said.err and "DLP_AUDIT_KEY_OLD" in said.err

    monkeypatch.setenv("DLP_AUDIT_KEY_OLD", NEW)
    assert rk.main([]) == 2, "the same key twice is not a rotation"
    said = capsys.readouterr()
    assert NEW not in said.out + said.err

    # there is no option that takes a key — and one given by mistake is not echoed back
    for wrong in (["--old", OLD], ["--new", NEW], [OLD]):
        assert rk.main(wrong) == 2
        said = capsys.readouterr()
        assert OLD not in said.out + said.err and NEW not in said.out + said.err


def test_the_queries_name_the_tables_own_columns():
    """The first version asked for ``request_logs.id``; the table has no such column and the dry run stopped
    on live. Every column a query names is one the model has, and a row is named by the model's own key."""
    import re

    from app.models.request_log import RequestLogORM

    table = RequestLogORM.__table__
    assert table.name == "request_logs"
    assert rk.KEY_COLUMN in [c.name for c in table.primary_key.columns]
    known = {c.name for c in table.columns}
    words = r"\b(SELECT|FROM|WHERE|IS|NOT|NULL|ORDER|BY|UPDATE|SET|ANY|request_logs)\b|:[a-z]+"
    for sql in (rk.SELECT_IDS, rk.SELECT_ROWS, rk.UPDATE_ROW):
        assert " request_logs " in sql
        named = set(re.findall(r"[a-z_]+", re.sub(words, " ", sql)))
        assert named and named <= known, (sql, named - known)
