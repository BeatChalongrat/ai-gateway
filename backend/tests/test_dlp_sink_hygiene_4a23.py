"""Phase 4A.2.3 — sink hygiene: output_final, log hygiene, secure trace."""

from __future__ import annotations

import structlog

from app.domain.observability import (
    OUTPUT_FINAL_REDACTED_MARKER,
    safe_output_final_snapshot,
    safe_output_raw_snapshot,
    safe_output_snapshot,
)

SENTINEL = "SECRET-SENTINEL-0812345678"


class _Enf:
    def __init__(self, blocked=False, findings=True):
        self.is_blocked = blocked
        self.match_actions = [object()] if findings else []


# ===========================================================================
# output_final = provider-safe (masked), never restored display
# ===========================================================================


class TestOutputFinalSnapshot:
    def test_masked_passthrough(self):
        masked = "salary <r0> phone <r1>"
        assert safe_output_final_snapshot(masked, _Enf()) == masked

    def test_blocked_placeholder(self):
        assert safe_output_final_snapshot("x", _Enf(blocked=True)) == "[BLOCKED_BY_DLP]"

    def test_missing_with_findings_redacted(self):
        assert safe_output_final_snapshot(None, _Enf(findings=True)) == OUTPUT_FINAL_REDACTED_MARKER
        assert safe_output_final_snapshot("", _Enf(findings=True)) == OUTPUT_FINAL_REDACTED_MARKER

    def test_missing_no_findings_passthrough(self):
        # clean text (no findings) is provider-safe as-is
        assert safe_output_final_snapshot(None, _Enf(findings=False)) is None
        assert safe_output_final_snapshot("hello", _Enf(findings=False)) == "hello"

    def test_never_contains_restored_display(self):
        # The masked source has tokens; the restored display (with SENTINEL)
        # is NOT an input to this function at all → can't leak.
        masked = "salary <r0>"
        out = safe_output_final_snapshot(masked, _Enf())
        assert SENTINEL not in out and out == masked


class TestDeprecatedSnapshotIsSafe:
    def test_returns_masked_not_restored(self):
        masked = "salary <r0>"
        restored = f"salary {SENTINEL}"  # the old "output_final" arg (display)
        out = safe_output_snapshot(masked, restored, _Enf())
        assert out == masked
        assert SENTINEL not in out  # restored display never returned

    def test_blocked(self):
        assert safe_output_snapshot("m", f"r {SENTINEL}", _Enf(blocked=True)) == "[BLOCKED_BY_DLP]"

    def test_output_raw_still_masked(self):
        # output_raw snapshot unchanged — provider-safe masked content.
        assert safe_output_raw_snapshot("ai <r0>", _Enf()) == "ai <r0>"


# ===========================================================================
# Log hygiene F1 — no raw exception message
# ===========================================================================


class TestLogHygiene:
    def test_safe_exc_helper_no_message(self):
        from app.api.routes.public_chat import _safe_exc
        exc = RuntimeError(f"redis failed for {SENTINEL}")
        f = _safe_exc(exc, "vault_store")
        assert f == {"error_code": "vault_store", "exception_type": "RuntimeError"}
        assert SENTINEL not in str(f)

    def test_log_event_excludes_sentinel(self):
        from app.api.routes.public_chat import _safe_exc
        exc = ValueError(f"parse error: {SENTINEL}")
        with structlog.testing.capture_logs() as logs:
            structlog.get_logger("probe").warning(
                "sse_dlp_chunk_failed", dropped_chars=5, **_safe_exc(exc, "sse_dlp_chunk")
            )
        blob = str(logs)
        assert SENTINEL not in blob
        assert "RuntimeError" not in blob  # only the actual type
        assert "sse_dlp_chunk" in blob and "ValueError" in blob

    def test_no_raw_error_kwarg_in_dlp_boundaries(self):
        # Source guard: the hardened raw-adjacent log sites no longer pass a
        # raw exception string.
        import pathlib
        for rel in ("app/services/dlp/vault_v2.py", "app/services/dlp/conversation_vault.py"):
            src = pathlib.Path(rel).read_text(encoding="utf-8")
            assert "error=str(exc)[:200]" not in src


# ===========================================================================
# Secure trace — deliberate in-memory, non-persistent
# ===========================================================================


class TestSecureTraceNonPersistent:
    def test_in_memory_dict_no_db_redis(self):
        from app.services.secure_trace_store import SecureTraceStore
        s = SecureTraceStore(ttl_seconds=900)
        # backed by a plain dict; no db/redis/pool/engine attributes.
        assert isinstance(s._records, dict)
        for attr in ("_db", "db", "_redis", "redis", "_pool", "_engine"):
            assert not hasattr(s, attr), f"secure trace must not hold {attr}"
        assert s._ttl_seconds == 900
