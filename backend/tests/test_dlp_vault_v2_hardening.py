"""Phase 4A.1.1 — Vault V2 allocation/binding/restore hardening tests.

Covers the six audit findings:
  C1  single allocation authority (reserved tokens, no in-request mint)
  C2  metadata bound to exact token/finding/span (no raw-value lookup)
  H1  no legacy vault read/write when V2 enabled
  H2  collision logs carry no raw value prefix
  M1  atomic cap + reservation
  M2  symmetric never-restore signals
"""

from __future__ import annotations

import asyncio
import pathlib

import fakeredis
import pytest
import structlog

from app.domain.dlp import DetectionMatch, EnforcementAction, MatchAction, SensitivityLevel
from app.schemas.common import Message
from app.services.dlp.numeric_context import CONFIDENTIAL_NUMERIC, GENERAL_NUMERIC
from app.services.dlp.transformer import DLPTransformer
from app.services.dlp.vault_v2 import (
    ConversationVaultV2,
    VaultCapExceededError,
    VaultScope,
    build_entries_from_records,
    build_vault_entry,
)


# --------------------------------------------------------------------------- helpers


def _match(text, category, start, end, *, detector="d", entity_type=None, numeric=None):
    meta = {"policy_v2": {"numeric_class": numeric}} if numeric else None
    return DetectionMatch(
        detector_name=detector, entity_type=entity_type, category=category,
        start=start, end=end, matched_text=text,
        severity=SensitivityLevel.MEDIUM, confidence=1.0, metadata=meta,
    )


def _ma(match, action=EnforcementAction.MASK):
    return MatchAction(match=match, action=action, reason_code="t", reason_message="t")


def _vault(max_entries=5000):
    return ConversationVaultV2(
        redis_client=fakeredis.FakeStrictRedis(decode_responses=True),
        max_entries=max_entries,
    )


def _scope(t="t1", p="p1", a="a1", c="c1"):
    return VaultScope(t, p, a, c)


# ===========================================================================
# C2 — metadata binding to exact token/finding/span (record-based)
# ===========================================================================


class TestRecordBinding:
    def _mask_records(self, text, matches):
        tx = DLPTransformer()
        from app.services.dlp.vault_v2 import _ReservedAllocator
        alloc = _ReservedAllocator(tuple(f"<r{i}>" for i in range(len(matches))))
        recs: list = []
        tx.mask_text(text, [_ma(m) for m in matches],
                     token_allocator=alloc, record_sink=recs)
        return recs

    def test_caseA_duplicate_value_not_cross_bound(self):
        # "A 50000 B 50000" — salary(confidential) + quantity(general).
        text = "A 50000 B 50000"
        salary = _match("50000", "financial", 2, 7, numeric=CONFIDENTIAL_NUMERIC)
        qty = _match("50000", "pii", 10, 15, numeric=GENERAL_NUMERIC)
        recs = self._mask_records(text, [salary, qty])
        entries = {e.token: e for e in build_entries_from_records(recs, created_at=1)}
        assert entries["<r0>"].calculation_eligible is True   # salary
        assert entries["<r1>"].calculation_eligible is False  # quantity
        assert entries["<r0>"].numeric_class == CONFIDENTIAL_NUMERIC
        assert entries["<r1>"].numeric_class == GENERAL_NUMERIC

    def test_caseB_identifier_not_mislabeled(self):
        # identifier + salary, same raw value, different spans.
        text = "ID 50000 PAY 50000"
        phone = _match("50000", "pii", 3, 8, detector="phone")
        salary = _match("50000", "financial", 13, 18, numeric=CONFIDENTIAL_NUMERIC)
        recs = self._mask_records(text, [phone, salary])
        e = {r.token: r for r in build_entries_from_records(recs, created_at=1)}
        # <r0> is the phone span → never eligible / never restore.
        assert e["<r0>"].calculation_eligible is False
        assert e["<r0>"].never_restore is True
        # <r1> is the salary span → eligible.
        assert e["<r1>"].calculation_eligible is True

    def test_caseC_two_messages_bound_by_span(self):
        tx = DLPTransformer()
        from app.services.dlp.vault_v2 import _ReservedAllocator
        msgs = [Message(role="user", content="เงินเดือน 50000"),
                Message(role="user", content="งบ 50000")]
        # message_index pins each finding to its own message.
        m0 = _match("50000", "financial", 10, 15, numeric=CONFIDENTIAL_NUMERIC)
        m0.metadata["policy_v2"]["message_index"] = 0
        m1 = _match("50000", "pii", 3, 8, numeric=GENERAL_NUMERIC)
        m1.metadata["policy_v2"]["message_index"] = 1
        recs: list = []
        tx.mask_messages(msgs, [_ma(m0), _ma(m1)],
                         token_allocator=_ReservedAllocator(("<r0>", "<r1>")),
                         record_sink=recs)
        by_mi = {r.message_index: r for r in recs}
        assert by_mi[0].policy_v2.get("numeric_class") == CONFIDENTIAL_NUMERIC
        assert by_mi[1].policy_v2.get("numeric_class") == GENERAL_NUMERIC

    def test_caseE_same_value_twice_distinct_tokens(self):
        recs = self._mask_records(
            "X 50000 Y 50000",
            [_match("50000", "financial", 2, 7, numeric=CONFIDENTIAL_NUMERIC),
             _match("50000", "financial", 10, 15, numeric=CONFIDENTIAL_NUMERIC)],
        )
        assert {r.token for r in recs} == {"<r0>", "<r1>"}  # per-span, distinct
        assert recs[0].start != recs[1].start

    def test_caseF_identifier_value_equals_confidential(self):
        # A credit-card-classed finding never becomes eligible.
        recs = self._mask_records(
            "CC 50000",
            [_match("50000", "financial", 3, 8, detector="credit_card",
                    numeric=CONFIDENTIAL_NUMERIC)],
        )
        e = build_entries_from_records(recs, created_at=1)[0]
        assert e.calculation_eligible is False and e.never_restore is True


# ===========================================================================
# E2E: reserve → transform → records → store  (V2-enabled simulation)
# ===========================================================================


async def _request(vault, scope, tx, text, matches):
    """One V2 'request': reserve, mask via allocator, store from records."""
    n = sum(1 for m in matches if True)
    reserved = await vault.reserve_tokens_async(scope, n)
    recs: list = []
    masked, span_map, _ = tx.mask_text(
        text, [_ma(m) for m in matches],
        token_allocator=reserved.allocator(), record_sink=recs,
    )
    entries = build_entries_from_records(recs, created_at=1)
    await vault.internal_store_reserved_entries_async(scope, reserved, entries)
    return masked


class TestSequentialE2E:
    @pytest.mark.asyncio
    async def test_three_requests_monotonic(self):
        v, s, tx = _vault(), _scope(), DLPTransformer()
        m1 = await _request(v, s, tx, "salary 50000",
                            [_match("50000", "financial", 7, 12, numeric=CONFIDENTIAL_NUMERIC)])
        m2 = await _request(v, s, tx, "bonus 30000",
                            [_match("30000", "financial", 6, 11, numeric=CONFIDENTIAL_NUMERIC)])
        m3 = await _request(v, s, tx, "budget 70000",
                            [_match("70000", "financial", 7, 12, numeric=CONFIDENTIAL_NUMERIC)])
        assert "<r0>" in m1 and "<r1>" in m2 and "<r2>" in m3
        # all three entries persist, none overwritten
        got = await v.get_entries_scoped_async(s, ["<r0>", "<r1>", "<r2>"])
        assert {got["<r0>"].raw_value, got["<r1>"].raw_value, got["<r2>"].raw_value} == {
            "50000", "30000", "70000"}
        # counter is authority; future reserve continues at <r3>
        assert (await v.reserve_tokens_async(s, 1)).tokens == ("<r3>",)


class TestConcurrentE2E:
    @pytest.mark.asyncio
    async def test_fifty_concurrent_same_scope_unique(self):
        v, s, tx = _vault(), _scope(), DLPTransformer()

        async def one(i):
            return await _request(
                v, s, tx, f"salary {10000+i}",
                [_match(str(10000 + i), "financial", 7, 12, numeric=CONFIDENTIAL_NUMERIC)],
            )

        masked = await asyncio.gather(*[one(i) for i in range(50)])
        # 50 distinct tokens emitted
        import re
        toks = [re.search(r"<r\d+>", m).group(0) for m in masked]
        assert len(set(toks)) == 50
        # 50 entries stored, each resolves its own value
        entries = await v.get_entries_scoped_async(s, [f"<r{i}>" for i in range(50)])
        assert len(entries) == 50
        for i in range(50):
            assert entries[f"<r{i}>"].raw_value == str(10000 + i)


class TestSeparateScopes:
    @pytest.mark.asyncio
    async def test_each_scope_has_own_r0(self):
        v, tx = _vault(), DLPTransformer()
        a, b = _scope(a="alice"), _scope(a="bob")
        ma = await _request(v, a, tx, "salary 11111",
                            [_match("11111", "financial", 7, 12, numeric=CONFIDENTIAL_NUMERIC)])
        mb = await _request(v, b, tx, "salary 22222",
                            [_match("22222", "financial", 7, 12, numeric=CONFIDENTIAL_NUMERIC)])
        assert "<r0>" in ma and "<r0>" in mb  # both own <r0>, no clash
        assert (await v.resolve_for_user_async(a, ["<r0>"]))["<r0>"] == "11111"
        assert (await v.resolve_for_user_async(b, ["<r0>"]))["<r0>"] == "22222"


# ===========================================================================
# M1 — atomic cap + reservation
# ===========================================================================


class TestCapAtomicity:
    @pytest.mark.asyncio
    async def test_concurrent_reserve_respects_cap(self):
        v, s = _vault(max_entries=5), _scope()
        results = await asyncio.gather(
            *[v.reserve_tokens_async(s, 1) for _ in range(10)],
            return_exceptions=True,
        )
        ok = [r for r in results if not isinstance(r, Exception)]
        rejected = [r for r in results if isinstance(r, VaultCapExceededError)]
        assert len(ok) == 5 and len(rejected) == 5
        # counter did not advance past the cap on rejection
        fake = v._fake_client
        assert int(fake.hget(s.redis_key(), "__counter__")) == 5

    @pytest.mark.asyncio
    async def test_multi_count_reservations(self):
        v, s = _vault(max_entries=6), _scope()
        a = await v.reserve_tokens_async(s, 2)
        b = await v.reserve_tokens_async(s, 2)
        c = await v.reserve_tokens_async(s, 2)
        assert a.tokens == ("<r0>", "<r1>")
        assert b.tokens == ("<r2>", "<r3>")
        assert c.tokens == ("<r4>", "<r5>")
        with pytest.raises(VaultCapExceededError):
            await v.reserve_tokens_async(s, 1)
        assert int(v._fake_client.hget(s.redis_key(), "__counter__")) == 6


# ===========================================================================
# Reserved-token store contract
# ===========================================================================


class TestReservedStore:
    @pytest.mark.asyncio
    async def test_rejects_unreserved_token(self):
        v, s = _vault(), _scope()
        reserved = await v.reserve_tokens_async(s, 1)  # only <r0>
        good = build_vault_entry(token="<r0>", raw_value="x", category="pii",
                                 action=EnforcementAction.MASK, created_at=1)
        forged = build_vault_entry(token="<r9>", raw_value="y", category="pii",
                                   action=EnforcementAction.MASK, created_at=1)
        n = await v.internal_store_reserved_entries_async(s, reserved, [good, forged])
        assert n == 1  # forged <r9> rejected
        got = await v.get_entries_scoped_async(s, ["<r0>", "<r9>"])
        assert "<r0>" in got and "<r9>" not in got

    @pytest.mark.asyncio
    async def test_mismatched_reservation_rejected(self):
        v, s = _vault(), _scope()
        reserved = await v.reserve_tokens_async(s, 1)
        other = _scope(a="other")
        e = build_vault_entry(token="<r0>", raw_value="x", category="pii",
                              action=EnforcementAction.MASK, created_at=1)
        from app.services.dlp.vault_v2 import VaultScopeError
        with pytest.raises(VaultScopeError):
            await v.internal_store_reserved_entries_async(other, reserved, [e])


# ===========================================================================
# M2 — symmetric never-restore signals
# ===========================================================================


class TestNeverRestoreSymmetric:
    @pytest.mark.parametrize("kw", [
        {"category": "pii", "detector": "phone"},
        {"category": "pii", "entity_type": "phone"},
        {"category": "phone"},
        {"category": "pii", "numeric_class": "STRONG_IDENTIFIER"},
        {"category": "financial", "detector": "credit_card"},
        {"category": "pii", "detector": "secret"},          # secret via detector
        {"category": "pii", "entity_type": "credential"},   # credential via entity
        {"category": "pii", "detector": "credit-card"},     # separator normalized
    ])
    def test_strong_signal_forces_never_restore(self, kw):
        e = build_vault_entry(token="<r0>", raw_value="v",
                              action=EnforcementAction.MASK, created_at=1,
                              **{k: kw.get(k) for k in
                                 ("category", "detector", "entity_type", "numeric_class")
                                 if k in kw})
        assert e.never_restore is True
        assert e.calculation_eligible is False

    def test_unknown_incomplete_conservative(self):
        e = build_vault_entry(token="<r0>", raw_value="v", category="mystery",
                              action=EnforcementAction.MASK, created_at=1)
        assert e.restorable_to_user is False  # not a known-safe category


# ===========================================================================
# H2 — collision logs carry no raw value
# ===========================================================================


class TestLogPrivacy:
    def test_transformer_source_has_no_raw_prefix_log(self):
        src = pathlib.Path("app/services/dlp/transformer.py").read_text(encoding="utf-8")
        assert "existing_value_prefix" not in src
        assert "new_value_prefix" not in src
        assert "[:20]" not in src

    def test_no_raw_value_in_logs_during_v2_mask(self):
        tx = DLPTransformer()
        from app.services.dlp.vault_v2 import _ReservedAllocator
        sentinel = "SENTINEL_SECRET_9182"
        with structlog.testing.capture_logs() as logs:
            recs: list = []
            tx.mask_text(
                f"x {sentinel} y",
                [_ma(_match(sentinel, "financial", 2, 2 + len(sentinel),
                            numeric=CONFIDENTIAL_NUMERIC))],
                token_allocator=_ReservedAllocator(("<r0>",)), record_sink=recs,
            )
        blob = str(logs)
        assert sentinel not in blob
        assert sentinel[:10] not in blob


# ===========================================================================
# H1 — no legacy vault key written when V2 enabled
# ===========================================================================


class TestOrchestratorHelpers:
    """Regression guards for the orchestrator V2 helper methods (the
    shadow path runs by DEFAULT, so a NameError there breaks production)."""

    def test_shadow_helper_no_nameerror(self):
        from app.services.orchestrator import GatewayOrchestrator
        from app.services.dlp.policy_engine import DLPRequestContext
        o = GatewayOrchestrator()
        ctx = DLPRequestContext(tenant_id="t", project_id="p", actor_id="a",
                                conversation_id="c")
        # Must not raise — exercises build_scope + derive + report + logger.
        o._vault_v2_shadow(ctx, {"<r0>": "v"}, [])

    @pytest.mark.asyncio
    async def test_prepare_none_without_scope(self):
        from app.services.orchestrator import GatewayOrchestrator
        from app.services.dlp.policy_engine import DLPRequestContext
        o = GatewayOrchestrator()
        # No actor/conversation → no scope → None, no Redis, no NameError.
        out = await o._vault_v2_prepare(DLPRequestContext(tenant_id="t"), [])
        assert out is None


class TestNoLegacyWrite:
    @pytest.mark.asyncio
    async def test_v2_e2e_writes_only_scoped_key(self):
        v, s, tx = _vault(), _scope(c="conv-xyz"), DLPTransformer()
        await _request(v, s, tx, "salary 50000",
                       [_match("50000", "financial", 7, 12, numeric=CONFIDENTIAL_NUMERIC)])
        fake = v._fake_client
        # the scoped v2 key exists; the legacy key for this conversation does not
        assert fake.exists(s.redis_key()) == 1
        assert fake.exists("dlp_vault:conv-xyz") == 0
