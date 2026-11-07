"""FIX47 Part A — the error frame says the truth about the code it carries.

Before: every ``_emit_error`` frame carried "Model unavailable, please try
again" whatever the code, so a tools request on a tools-blind model
(``validation_error``) told the person to retry a request that could
never succeed. Now the sentence and a ``retryable`` verdict come from
``app.core.error_catalog``; the web client mirrors the table
(``apps/web-chat/src/lib/errorMessages.test.ts`` reads this file).
"""

from __future__ import annotations

import re

import pytest

from app.core import error_catalog, exceptions as exc

RETRY_WORDS = re.compile(r"try again|retry", re.IGNORECASE)


def _gateway_error_codes() -> set[str]:
    """Every ``code`` a GatewayError subclass can carry."""
    out: set[str] = set()
    stack = [exc.GatewayError]
    while stack:
        cls = stack.pop()
        out.add(cls.code)
        stack.extend(cls.__subclasses__())
    return out


def test_every_gateway_exception_code_is_in_the_catalog():
    missing = _gateway_error_codes() - set(error_catalog.CATALOG)
    assert not missing, f"codes without a catalog row: {sorted(missing)}"


def test_stream_only_codes_are_in_the_catalog():
    # emitted by public_chat / the fallback chain, not by an exception class
    for code in ("all_models_failed", "stream_interrupted", "model_no_tool_support",
                 "provider_rate_limited", "provider_model_not_found"):
        assert code in error_catalog.CATALOG, code


def test_no_non_retryable_code_is_told_to_try_again():
    for info in error_catalog.CATALOG.values():
        if not info.retryable:
            assert not RETRY_WORDS.search(info.message), f"{info.code}: {info.message!r}"


def test_every_retryable_code_says_so():
    for info in error_catalog.CATALOG.values():
        if info.retryable:
            assert RETRY_WORDS.search(info.message), f"{info.code}: {info.message!r}"


@pytest.mark.parametrize("code", [
    "validation_error", "model_no_tool_support", "model_no_vision_support",
    "no_capable_model", "context_length_exceeded", "upstream_bad_response",
    "quota_exceeded", "dlp_violation", "guardrail_blocked", "authorization_error",
])
def test_permanent_failures_are_not_retryable(code):
    assert error_catalog.is_retryable(code) is False


@pytest.mark.parametrize("code", [
    "provider_error", "provider_unavailable", "provider_timeout",
    "provider_at_capacity", "rate_limit_exceeded", "all_models_failed",
])
def test_transient_failures_are_retryable(code):
    assert error_catalog.is_retryable(code) is True


def test_unknown_code_is_not_retryable_and_never_promises_a_retry():
    info = error_catalog.describe("something_new")
    assert info.retryable is False
    assert not RETRY_WORDS.search(info.message)
    assert error_catalog.describe(None).retryable is False


def test_tool_capability_error_carries_its_own_code():
    # the adapters raise this for tools on a tools-blind model
    err = exc.ValidationError("x", code="model_no_tool_support", details={"model": "m"})
    assert err.code == "model_no_tool_support"
    assert error_catalog.describe(err.code).retryable is False
    assert "tools" in error_catalog.describe(err.code).message
