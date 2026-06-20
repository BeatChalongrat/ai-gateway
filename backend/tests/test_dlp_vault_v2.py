"""Phase 4A.1 — Scoped Vault V2 tests.

Covers: scope-key isolation, cross-owner non-resolution, token behavior,
atomic concurrent reservation, calculation-eligibility matrix, authorized
restore + cross-turn bypass closure, TTL/cap/lifecycle, failure
semantics, and behavior purity (no raw values/ids in keys or logs).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import fakeredis
import pytest

from app.domain.dlp import EnforcementAction
from app.services.dlp.numeric_context import (
    CONFIDENTIAL_NUMERIC,
    GENERAL_NUMERIC,
    SECRET_NUMERIC,
    STRONG_IDENTIFIER,
)
from app.services.dlp.policy_engine import DLPRequestContext
from app.services.dlp.vault_v2 import (
    DEFAULT_MAX_ENTRIES,
    SOURCE_MASKED_VALUE,
    VAULT_V2_TTL_SECONDS,
    ConversationVaultV2,
    VaultCapExceededError,
    VaultCounterError,
    VaultEntryV2,
    VaultScope,
    VaultScopeError,
    build_vault_entry,
    build_vault_scope,
    derive_entries_from_span_map,
    vault_v2_shadow_report,
)


class _Match:
    def __init__(self, matched_text, category, detector_name="d",
                 entity_type=None, metadata=None):
        self.matched_text = matched_text
        self.category = category
        self.detector_name = detector_name
        self.entity_type = entity_type
        self.metadata = metadata


class _MA:
    def __init__(self, match, action=EnforcementAction.MASK):
        self.match = match
        self.action = action


def _fake():
    return fakeredis.FakeStrictRedis(decode_responses=True)


def _vault(max_entries=DEFAULT_MAX_ENTRIES):
    return ConversationVaultV2(redis_client=_fake(), max_entries=max_entries)


def _scope(t="t1", p="p1", a="a1", c="c1"):
    return VaultScope(tenant_id=t, project_id=p, actor_id=a, conversation_id=c)


def _entry(token="<r0>", raw="50000", category="financial",
           action=EnforcementAction.MASK, numeric_class=None, **kw):
    return build_vault_entry(
        token=token, raw_value=raw, category=category, action=action,
        created_at=1_700_000_000, numeric_class=numeric_class, **kw
    )


# ===========================================================================
# Scope construction + key isolation
# ===========================================================================


class TestScopeKey:
    def test_from_context_full(self):
        ctx = DLPRequestContext(tenant_id="t", project_id="p", actor_id="a",
                                conversation_id="c")
        s = build_vault_scope(ctx)
        assert s == VaultScope("t", "p", "a", "c") and s.is_valid

    @pytest.mark.parametrize("missing", ["tenant_id", "project_id", "actor_id",
                                         "conversation_id"])
    def test_from_context_missing_field_invalid(self, missing):
        fields = dict(tenant_id="t", project_id="p", actor_id="a",
                      conversation_id="c")
        fields[missing] = None
        assert build_vault_scope(DLPRequestContext(**fields)) is None

    def test_blank_and_whitespace_rejected(self):
        assert build_vault_scope(
            DLPRequestContext(tenant_id="t", project_id="p", actor_id="   ",
                              conversation_id="c")
        ) is None
        assert VaultScope("t", "p", "", "c").is_valid is False

    def test_same_scope_same_key(self):
        assert _scope().redis_key() == _scope().redis_key()

    def test_different_actor_different_key(self):
        assert _scope(a="a1").redis_key() != _scope(a="a2").redis_key()

    def test_different_tenant_different_key(self):
        assert _scope(t="t1").redis_key() != _scope(t="t2").redis_key()

    def test_different_project_different_key(self):
        assert _scope(p="p1").redis_key() != _scope(p="p2").redis_key()

    def test_different_conversation_different_key(self):
        assert _scope(c="c1").redis_key() != _scope(c="c2").redis_key()

    def test_channel_streaming_model_direction_not_in_key(self):
        # Same owner scope must collapse to one key regardless of these.
        ctx1 = DLPRequestContext(tenant_id="t", project_id="p", actor_id="a",
                                 conversation_id="c", streaming=False,
                                 model="gpt", channel="api")
        ctx2 = DLPRequestContext(tenant_id="t", project_id="p", actor_id="a",
                                 conversation_id="c", streaming=True,
                                 model="claude", channel="webchat")
        assert build_vault_scope(ctx1).redis_key() == build_vault_scope(ctx2).redis_key()

    def test_raw_ids_not_in_key(self):
        s = VaultScope("tenant-SECRET", "proj-SECRET", "actor-SECRET",
                       "conv-SECRET")
        key = s.redis_key()
        assert key.startswith("dlp_vault:v2:")
        for raw in ("tenant-SECRET", "proj-SECRET", "actor-SECRET", "conv-SECRET"):
            assert raw not in key
        assert s.fingerprint() in key  # short fp is a prefix of the digest

    def test_delimiter_unambiguous(self):
        # Splitting fields differently must not collide.
        assert _scope(t="a", p="b").redis_key() != _scope(t="a:b", p="").redis_key()


# ===========================================================================
# Entry schema + eligibility matrix
# ===========================================================================


class TestEntryEligibility:
    def test_confidential_numeric_mask_is_eligible(self):
        e = _entry(category="financial", action=EnforcementAction.MASK,
                   numeric_class=CONFIDENTIAL_NUMERIC)
        assert e.calculation_eligible is True

    def test_budget_confidential_eligible(self):
        e = _entry(raw="5000000", category="financial",
                   action=EnforcementAction.MASK, numeric_class=CONFIDENTIAL_NUMERIC)
        assert e.calculation_eligible is True

    @pytest.mark.parametrize("category", ["phone", "thai_national_id",
                                          "credit_card", "bank_account",
                                          "promptpay", "otp", "pin", "password"])
    def test_identifier_categories_never_eligible(self, category):
        e = _entry(category=category, action=EnforcementAction.MASK,
                   numeric_class=CONFIDENTIAL_NUMERIC)
        assert e.calculation_eligible is False
        assert e.never_restore is True

    def test_secret_numeric_never_eligible(self):
        e = _entry(category="financial", action=EnforcementAction.MASK,
                   numeric_class=SECRET_NUMERIC)
        assert e.calculation_eligible is False

    def test_strong_identifier_never_eligible(self):
        e = _entry(category="pii", action=EnforcementAction.MASK,
                   numeric_class=STRONG_IDENTIFIER)
        assert e.calculation_eligible is False
        assert e.never_restore is True

    def test_general_number_not_eligible(self):
        e = _entry(category="pii", action=EnforcementAction.MASK,
                   numeric_class=GENERAL_NUMERIC)
        assert e.calculation_eligible is False

    def test_no_numeric_metadata_defaults_false(self):
        e = _entry(category="financial", action=EnforcementAction.MASK,
                   numeric_class=None)
        assert e.calculation_eligible is False  # legacy decision → not eligible

    def test_unknown_category_not_restorable(self):
        e = _entry(category="mystery", action=EnforcementAction.MASK)
        assert e.restorable_to_user is False

    def test_unknown_action_rejected(self):
        assert _entry(action="WAT") is None

    def test_eligible_requires_mask_action(self):
        e = _entry(category="financial", action=EnforcementAction.REDACT,
                   numeric_class=CONFIDENTIAL_NUMERIC)
        assert e.calculation_eligible is False

    def test_secret_category_never_restore(self):
        e = _entry(category="secret", action=EnforcementAction.MASK)
        assert e.never_restore is True and e.restorable_to_user is False

    def test_blank_token_rejected(self):
        assert _entry(token="") is None
        assert _entry(token="not-a-token") is None

    def test_non_masked_source_rejected(self):
        assert _entry(source_kind="derived_result") is None

    def test_roundtrip_json(self):
        e = _entry(category="pii", action=EnforcementAction.MASK)
        back = VaultEntryV2.from_json(e.to_json())
        assert back == e

    def test_from_json_malformed(self):
        assert VaultEntryV2.from_json("not json") is None
        assert VaultEntryV2.from_json('{"token":"<r0>"}') is None  # missing fields
        assert VaultEntryV2.from_json(None) is None


# ===========================================================================
# Reservation (atomic + bounded)
# ===========================================================================


class TestReservation:
    @pytest.mark.asyncio
    async def test_sequential_reservation_monotonic(self):
        v = _vault()
        s = _scope()
        assert (await v.reserve_tokens_async(s, 3)).tokens == ("<r0>", "<r1>", "<r2>")
        assert (await v.reserve_tokens_async(s, 2)).tokens == ("<r3>", "<r4>")

    @pytest.mark.asyncio
    async def test_concurrent_reservation_unique(self):
        v = _vault()
        s = _scope()
        results = await asyncio.gather(*[v.reserve_tokens_async(s, 1)
                                         for _ in range(50)])
        flat = [t for r in results for t in r.tokens]
        assert len(flat) == 50 and len(set(flat)) == 50  # no duplicates

    @pytest.mark.asyncio
    async def test_count_must_be_positive(self):
        v = _vault()
        with pytest.raises(ValueError):
            await v.reserve_tokens_async(_scope(), 0)
        with pytest.raises(ValueError):
            await v.reserve_tokens_async(_scope(), -1)

    @pytest.mark.asyncio
    async def test_invalid_scope_no_redis_op(self):
        client = MagicMock()
        v = ConversationVaultV2(redis_client=client)
        with pytest.raises(VaultScopeError):
            await v.reserve_tokens_async(VaultScope("t", "p", "", "c"), 1)
        client.hincrby.assert_not_called()
        client.hget.assert_not_called()

    @pytest.mark.asyncio
    async def test_cap_exceeded_no_reuse(self):
        v = _vault(max_entries=3)
        s = _scope()
        assert (await v.reserve_tokens_async(s, 3)).tokens == ("<r0>", "<r1>", "<r2>")
        with pytest.raises(VaultCapExceededError):
            await v.reserve_tokens_async(s, 1)

    @pytest.mark.asyncio
    async def test_malformed_counter_fails_closed(self):
        fake = _fake()
        s = _scope()
        fake.hset(s.redis_key(), "__counter__", "not-an-int")
        v = ConversationVaultV2(redis_client=fake)
        with pytest.raises(VaultCounterError):
            await v.reserve_tokens_async(s, 1)

    @pytest.mark.asyncio
    async def test_reservation_sets_ttl(self):
        fake = _fake()
        v = ConversationVaultV2(redis_client=fake)
        s = _scope()
        await v.reserve_tokens_async(s, 1)
        ttl = fake.ttl(s.redis_key())
        assert 0 < ttl <= VAULT_V2_TTL_SECONDS


# ===========================================================================
# Store / read / authorized restore
# ===========================================================================


class TestStoreAndResolve:
    @pytest.mark.asyncio
    async def test_extend_and_resolve_restorable(self):
        v = _vault()
        s = _scope()
        await v.extend_scoped_async(s, [_entry(token="<r0>", raw="alice@x.com",
                                               category="pii",
                                               action=EnforcementAction.MASK)])
        assert await v.resolve_for_user_async(s, ["<r0>"]) == {"<r0>": "alice@x.com"}

    @pytest.mark.asyncio
    async def test_secret_entry_not_restored_in_scope(self):
        v = _vault()
        s = _scope()
        await v.extend_scoped_async(s, [_entry(token="<r0>", raw="sk-xxx",
                                               category="secret",
                                               action=EnforcementAction.MASK)])
        # present as an entry, but resolve refuses it
        assert await v.resolve_for_user_async(s, ["<r0>"]) == {}
        entries = await v.get_entries_scoped_async(s, ["<r0>"])
        assert entries["<r0>"].never_restore is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", ["phone", "thai_national_id",
                                          "credit_card", "otp"])
    async def test_identifier_not_restored_in_scope(self, category):
        v = _vault()
        s = _scope()
        await v.extend_scoped_async(s, [_entry(token="<r0>", raw="123",
                                               category=category,
                                               action=EnforcementAction.MASK)])
        assert await v.resolve_for_user_async(s, ["<r0>"]) == {}

    @pytest.mark.asyncio
    async def test_forged_token_unresolved(self):
        v = _vault()
        s = _scope()
        await v.extend_scoped_async(s, [_entry(token="<r0>", raw="x@y.com",
                                               category="pii")])
        assert await v.resolve_for_user_async(s, ["<r999>"]) == {}

    @pytest.mark.asyncio
    async def test_malformed_token_unresolved(self):
        v = _vault()
        s = _scope()
        assert await v.resolve_for_user_async(s, ["DROP TABLE", "<r0"]) == {}

    @pytest.mark.asyncio
    async def test_malformed_stored_entry_unresolved(self):
        fake = _fake()
        s = _scope()
        fake.hset(s.redis_key(), "<r0>", "{not valid json")
        v = ConversationVaultV2(redis_client=fake)
        assert await v.resolve_for_user_async(s, ["<r0>"]) == {}

    @pytest.mark.asyncio
    async def test_token_field_mismatch_rejected(self):
        # An entry stored under <r0> but whose envelope claims <r9> is
        # treated as unresolved (tamper / corruption guard).
        fake = _fake()
        s = _scope()
        forged = _entry(token="<r9>", raw="x@y.com", category="pii")
        fake.hset(s.redis_key(), "<r0>", forged.to_json())
        v = ConversationVaultV2(redis_client=fake)
        assert await v.get_entries_scoped_async(s, ["<r0>"]) == {}

    @pytest.mark.asyncio
    async def test_malformed_entry_skipped_on_write(self):
        v = _vault()
        s = _scope()
        n = await v.extend_scoped_async(s, [None, _entry(token="<r0>",
                                                         category="pii")])
        assert n == 1


# ===========================================================================
# derive_entries_from_span_map — correlation to real findings
# ===========================================================================


class TestDeriveEntries:
    def test_correlates_category_and_numeric_class(self):
        span_map = {"<r0>": "50000", "<r1>": "alice@x.com"}
        mas = [
            _MA(_Match("50000", "financial",
                       metadata={"policy_v2": {"numeric_class": CONFIDENTIAL_NUMERIC,
                                               "message_index": 0}})),
            _MA(_Match("alice@x.com", "pii", entity_type="email_address")),
        ]
        entries = derive_entries_from_span_map(span_map, mas, created_at=1)
        by_token = {e.token: e for e in entries}
        assert by_token["<r0>"].calculation_eligible is True
        assert by_token["<r0>"].numeric_class == CONFIDENTIAL_NUMERIC
        assert by_token["<r1>"].calculation_eligible is False
        assert by_token["<r1>"].restorable_to_user is True

    def test_uncorrelated_token_conservative(self):
        # A token whose value has no matching finding → unknown category,
        # non-restorable, ineligible.
        entries = derive_entries_from_span_map({"<r0>": "orphan"}, [], created_at=1)
        assert entries[0].category == "unknown"
        assert entries[0].restorable_to_user is False
        assert entries[0].calculation_eligible is False

    def test_metadata_not_from_body(self):
        # Even if a finding lacks policy_v2 metadata, eligibility stays
        # False (no guessing from detector name / digits).
        mas = [_MA(_Match("99999", "financial", detector_name="numeric"))]
        entries = derive_entries_from_span_map({"<r0>": "99999"}, mas, created_at=1)
        assert entries[0].calculation_eligible is False


# ===========================================================================
# Cross-owner isolation
# ===========================================================================


class TestCrossOwner:
    @pytest.mark.asyncio
    async def test_same_conversation_different_actor_isolated(self):
        v = _vault()  # one shared Redis backend
        a = _scope(a="alice", c="shared-conv")
        b = _scope(a="bob", c="shared-conv")
        await v.extend_scoped_async(a, [_entry(token="<r0>", raw="alice@x.com",
                                               category="pii")])
        # B cannot resolve, cannot see existence, cannot overwrite A.
        assert await v.resolve_for_user_async(b, ["<r0>"]) == {}
        assert await v.get_entries_scoped_async(b, ["<r0>"]) == {}
        await v.extend_scoped_async(b, [_entry(token="<r0>", raw="bob@x.com",
                                               category="pii")])
        assert (await v.resolve_for_user_async(a, ["<r0>"]))["<r0>"] == "alice@x.com"
        assert (await v.resolve_for_user_async(b, ["<r0>"]))["<r0>"] == "bob@x.com"

    @pytest.mark.asyncio
    async def test_cross_tenant_isolated(self):
        v = _vault()
        a = _scope(t="t1", c="c")
        b = _scope(t="t2", c="c")
        await v.extend_scoped_async(a, [_entry(token="<r0>", raw="v1", category="pii")])
        assert await v.resolve_for_user_async(b, ["<r0>"]) == {}

    @pytest.mark.asyncio
    async def test_api_key_actor_vs_portal_actor_isolated(self):
        v = _vault()
        keyed = _scope(a="gw_key_123", c="c")
        portal = _scope(a="portal_user_9", c="c")
        await v.extend_scoped_async(keyed, [_entry(token="<r0>", raw="v",
                                                   category="pii")])
        assert await v.resolve_for_user_async(portal, ["<r0>"]) == {}

    @pytest.mark.asyncio
    async def test_same_token_distinct_values_no_collision(self):
        v = _vault()
        a, b = _scope(a="a"), _scope(a="b")
        await v.extend_scoped_async(a, [_entry(token="<r0>", raw="A", category="pii")])
        await v.extend_scoped_async(b, [_entry(token="<r0>", raw="B", category="pii")])
        assert (await v.resolve_for_user_async(a, ["<r0>"]))["<r0>"] == "A"
        assert (await v.resolve_for_user_async(b, ["<r0>"]))["<r0>"] == "B"


# ===========================================================================
# TTL / lifecycle / no-legacy
# ===========================================================================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_write_sets_ttl_read_does_not_refresh(self):
        fake = _fake()
        v = ConversationVaultV2(redis_client=fake)
        s = _scope()
        await v.extend_scoped_async(s, [_entry(token="<r0>", category="pii")])
        fake.expire(s.redis_key(), 100)  # simulate aging
        await v.resolve_for_user_async(s, ["<r0>"])  # read must NOT refresh
        assert fake.ttl(s.redis_key()) <= 100

    @pytest.mark.asyncio
    async def test_write_refreshes_ttl(self):
        fake = _fake()
        v = ConversationVaultV2(redis_client=fake)
        s = _scope()
        await v.extend_scoped_async(s, [_entry(token="<r0>", category="pii")])
        fake.expire(s.redis_key(), 100)
        await v.extend_scoped_async(s, [_entry(token="<r1>", category="pii")])
        assert fake.ttl(s.redis_key()) > 100

    @pytest.mark.asyncio
    async def test_delete_scope_only_that_key(self):
        fake = _fake()
        v = ConversationVaultV2(redis_client=fake)
        a, b = _scope(a="a"), _scope(a="b")
        await v.extend_scoped_async(a, [_entry(token="<r0>", category="pii")])
        await v.extend_scoped_async(b, [_entry(token="<r0>", category="pii")])
        await v.delete_scope_async(a)
        assert fake.exists(a.redis_key()) == 0
        assert fake.exists(b.redis_key()) == 1

    @pytest.mark.asyncio
    async def test_delete_invalid_scope_no_op(self):
        client = MagicMock()
        v = ConversationVaultV2(redis_client=client)
        await v.delete_scope_async(VaultScope("t", "p", "", "c"))
        client.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_v2_ignores_legacy_key(self):
        # A legacy ``dlp_vault:{conversation_id}`` hash with <r0> must be
        # invisible to V2 (no dual-read).
        fake = _fake()
        fake.hset("dlp_vault:c1", "<r0>", "legacy-secret")
        v = ConversationVaultV2(redis_client=fake)
        s = _scope(c="c1")
        assert await v.resolve_for_user_async(s, ["<r0>"]) == {}


# ===========================================================================
# Failure semantics
# ===========================================================================


class TestFailures:
    @pytest.mark.asyncio
    async def test_redis_down_on_store_masks_no_raw(self):
        client = MagicMock()
        client.hset.side_effect = ConnectionError("down")
        v = ConversationVaultV2(redis_client=client)
        n = await v.extend_scoped_async(_scope(), [_entry(token="<r0>",
                                                          category="pii")])
        assert n == 0  # nothing stored; caller keeps masked placeholder

    @pytest.mark.asyncio
    async def test_redis_down_on_resolve_opaque(self):
        client = MagicMock()
        client.hmget.side_effect = ConnectionError("down")
        v = ConversationVaultV2(redis_client=client)
        assert await v.resolve_for_user_async(_scope(), ["<r0>"]) == {}

    @pytest.mark.asyncio
    async def test_get_invalid_scope_empty(self):
        v = _vault()
        assert await v.get_entries_scoped_async(None, ["<r0>"]) == {}
        assert await v.resolve_for_user_async(None, ["<r0>"]) == {}


# ===========================================================================
# Shadow report purity
# ===========================================================================


class TestShadowReport:
    def test_counts(self):
        s = _scope()
        entries = [
            _entry(token="<r0>", category="financial",
                   action=EnforcementAction.MASK, numeric_class=CONFIDENTIAL_NUMERIC),
            _entry(token="<r1>", category="secret", action=EnforcementAction.MASK),
            None,
        ]
        rep = vault_v2_shadow_report(s, entries)
        assert rep["scope_valid"] == 1 and rep["scope_missing"] == 0
        assert rep["entries_valid"] == 2
        assert rep["entries_ineligible"] == 1  # secret not eligible
        assert rep["restore_candidates"] == 1  # only the financial one

    def test_missing_scope_counted(self):
        rep = vault_v2_shadow_report(None, [])
        assert rep["scope_missing"] == 1 and rep["scope_valid"] == 0


# ===========================================================================
# Integration wiring guards (no Redis when scope is absent)
# ===========================================================================


class TestIntegrationGuards:
    def test_stream_resolver_none_without_scope(self):
        from app.api.routes.public_chat import _build_v2_stream_resolver
        # No conversation_id → no scope → no resolver (no Redis construction).
        assert _build_v2_stream_resolver(DLPRequestContext(
            tenant_id="t", project_id="p", actor_id="a")) is None

    @pytest.mark.asyncio
    async def test_stream_prepare_none_without_scope(self):
        from app.api.routes.public_chat import _vault_v2_prepare_stream
        # Missing actor → invalid scope → no reservation (None), no Redis.
        out = await _vault_v2_prepare_stream(
            DLPRequestContext(tenant_id="t", project_id="p", conversation_id="c"),
            [],
        )
        assert out is None

    def test_stream_shadow_noop_without_scope(self):
        from app.api.routes.public_chat import _vault_v2_shadow_stream
        # Pure: builds report, logs counters, no Redis, no raise.
        _vault_v2_shadow_stream(DLPRequestContext(), {"<r0>": "x"}, [])


# ===========================================================================
# Log/key secrecy
# ===========================================================================


class TestSecrecy:
    def test_entry_repr_excluded_from_logs_helper(self):
        # The shadow report exposes only counts — never raw values.
        s = _scope()
        rep = vault_v2_shadow_report(s, [_entry(token="<r0>", raw="TOPSECRET",
                                                category="pii")])
        assert "TOPSECRET" not in str(rep)

    def test_fingerprint_is_not_raw(self):
        s = VaultScope("tenant-X", "p", "a", "c")
        assert "tenant-X" not in s.fingerprint()
        assert len(s.fingerprint()) == 12
