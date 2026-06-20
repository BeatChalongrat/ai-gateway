"""Phase 4A.2.7 — tenant-scoped feature gate logic."""

from __future__ import annotations

import app.services.dlp.feature_scope as fs


def _set(monkeypatch, mode, allowlist=""):
    monkeypatch.setattr(fs.settings, "dlp_feature_scope_mode", mode, raising=False)
    monkeypatch.setattr(fs.settings, "dlp_feature_allowlist", allowlist, raising=False)
    fs._parse_allowlist.cache_clear()


def test_off_disables_everything(monkeypatch):
    _set(monkeypatch, "off", "acme/cs")
    assert fs.is_in_scope("acme", "cs") is False
    assert fs.effective_feature_enabled(True, "acme", "cs") is False


def test_allowlist_match_and_mismatch(monkeypatch):
    _set(monkeypatch, "allowlist", "acme-corp-001/acme-customer-support, x/y")
    assert fs.is_in_scope("acme-corp-001", "acme-customer-support") is True
    assert fs.effective_feature_enabled(True, "acme-corp-001", "acme-customer-support") is True
    # mismatch
    assert fs.is_in_scope("demo-tenant-000", "default") is False
    # global flag off → still off even if allowlisted
    assert fs.effective_feature_enabled(False, "acme-corp-001", "acme-customer-support") is False


def test_missing_tenant_or_project_disabled(monkeypatch):
    _set(monkeypatch, "global")
    assert fs.is_in_scope(None, "p") is False
    assert fs.is_in_scope("t", None) is False
    assert fs.is_in_scope("", "p") is False
    assert fs.is_in_scope("t", "  ") is False


def test_global_enables_any_valid_tenant(monkeypatch):
    _set(monkeypatch, "global")
    assert fs.is_in_scope("any", "proj") is True
    assert fs.effective_feature_enabled(True, "any", "proj") is True


def test_malformed_mode_fail_closed(monkeypatch):
    _set(monkeypatch, "weird-mode", "acme/cs")
    assert fs.scope_mode() == "off"
    assert fs.is_in_scope("acme", "cs") is False


def test_malformed_allowlist_entries_skipped(monkeypatch):
    # entries with no '/' or blank halves are dropped (never wildcard)
    _set(monkeypatch, "allowlist", "  , no-slash, /noproj, notenant/, good/proj , a/b/c")
    assert fs.is_in_scope("good", "proj") is True
    assert fs.is_in_scope("no-slash", "") is False
    assert fs.is_in_scope("notenant", "") is False
    assert fs.is_in_scope("a", "b/c") is False  # 'a/b/c' has 2 slashes → skipped
