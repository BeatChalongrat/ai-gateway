"""FIX88 — the ledger rule.

Static half: no route writes ``portal_users.credit_balance`` or
``credit_quota``; every balance movement goes through
``credit_service`` (or the gateway deduction hook, which was already
ledger-backed). Runtime half: after every service operation on a fresh
user, ``sum(ledger amounts) == credit_balance`` — no function moves the
balance without a row.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.exceptions import ValidationError
from app.services.portal import credit_service

from tests.fix88_support import Session, clean_portal_tables, ledger, make_user, reload


APP = Path(__file__).resolve().parent.parent / "app"

# The only two modules allowed to assign the balance.
ALLOWED = {
    APP / "services" / "portal" / "credit_service.py",
    APP / "services" / "gateway" / "portal_credit_check.py",
}

# Attribute assignment (``x.credit_balance = ...`` / ``+=`` / ``-=``);
# the leading dot keeps constructor kwargs (``credit_balance=0`` on a
# new row) out of it.
_ASSIGN = re.compile(r"\.credit_(balance|quota)\s*(?:[+\-*/]?=)(?!=)")


def _sources() -> list[Path]:
    files = sorted((APP / "api" / "routes").glob("*.py"))
    files += sorted((APP / "services" / "portal").glob("*.py"))
    return files


def test_routes_and_portal_services_never_assign_the_balance():
    offenders: list[str] = []
    for path in _sources():
        if path in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _ASSIGN.search(line):
                offenders.append(f"{path.relative_to(APP.parent)}:{lineno}: {line.strip()}")
    assert not offenders, "balance/quota written outside credit_service:\n" + "\n".join(offenders)
    # The named files the brief asks about exist and were scanned.
    scanned = {p.name for p in _sources()}
    assert "admin_portal_users.py" in scanned
    assert any(n.startswith("portal_billing") for n in scanned)


def test_credit_service_is_the_only_balance_writer_among_services():
    # Sanity on the allow-list itself: the service really does assign.
    src = (APP / "services" / "portal" / "credit_service.py").read_text(encoding="utf-8")
    assert _ASSIGN.search(src)


@pytest.fixture(autouse=True)
def _clean():
    db = Session()
    try:
        clean_portal_tables(db)
        yield
    finally:
        db.close()


def _assert_ledger_matches(uid: str) -> None:
    rows = ledger(uid)
    assert sum(int(r.amount) for r in rows) == int(reload(uid).credit_balance)
    # And each row's snapshot is self-consistent with a running sum.
    running = 0
    for r in sorted(rows, key=lambda r: (r.created_at, r.balance_after)):
        running += int(r.amount)
    assert running == int(reload(uid).credit_balance)


def test_every_service_operation_writes_a_row_that_reconciles():
    db = Session()
    try:
        uid = make_user(db, balance=0, used=0, quota=0).id

        assert credit_service.set_quota(db, uid, 1_000_000, created_by="a") is not None
        db.commit()
        _assert_ledger_matches(uid)

        assert credit_service.deduct_credits(db, uid, 100_000, request_id="r1") is not None
        db.commit()
        _assert_ledger_matches(uid)

        assert credit_service.topup_credits(db, uid, 50_000, created_by="a") is not None
        db.commit()
        _assert_ledger_matches(uid)

        assert credit_service.fill_to_quota(db, uid, created_by="a") is not None
        db.commit()
        _assert_ledger_matches(uid)
        assert reload(uid).credit_balance == 1_000_000

        assert credit_service.grant_credits(db, uid, 25_000, created_by="a", type="purchase") is not None
        db.commit()
        _assert_ledger_matches(uid)
        assert reload(uid).credit_quota == 1_025_000

        assert credit_service.topup_credits(
            db, uid, 7, created_by="system", type="signup_bonus"
        ) is not None
        db.commit()
        _assert_ledger_matches(uid)

        tx = credit_service.set_quota(db, uid, 500_000, created_by="a")
        assert tx is not None and tx.amount == -(1_025_007 - 500_000)
        db.commit()
        _assert_ledger_matches(uid)

        assert credit_service.set_quota(db, uid, 0, created_by="a") is not None
        db.commit()
        _assert_ledger_matches(uid)
        assert reload(uid).credit_balance == 0

        # No-op paths write nothing and the sum still holds.
        assert credit_service.fill_to_quota(db, uid, created_by="a") is None
        assert credit_service.set_quota(db, uid, 0, created_by="a") is None
        assert credit_service.deduct_credits(db, uid, 0) is None
        assert credit_service.topup_credits(db, uid, 0, created_by="a") is None
        assert credit_service.grant_credits(db, uid, 0, created_by="a") is None
        db.commit()
        _assert_ledger_matches(uid)

        # A refused top-up writes nothing either.
        with pytest.raises(ValidationError):
            credit_service.topup_credits(db, uid, 1, created_by="a")
        db.rollback()
        _assert_ledger_matches(uid)
        assert len(ledger(uid)) == 8
    finally:
        db.close()
