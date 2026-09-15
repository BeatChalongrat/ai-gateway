"""FIX120 — a raw transport failure on the streaming path is mapped like the
non-streaming path maps it (found live: connection refused reached the
stream handler as a bare httpx.ConnectError → unclassified_error → no
fallback, while the same failure non-streaming was provider_unavailable).
"""

from __future__ import annotations

import httpx
import pytest

from app.core.exceptions import ProviderError, ValidationError
from app.services.gateway import fallback_policy as fp
from app.services.providers.error_handler import map_provider_error, normalise_transport_error


def test_connect_error_becomes_provider_unavailable_and_walks():
    exc = normalise_transport_error(httpx.ConnectError("All connection attempts failed"), "openai")
    assert getattr(exc, "code", None) == "provider_unavailable"
    assert fp.decide(error_code=exc.code).action == "cooldown_then_next"
    # the same answer the non-streaming path has always given
    assert type(exc) is type(map_provider_error(httpx.ConnectError("x"), "openai"))


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.PoolTimeout("slow")])
def test_timeouts_become_provider_timeout(exc):
    mapped = normalise_transport_error(exc, "sranai")
    assert getattr(mapped, "code", None) == "provider_timeout"
    assert fp.decide(error_code=mapped.code).walks


def test_gateway_errors_and_unknown_exceptions_pass_through_untouched():
    gw = ProviderError("x", code="provider_5xx")
    assert normalise_transport_error(gw, "openai") is gw
    val = ValidationError("bad")
    assert normalise_transport_error(val, "openai") is val
    other = RuntimeError("parser bug")
    assert normalise_transport_error(other, "openai") is other
    assert getattr(other, "code", None) is None  # still unclassified, on purpose
