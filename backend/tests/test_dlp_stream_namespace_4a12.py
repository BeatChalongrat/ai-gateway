"""Phase 4A.1.2 — streaming token namespace & restore-provenance tests.

Validates B1's fix: under Vault V2 the streaming OUTBOUND mask reserves
from the same scoped counter as inbound (no collision), outbound tokens
carry never-restore provenance, and the per-emit restore can never splice
an inbound value into an outbound-masked position.
"""

from __future__ import annotations

import asyncio
import fakeredis
import pytest

from app.domain.dlp import DetectionMatch, EnforcementAction, MatchAction, SensitivityLevel
from app.services.dlp.numeric_context import CONFIDENTIAL_NUMERIC
from app.services.dlp.transformer import DLPTransformer
from app.services.dlp.vault_v2 import (
    ConversationVaultV2, VaultScope, build_entries_from_records,
    select_stream_restore_candidates,
)


def _m(t, cat, s, e, *, det="d", nc=None):
    meta = {"policy_v2": {"numeric_class": nc}} if nc else None
    return DetectionMatch(detector_name=det, entity_type=None, category=cat,
                          start=s, end=e, matched_text=t,
                          severity=SensitivityLevel.MEDIUM, confidence=1.0, metadata=meta)


def _ma(m, a=EnforcementAction.MASK):
    return MatchAction(match=m, action=a, reason_code="t", reason_message="t")


def _vault(mx=5000):
    return ConversationVaultV2(redis_client=fakeredis.FakeStrictRedis(decode_responses=True),
                              max_entries=mx)


def _scope(a="a1", c="c1"):
    return VaultScope("t1", "p1", a, c)


# --------------------------------------------------------------------------- §4 provenance helper


class TestProvenanceHelper:
    def test_basic_intersection(self):
        out = select_stream_restore_candidates(
            output_tokens={"<r0>", "<r1>", "<r9>"},
            authorized_inbound_tokens=frozenset({"<r0>", "<r1>"}),
            outbound_generated_tokens=frozenset({"<r1>"}),
        )
        assert out == {"<r0>"}  # <r1> excluded (outbound), <r9> not authorized

    def test_outbound_provenance_wins_on_collision(self):
        # Even if an outbound token text collides with an authorized inbound
        # token, provenance excludes it.
        out = select_stream_restore_candidates(
            output_tokens={"<r0>"},
            authorized_inbound_tokens=frozenset({"<r0>"}),
            outbound_generated_tokens=frozenset({"<r0>"}),
        )
        assert out == set()


# --------------------------------------------------------------------------- §8 core regression


async def _inbound(vault, scope, tx, text, match):
    r = await vault.reserve_tokens_async(scope, 1)
    recs: list = []
    masked, span_map, _ = tx.mask_text(text, [_ma(match)],
                                       token_allocator=r.allocator(), record_sink=recs)
    await vault.internal_store_reserved_entries_async(
        scope, r, build_entries_from_records(recs, created_at=1))
    return masked, span_map


async def _outbound(vault, scope, tx, text, match):
    """Mirror the generator's V2 outbound path: reserve, mask, NO store."""
    r = await vault.reserve_tokens_async(scope, 1)
    recs: list = []
    masked, out_map, _ = tx.mask_text(text, [_ma(match)],
                                      token_allocator=r.allocator(), record_sink=recs)
    return masked, set(out_map.keys())


class TestCoreRegression:
    @pytest.mark.asyncio
    async def test_outbound_does_not_collide_and_no_misrestore(self):
        v, s, tx = _vault(), _scope(), DLPTransformer()
        # Inbound salary → <r0> (counter→1), stored restorable.
        _, span_in = await _inbound(v, s, tx, "salary 50000",
                                    _m("50000", "financial", 7, 12, nc=CONFIDENTIAL_NUMERIC))
        assert span_in == {"<r0>": "50000"}
        # Provider outputs a NEW raw phone → outbound mask reserves <r1>.
        out_masked, outbound = await _outbound(
            v, s, tx, "phone 0812345678", _m("0812345678", "pii", 6, 16, det="phone"))
        assert "<r1>" in out_masked and "<r0>" not in out_masked   # distinct index
        assert outbound == {"<r1>"}
        # Per-emit restore over the outbound-masked text with provenance:
        allowed = select_stream_restore_candidates(
            output_tokens=set(span_in) | outbound,
            authorized_inbound_tokens=frozenset(span_in),
            outbound_generated_tokens=frozenset(outbound),
        )
        restored = tx.restore(out_masked, span_in, allowed_tokens=allowed)
        assert "50000" not in restored          # NO mis-restore of the salary
        assert "0812345678" not in restored      # raw phone not leaked
        assert "<r1>" in restored                 # stays an opaque placeholder
        # Counter advanced; outbound raw value NOT persisted.
        assert int(v._fake_client.hget(s.redis_key(), "__counter__")) == 2
        assert await v.get_entries_scoped_async(s, ["<r1>"]) == {}
        assert (await v.resolve_for_user_async(s, ["<r0>"]))["<r0>"] == "50000"
        # future reserve continues past the burned index
        assert (await v.reserve_tokens_async(s, 1)).tokens == ("<r2>",)


# --------------------------------------------------------------------------- §9 echo case


class TestEcho:
    @pytest.mark.asyncio
    async def test_inbound_token_echo_restores(self):
        v, s, tx = _vault(), _scope(), DLPTransformer()
        _, span_in = await _inbound(v, s, tx, "salary 50000",
                                    _m("50000", "financial", 7, 12, nc=CONFIDENTIAL_NUMERIC))
        # Provider echoes the inbound placeholder (NOT outbound-generated).
        output = "your salary is <r0>"
        outbound: set = set()  # nothing minted outbound
        allowed = select_stream_restore_candidates(
            output_tokens={"<r0>"},
            authorized_inbound_tokens=frozenset(span_in),
            outbound_generated_tokens=frozenset(outbound),
        )
        restored = tx.restore(output, span_in, allowed_tokens=allowed)
        assert restored == "your salary is 50000"  # authorized echo restores


# --------------------------------------------------------------------------- §11 sequential


class TestSequentialStream:
    @pytest.mark.asyncio
    async def test_two_streams_continuous_counter(self):
        v, s, tx = _vault(), _scope(), DLPTransformer()
        # Stream 1
        await _inbound(v, s, tx, "salary 50000",
                       _m("50000", "financial", 7, 12, nc=CONFIDENTIAL_NUMERIC))   # <r0>
        _, ob1 = await _outbound(v, s, tx, "phone 0812345678",
                                 _m("0812345678", "pii", 6, 16, det="phone"))      # <r1>
        # Stream 2
        await _inbound(v, s, tx, "bonus 30000",
                       _m("30000", "financial", 6, 11, nc=CONFIDENTIAL_NUMERIC))   # <r2>
        _, ob2 = await _outbound(v, s, tx, "mail a@b.com",
                                 _m("a@b.com", "pii", 5, 12, det="email"))         # <r3>
        assert ob1 == {"<r1>"} and ob2 == {"<r3>"}
        assert int(v._fake_client.hget(s.redis_key(), "__counter__")) == 4
        assert (await v.reserve_tokens_async(s, 1)).tokens == ("<r4>",)
        # inbound entries persist; outbound indices have no entries
        got = await v.get_entries_scoped_async(s, ["<r0>", "<r1>", "<r2>", "<r3>"])
        assert set(got) == {"<r0>", "<r2>"}


# --------------------------------------------------------------------------- §12 concurrent


class TestConcurrentStream:
    @pytest.mark.asyncio
    async def test_50_streams_unique_no_overlap(self):
        v, s, tx = _vault(), _scope(), DLPTransformer()

        async def one(i):
            await _inbound(v, s, tx, f"salary {10000+i}",
                           _m(str(10000 + i), "financial", 7, 12, nc=CONFIDENTIAL_NUMERIC))
            _, ob = await _outbound(v, s, tx, f"phone {20000+i}",
                                    _m(str(20000 + i), "pii", 6, 11, det="phone"))
            return ob

        obs = await asyncio.gather(*[one(i) for i in range(50)])
        all_out = set().union(*obs)
        assert len(all_out) == 50                       # outbound tokens unique
        assert int(v._fake_client.hget(s.redis_key(), "__counter__")) == 100
        # outbound tokens never resolve to a value
        assert await v.resolve_for_user_async(s, list(all_out)) == {}

    @pytest.mark.asyncio
    async def test_separate_scopes_no_cross_resolve(self):
        v, tx = _vault(), DLPTransformer()
        a, b = _scope(a="alice"), _scope(a="bob")
        _, sa = await _inbound(v, a, tx, "salary 11111",
                               _m("11111", "financial", 7, 12, nc=CONFIDENTIAL_NUMERIC))
        _, sb = await _inbound(v, b, tx, "salary 22222",
                               _m("22222", "financial", 7, 12, nc=CONFIDENTIAL_NUMERIC))
        assert sa == {"<r0>": "11111"} and sb == {"<r0>": "22222"}
        assert (await v.resolve_for_user_async(a, ["<r0>"]))["<r0>"] == "11111"
        assert (await v.resolve_for_user_async(b, ["<r0>"]))["<r0>"] == "22222"


# --------------------------------------------------------------------------- §6 fail-closed redaction


class TestReserveFailRedact:
    def test_redact_only_no_token_no_raw(self):
        # The generator's fail-closed branch converts MASK→REDACT and masks
        # with no allocator. Verify that produces [REDACTED], no token, no raw.
        import dataclasses
        tx = DLPTransformer()
        actions = [_ma(_m("0812345678", "pii", 6, 16, det="phone"))]
        red = [dataclasses.replace(ma, action=EnforcementAction.REDACT) for ma in actions]
        masked, span_map, _ = tx.mask_text("phone 0812345678", red)
        assert "[REDACTED]" in masked
        assert "0812345678" not in masked
        assert span_map == {}        # REDACT mints no token
        assert "<r" not in masked    # no local namespace token
