"""Phase 4A.2.6.1 — in-chat secure-calc orchestration (intent + operand
collection from allocation records + compute/mint). No provider/orchestrator
process() here; the live chat flow is proven by the staging E2E."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import fakeredis
import pytest

from app.domain.dlp import EnforcementAction
from app.services.dlp.numeric_context import CONFIDENTIAL_NUMERIC
from app.services.dlp.secure_calc import (
    CalcError,
    SecureCalcResultStore,
    orchestrate_secure_calc,
)
import app.services.dlp.secure_calc as sc
from app.services.dlp.secure_calc_intent import detect_calc_operation, CalcOperation
from app.services.dlp.vault_v2 import VaultScope


def _scope():
    return VaultScope(tenant_id="t", project_id="p", actor_id="a", conversation_id="c")


def _rec(token, raw, *, nclass=CONFIDENTIAL_NUMERIC, category="financial",
         action=EnforcementAction.MASK, detector="numeric_confidential"):
    return SimpleNamespace(token=token, raw_value=raw, category=category, action=action,
                           detector=detector, entity_type="personal_finance",
                           policy_v2={"numeric_class": nclass}, message_index=0)


def _run(text, records, monkeypatch, op_currency=False):
    monkeypatch.setattr(sc, "secure_calc_result_store",
                        SecureCalcResultStore(fakeredis.FakeStrictRedis(decode_responses=True)))
    return asyncio.run(orchestrate_secure_calc(
        scope=_scope(), records=records, user_text=text, created_at=1))


# ── intent ──────────────────────────────────────────────────────────────────

def test_intent_detection():
    assert detect_calc_operation("เงินเดือนผม 50000 โบนัส 30000 รวมให้หน่อย") == CalcOperation.SUM
    assert detect_calc_operation("ค่าเฉลี่ยเท่าไหร่") == CalcOperation.AVERAGE
    assert detect_calc_operation("ส่วนต่างเท่าไหร่") == CalcOperation.DIFFERENCE
    assert detect_calc_operation("sum these please") == CalcOperation.SUM
    assert detect_calc_operation("รวม และ เฉลี่ย ด้วย") is None  # ambiguous → deny
    assert detect_calc_operation("สวัสดีครับ") is None             # no intent


# ── orchestration ─────────────────────────────────────────────────────────────

def test_orchestrate_sum(monkeypatch):
    recs = [_rec("<r0>", "50000"), _rec("<r1>", "30000")]
    out = _run("รวมเงินเดือนกับโบนัสให้หน่อย", recs, monkeypatch)
    assert out is not None
    assert out.token == "<CALC_0>"
    assert out.raw_value == "80000"
    assert out.canonical == "ผลรวมคือ <CALC_0>"
    # canonical carries the token, NEVER the raw result.
    assert "80000" not in out.canonical and "50000" not in out.canonical


def test_orchestrate_no_intent_returns_none(monkeypatch):
    recs = [_rec("<r0>", "50000"), _rec("<r1>", "30000")]
    assert _run("ขอบคุณครับ", recs, monkeypatch) is None


def test_orchestrate_insufficient_operands_returns_none(monkeypatch):
    recs = [_rec("<r0>", "50000")]  # sum needs >= 2
    assert _run("รวมให้หน่อย", recs, monkeypatch) is None


def test_orchestrate_ineligible_operands_returns_none(monkeypatch):
    # OTP/secret operands are not calc-eligible → < 2 eligible → no calc.
    recs = [_rec("<r0>", "123456", nclass=None, category="secret", detector="numeric_secret"),
            _rec("<r1>", "654321", nclass=None, category="secret", detector="numeric_secret")]
    assert _run("รวมให้หน่อย", recs, monkeypatch) is None


def test_orchestrate_division_by_zero_raises(monkeypatch):
    recs = [_rec("<r0>", "80000"), _rec("<r1>", "0")]
    with pytest.raises(CalcError) as ei:
        _run("หารกันหน่อย", recs, monkeypatch)
    assert ei.value.code == "division_by_zero"
