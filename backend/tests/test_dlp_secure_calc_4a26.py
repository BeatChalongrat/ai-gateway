"""Phase 4A.2.6 — Secure Calculator: engine, result tokens, endpoint, restore.

Proves the security properties WITHOUT a provider/orchestrator: the calculator
computes only over Vault V2 eligible tokens, mints <CALC_n>, never returns raw
operands/result in its response, restores only to the owner, and fails closed
on ineligible/secret/chained/bad-math inputs. Sentinel absence is asserted on
the calc-endpoint response + logs.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import fakeredis
import pytest
from fastapi.responses import JSONResponse

from app.domain.dlp import EnforcementAction
from app.services.dlp.numeric_context import CONFIDENTIAL_NUMERIC
from app.services.dlp.vault_v2 import build_vault_entry
import app.services.dlp.secure_calc as sc
from app.services.dlp.secure_calc import (
    CalcError,
    CalcOperation,
    SecureCalcResultStore,
    compute,
    format_result,
    is_calc_token,
    parse_operand,
)
import app.api.routes.internal_dlp as endpoint

S50 = "SECRET-CALC-SENTINEL-50000"   # stands in for raw operand 50000
S30 = "SECRET-CALC-SENTINEL-30000"   # stands in for raw operand 30000
SRES = "CALC-RESULT-SENTINEL-80000"  # stands in for raw result 80000


# ── engine ────────────────────────────────────────────────────────────────

def test_engine_operations():
    assert format_result(compute(CalcOperation.SUM, [Decimal("50000"), Decimal("30000")])) == "80000"
    assert format_result(compute(CalcOperation.ADD, [Decimal("1"), Decimal("2"), Decimal("3")])) == "6"
    assert format_result(compute(CalcOperation.AVERAGE, [Decimal("50000"), Decimal("30000")])) == "40000"
    assert format_result(compute(CalcOperation.DIFFERENCE, [Decimal("50000"), Decimal("30000")])) == "20000"
    assert format_result(compute(CalcOperation.MULTIPLY, [Decimal("2"), Decimal("3")])) == "6"
    assert format_result(compute(CalcOperation.DIVIDE, [Decimal("80000"), Decimal("2")])) == "40000"
    assert format_result(compute(CalcOperation.PERCENTAGE_CHANGE, [Decimal("50000"), Decimal("55000")])) == "10"


def test_engine_fail_closed():
    for op, ops, code in [
        (CalcOperation.DIVIDE, [Decimal(1), Decimal(0)], "division_by_zero"),
        (CalcOperation.PERCENTAGE_CHANGE, [Decimal(0), Decimal(5)], "percentage_change_base_zero"),
        (CalcOperation.SUM, [], "no_operands"),
        (CalcOperation.DIVIDE, [Decimal(1)], "operation_requires_two_operands"),
    ]:
        with pytest.raises(CalcError) as ei:
            compute(op, ops)
        assert ei.value.code == code


def test_parse_operand():
    assert parse_operand("50,000") == Decimal("50000")
    for bad in ["abc", "5+5", "", "1e9", "$50"]:
        with pytest.raises(CalcError):
            parse_operand(bad)


def test_calc_token_namespace_disjoint():
    assert is_calc_token("<CALC_0>") and is_calc_token("<CALC_12>")
    for other in ["<r0>", "<<EMAIL_1>>", "<CALC_>", "CALC_0", "<calc_0>"]:
        assert not is_calc_token(other)


# ── result store (fakeredis) ────────────────────────────────────────────────

def _scope():
    from app.services.dlp.vault_v2 import VaultScope
    return VaultScope(tenant_id="t", project_id="p", actor_id="a", conversation_id="c")


def test_result_store_mint_and_owner_restore():
    store = SecureCalcResultStore(fakeredis.FakeStrictRedis(decode_responses=True))
    scope = _scope()
    tok, entry = asyncio.run(store.mint_and_store_async(
        scope, raw_value="80000", operation="sum", operand_tokens=["<r0>", "<r1>"], created_at=1))
    assert tok == "<CALC_0>"
    assert entry.calculation_eligible is False  # no chaining
    assert entry.never_restore_to_provider is True
    got = asyncio.run(store.resolve_for_user_async(scope, [tok]))
    assert got[tok] == "80000"
    # Wrong scope → not found.
    from app.services.dlp.vault_v2 import VaultScope
    other = VaultScope(tenant_id="t", project_id="p", actor_id="OTHER", conversation_id="c")
    assert asyncio.run(store.resolve_for_user_async(other, [tok])) == {}


# ── endpoint ────────────────────────────────────────────────────────────────

def _auth(internal=True):
    return SimpleNamespace(tenant_id="tenant-a", project_id="proj-a", key_id="k",
                           portal_user_id="actor-a", internal_trusted=internal,
                           trusted_conversation_id="conv-a")


def _eligible(token, raw):
    return build_vault_entry(token=token, raw_value=raw, category="financial",
                             action=EnforcementAction.MASK, created_at=1,
                             detector="numeric_confidential", numeric_class=CONFIDENTIAL_NUMERIC)


def _otp(token, raw):
    return build_vault_entry(token=token, raw_value=raw, category="secret",
                             action=EnforcementAction.MASK, created_at=1, detector="numeric_secret")


class _FakeVault:
    def __init__(self, entries):
        self._e = entries

    async def get_entries_scoped_async(self, scope, tokens):
        return {t: self._e[t] for t in tokens if t in self._e}


def _calc(body, auth, secret, monkeypatch, *, entries, all_on=True, scope_mode="global", allowlist=""):
    for f in ("dlp_secure_calc_enabled", "dlp_vault_v2_enabled",
              "dlp_policy_engine_v2_enabled", "dlp_canonical_artifact_enabled"):
        monkeypatch.setattr(endpoint.settings, f, all_on, raising=False)
    monkeypatch.setattr(endpoint.settings, "internal_service_secret", "s", raising=False)
    # Phase 4A.2.7 — tenant scope gate. Default "global" so the operand-logic
    # tests exercise the calc; scope-specific tests override.
    monkeypatch.setattr(endpoint.settings, "dlp_feature_scope_mode", scope_mode, raising=False)
    monkeypatch.setattr(endpoint.settings, "dlp_feature_allowlist", allowlist, raising=False)
    monkeypatch.setattr(endpoint, "conversation_vault_v2", _FakeVault(entries))
    monkeypatch.setattr(sc, "secure_calc_result_store",
                        SecureCalcResultStore(fakeredis.FakeStrictRedis(decode_responses=True)))
    return asyncio.run(endpoint.secure_calc(body=body, auth=auth, x_internal_service_secret=secret))


def test_endpoint_sum_mints_calc_token_no_raw(monkeypatch):
    # Operands are real confidential numerics; the result (80000) and the raw
    # operands (50000/30000) must NEVER appear in the calc-endpoint response —
    # only the <CALC_0> token does.
    entries = {"<r0>": _eligible("<r0>", "50000"), "<r1>": _eligible("<r1>", "30000")}
    body = endpoint._CalcRequest(operation="sum", operand_tokens=["<r0>", "<r1>"], consumer="managed_webchat")
    res = _calc(body, _auth(), "s", monkeypatch, entries=entries)
    assert res["status"] == "complete" and res["result_token"] == "<CALC_0>"
    blob = str(res)
    assert "50000" not in blob and "30000" not in blob and "80000" not in blob


def test_endpoint_denies_ineligible_secret_operand(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", S50), "<r1>": _otp("<r1>", "123456")}
    body = endpoint._CalcRequest(operation="sum", operand_tokens=["<r0>", "<r1>"], consumer="managed_webchat")
    res = _calc(body, _auth(), "s", monkeypatch, entries=entries)
    assert isinstance(res, JSONResponse) and res.status_code == 422


def test_endpoint_denies_bare_untokenized(monkeypatch):
    # <r9> not present in vault → operand_not_eligible (fail closed).
    entries = {"<r0>": _eligible("<r0>", S50)}
    body = endpoint._CalcRequest(operation="sum", operand_tokens=["<r0>", "<r9>"], consumer="managed_webchat")
    res = _calc(body, _auth(), "s", monkeypatch, entries=entries)
    assert isinstance(res, JSONResponse) and res.status_code == 422


def test_endpoint_denies_chained_calc(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", S50)}
    body = endpoint._CalcRequest(operation="sum", operand_tokens=["<r0>", "<CALC_0>"], consumer="managed_webchat")
    res = _calc(body, _auth(), "s", monkeypatch, entries=entries)
    assert isinstance(res, JSONResponse) and res.status_code == 422


def test_endpoint_division_by_zero(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", "80000"), "<r1>": _eligible("<r1>", "0")}
    body = endpoint._CalcRequest(operation="divide", operand_tokens=["<r0>", "<r1>"], consumer="managed_webchat")
    res = _calc(body, _auth(), "s", monkeypatch, entries=entries)
    assert isinstance(res, JSONResponse) and res.status_code == 422


def test_endpoint_wrong_secret_403(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", S50)}
    body = endpoint._CalcRequest(operation="sum", operand_tokens=["<r0>"], consumer="managed_webchat")
    res = _calc(body, _auth(), "WRONG", monkeypatch, entries=entries)
    assert isinstance(res, JSONResponse) and res.status_code == 403


def test_endpoint_non_managed_consumer_denied(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", S50)}
    body = endpoint._CalcRequest(operation="sum", operand_tokens=["<r0>"], consumer="browser_reload_owner")
    res = _calc(body, _auth(), "s", monkeypatch, entries=entries)
    assert isinstance(res, JSONResponse) and res.status_code == 403


def test_endpoint_calculator_disabled(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", S50)}
    body = endpoint._CalcRequest(operation="sum", operand_tokens=["<r0>"], consumer="managed_webchat")
    res = _calc(body, _auth(), "s", monkeypatch, entries=entries, all_on=False)
    assert isinstance(res, JSONResponse) and res.status_code == 403


def test_endpoint_not_internal_trusted_403(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", S50)}
    body = endpoint._CalcRequest(operation="sum", operand_tokens=["<r0>"], consumer="managed_webchat")
    res = _calc(body, _auth(internal=False), "s", monkeypatch, entries=entries)
    assert isinstance(res, JSONResponse) and res.status_code == 403


# ── Phase 4A.2.7 — tenant-scope gating on the secure-calc endpoint ──────────

def _calc_body():
    return endpoint._CalcRequest(operation="sum", operand_tokens=["<r0>", "<r1>"], consumer="managed_webchat")


def test_endpoint_scope_off_denied(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", "50000"), "<r1>": _eligible("<r1>", "30000")}
    res = _calc(_calc_body(), _auth(), "s", monkeypatch, entries=entries, scope_mode="off")
    assert isinstance(res, JSONResponse) and res.status_code == 403


def test_endpoint_allowlist_match_ok(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", "50000"), "<r1>": _eligible("<r1>", "30000")}
    res = _calc(_calc_body(), _auth(), "s", monkeypatch, entries=entries,
                scope_mode="allowlist", allowlist="tenant-a/proj-a")
    assert res["status"] == "complete" and res["result_token"] == "<CALC_0>"


def test_endpoint_allowlist_mismatch_denied(monkeypatch):
    entries = {"<r0>": _eligible("<r0>", "50000"), "<r1>": _eligible("<r1>", "30000")}
    res = _calc(_calc_body(), _auth(), "s", monkeypatch, entries=entries,
                scope_mode="allowlist", allowlist="other-tenant/other-proj")
    assert isinstance(res, JSONResponse) and res.status_code == 403
