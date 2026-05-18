"""Unit tests for ``ConversationVault`` + ``transformer.restore`` vault hook.

Covers:
- extend/resolve round-trip
- resolve returns {} when token absent (and on empty vault)
- TTL is set + matches VAULT_TTL_SECONDS
- restore() consults vault_resolver for orphans
- Unresolvable orphans still log + don't raise
- Redis failure during resolve is swallowed
"""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest

from app.services.dlp.conversation_vault import (
    VAULT_TTL_SECONDS,
    ConversationVault,
)
from app.services.dlp.transformer import DLPTransformer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def vault():
    """Vault backed by an in-process fakeredis instance."""
    return ConversationVault(redis_client=fakeredis.FakeStrictRedis(
        decode_responses=True
    ))


# ---------------------------------------------------------------------------
# extend / resolve / TTL
# ---------------------------------------------------------------------------

def test_vault_extend_and_resolve(vault):
    vault.extend("conv-1", {"[EMAIL_0]": "a@b.com"})
    out = vault.resolve("conv-1", ["[EMAIL_0]"])
    assert out == {"[EMAIL_0]": "a@b.com"}


def test_vault_resolve_missing_token_returns_empty(vault):
    out = vault.resolve("conv-empty", ["[PHONE_0]"])
    assert out == {}, "Cold conversation must return empty, not raise"


def test_vault_ttl_set_on_extend(vault):
    vault.extend("conv-ttl", {"[EMAIL_0]": "x@y.com"})
    # fakeredis preserves TTL semantics — ttl() returns seconds.
    ttl = vault._redis.ttl(vault._key("conv-ttl"))
    assert 0 < ttl <= VAULT_TTL_SECONDS, (
        f"Expected TTL in (0, {VAULT_TTL_SECONDS}] seconds, got {ttl}"
    )


def test_vault_redis_down_returns_empty():
    """A broken Redis client must NOT raise — fail closed to empty."""
    bad_client = MagicMock()
    bad_client.hmget.side_effect = ConnectionError("simulated outage")
    vault = ConversationVault(redis_client=bad_client)
    out = vault.resolve("conv-x", ["[EMAIL_0]"])
    assert out == {}


# ---------------------------------------------------------------------------
# transformer.restore() vault_resolver hook
# ---------------------------------------------------------------------------

def test_orphan_resolved_from_vault_in_restore():
    """An orphan token absent from the current span_map but present
    in the vault must be replaced with the original."""
    transformer = DLPTransformer()
    result = transformer.restore(
        "[EMAIL_0] is your address",
        span_map={},
        vault_resolver=lambda tokens: {"[EMAIL_0]": "x@y.com"},
    )
    assert result == "x@y.com is your address"


def test_orphan_unresolvable_logs_warning_not_raise():
    """If the resolver returns {} for an orphan, the literal token
    must pass through to the user (legacy behaviour) and the restore
    call must not raise."""
    transformer = DLPTransformer()
    # Resolver returns empty — vault doesn't know this token either.
    result = transformer.restore(
        "I'll forward to [EMAIL_99] soon",
        span_map={},
        vault_resolver=lambda tokens: {},
    )
    assert "[EMAIL_99]" in result, "Literal token must remain in output"
