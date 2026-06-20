"""Phase 4A.2.5 (B2) — internal Vault V2 restore endpoint.

Exercises the authorization gates of ``POST /internal/dlp/vault-v2/restore``
directly (no HTTP): internal-trusted, owner-consumer-only, vault-enabled-only,
never_restore denial, not-found, and that a never-restore secret value never
appears in the response. Scope derivation is server-side from the auth context.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.responses import JSONResponse

from app.domain.dlp import EnforcementAction
from app.services.dlp.numeric_context import CONFIDENTIAL_NUMERIC
from app.services.dlp.vault_v2 import build_vault_entry
import app.api.routes.internal_dlp as endpoint
from app.api.routes.internal_dlp import _RestoreRequest, vault_v2_restore

SECRET = "SECRET-CALC-SENTINEL-50000"


def _auth(internal=True):
    return SimpleNamespace(
        tenant_id="tenant-a",
        project_id="proj-a",
        key_id="key-a",
        portal_user_id="actor-a",
        internal_trusted=internal,
        trusted_conversation_id="conv-a",
    )


class _FakeVault:
    def __init__(self, entries):
        self._e = entries

    async def get_entries_scoped_async(self, scope, tokens):
        return {t: self._e[t] for t in tokens if t in self._e}


def _entries():
    # Confidential numeric salary → restorable to owner.
    salary = build_vault_entry(
        token="<r0>", raw_value="50000", category="financial",
        action=EnforcementAction.MASK, created_at=1,
        detector="numeric_confidential", numeric_class=CONFIDENTIAL_NUMERIC,
    )
    # Secret (OTP-like) → never_restore; raw carries the sentinel.
    secret = build_vault_entry(
        token="<r1>", raw_value=SECRET, category="secret",
        action=EnforcementAction.MASK, created_at=1, detector="numeric_secret",
    )
    assert salary is not None and secret is not None
    assert salary.restorable_to_user and not salary.never_restore
    assert salary.calculation_eligible  # B3 metadata correctness
    assert secret.never_restore
    return {"<r0>": salary, "<r1>": secret}


def _run(body, auth, secret_hdr, monkeypatch, *, vault_enabled=True, configured="s"):
    monkeypatch.setattr(endpoint.settings, "internal_service_secret", configured, raising=False)
    monkeypatch.setattr(endpoint.settings, "dlp_vault_v2_enabled", vault_enabled, raising=False)
    monkeypatch.setattr(endpoint, "conversation_vault_v2", _FakeVault(_entries()))
    return asyncio.run(vault_v2_restore(body=body, auth=auth, x_internal_service_secret=secret_hdr))


def test_owner_restores_confidential_denies_secret_and_missing(monkeypatch):
    body = _RestoreRequest(tokens=["<r0>", "<r1>", "<r2>"], consumer="browser_reload_owner")
    res = _run(body, _auth(), "s", monkeypatch)
    assert res["tokens"]["<r0>"]["restored"] == "50000"
    assert res["denied"]["<r1>"] == "never_restore"
    assert res["denied"]["<r2>"] == "not_found"
    # The secret sentinel must NEVER appear in any restored value.
    assert SECRET not in str(res["tokens"])


def test_wrong_internal_secret_forbidden(monkeypatch):
    body = _RestoreRequest(tokens=["<r0>"], consumer="browser_reload_owner")
    res = _run(body, _auth(), "WRONG", monkeypatch)
    assert isinstance(res, JSONResponse) and res.status_code == 403


def test_not_internal_trusted_forbidden(monkeypatch):
    body = _RestoreRequest(tokens=["<r0>"], consumer="browser_reload_owner")
    res = _run(body, _auth(internal=False), "s", monkeypatch)
    assert isinstance(res, JSONResponse) and res.status_code == 403


def test_non_owner_consumer_denied_no_raw(monkeypatch):
    body = _RestoreRequest(tokens=["<r0>"], consumer="admin_default")
    res = _run(body, _auth(), "s", monkeypatch)
    assert res["tokens"] == {}
    assert res["denied"]["<r0>"] == "consumer_not_allowed"
    assert "50000" not in str(res)


def test_vault_disabled_no_restore(monkeypatch):
    body = _RestoreRequest(tokens=["<r0>"], consumer="browser_reload_owner")
    res = _run(body, _auth(), "s", monkeypatch, vault_enabled=False)
    assert res["tokens"] == {}
    assert res["denied"]["<r0>"] == "vault_disabled"
    assert "50000" not in str(res)
