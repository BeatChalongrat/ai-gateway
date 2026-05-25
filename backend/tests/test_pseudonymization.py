"""Pseudonymization layer tests (``DLP_PSEUDONYM_MODE=true``).

Each test enables the feature flag via monkeypatch so the default
``<rN>`` legacy behaviour is unaffected for the rest of the suite.
"""

from __future__ import annotations

import pytest

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
)
from app.services.dlp.transformer import (
    ANY_MASK_TOKEN_RE,
    DLPTransformer,
)


@pytest.fixture
def pseudonym_on(monkeypatch):
    """Enable ``DLP_PSEUDONYM_MODE`` for the duration of the test."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_pseudonym_mode", True, raising=False)
    yield


def _match(
    detector_name: str,
    category: str,
    start: int,
    end: int,
    text_slice: str,
    severity: SensitivityLevel = SensitivityLevel.MEDIUM,
) -> MatchAction:
    return MatchAction(
        match=DetectionMatch(
            detector_name=detector_name,
            entity_type=detector_name,
            category=category,
            start=start,
            end=end,
            matched_text=text_slice,
            severity=severity,
        ),
        action=EnforcementAction.MASK,
        reason_code="test",
        reason_message=f"test {detector_name}",
    )


# ---------------------------------------------------------------------------
# Test 1: identity-class detectors get readable pseudonym tokens
# ---------------------------------------------------------------------------


def test_identity_class_gets_pseudonym(pseudonym_on):
    """Identity-class findings produce ``<<CATEGORY_N>>`` in masked output.

    The flag-on path must NOT use the legacy ``<rN>`` shape for
    detectors listed in ``PSEUDONYM_MAP``. The spec says PERSON uses
    a letter suffix (A first), others use 1-indexed numerics.
    """
    text = "สมชาย email somchai@gmail.com โทร 081-234-5678"
    name_start = text.index("สมชาย")
    email_start = text.index("somchai@gmail.com")
    phone_start = text.index("081-234-5678")
    actions = [
        _match(
            "presidio_ner",
            "pii",
            name_start,
            name_start + len("สมชาย"),
            "สมชาย",
        ),
        _match(
            "email",
            "pii",
            email_start,
            email_start + len("somchai@gmail.com"),
            "somchai@gmail.com",
        ),
        _match(
            "phone",
            "pii",
            phone_start,
            phone_start + len("081-234-5678"),
            "081-234-5678",
        ),
    ]
    masked, span_map, _ = DLPTransformer().mask_text(text, actions)

    assert "<<PERSON_A>>" in masked
    assert "<<EMAIL_1>>" in masked
    assert "<<PHONE_1>>" in masked
    # No legacy tokens minted under pseudonym mode for identity-class.
    assert "<r0>" not in masked
    assert "<r1>" not in masked
    assert "<r2>" not in masked
    assert span_map["<<PERSON_A>>"] == "สมชาย"
    assert span_map["<<EMAIL_1>>"] == "somchai@gmail.com"
    assert span_map["<<PHONE_1>>"] == "081-234-5678"


# ---------------------------------------------------------------------------
# Test 2: secret-class detectors keep the legacy <rN> shape
# ---------------------------------------------------------------------------


def test_secret_class_keeps_rN(pseudonym_on):
    """Secret-class findings stay on ``<rN>`` shape AND are never restored."""
    text = "my api key is sk-ant-api03-abc123"
    key_start = text.index("sk-ant-api03-abc123")
    actions = [
        _match(
            "api_key_secret",
            "credential",
            key_start,
            key_start + len("sk-ant-api03-abc123"),
            "sk-ant-api03-abc123",
            severity=SensitivityLevel.CRITICAL,
        ),
    ]
    masked, span_map, _ = DLPTransformer().mask_text(text, actions)

    assert "<r0>" in masked
    assert "<<" not in masked  # no pseudonym shape
    assert span_map == {"<r0>": "sk-ant-api03-abc123"}

    # Restore with empty allowed_tokens (secret category never RESTOREs) —
    # the secret remains masked.
    restored = DLPTransformer().restore(
        masked, span_map, allowed_tokens=set()
    )
    assert "sk-ant-api03-abc123" not in restored
    assert "<r0>" in restored


# ---------------------------------------------------------------------------
# Test 3: income detector is bypassed (value stays untouched)
# ---------------------------------------------------------------------------


def test_income_bypassed(pseudonym_on):
    """Income findings are NOT masked — value reaches the LLM untouched."""
    text = "เงินเดือน 85,000 บาท"
    amount_start = text.index("85,000")
    actions = [
        _match(
            "income",
            "financial",
            amount_start,
            amount_start + len("85,000"),
            "85,000",
            severity=SensitivityLevel.HIGH,
        ),
    ]
    masked, span_map, _ = DLPTransformer().mask_text(text, actions)

    assert "85,000" in masked
    assert "<r0>" not in masked
    assert "<<INCOME_1>>" not in masked
    assert span_map == {}


# ---------------------------------------------------------------------------
# Test 4: same value dedups to the same pseudonym
# ---------------------------------------------------------------------------


def test_same_value_same_pseudonym(pseudonym_on):
    """A value seen N times in one call collapses to one token + one binding."""
    text = "สมชาย สั่งของ แล้ว สมชาย จ่ายเงิน"
    # Build two matches over the same matched_text — the second
    # occurrence has a different start/end.
    first = text.index("สมชาย")
    second = text.index("สมชาย", first + 1)
    actions = [
        _match(
            "presidio_ner",
            "pii",
            first,
            first + len("สมชาย"),
            "สมชาย",
        ),
        _match(
            "presidio_ner",
            "pii",
            second,
            second + len("สมชาย"),
            "สมชาย",
        ),
    ]
    masked, span_map, _ = DLPTransformer().mask_text(text, actions)

    assert masked.count("<<PERSON_A>>") == 2
    assert "<<PERSON_B>>" not in masked
    assert span_map == {"<<PERSON_A>>": "สมชาย"}


# ---------------------------------------------------------------------------
# Test 5: distinct values get distinct suffixes per category
# ---------------------------------------------------------------------------


def test_different_values_different_pseudonyms(pseudonym_on):
    """Two emails get different numeric suffixes; two names get different letters."""
    text = (
        "สมชาย email somchai@gmail.com, "
        "สมหญิง email somying@gmail.com"
    )
    name1 = text.index("สมชาย")
    name2 = text.index("สมหญิง")
    email1 = text.index("somchai@gmail.com")
    email2 = text.index("somying@gmail.com")
    actions = [
        _match("presidio_ner", "pii", name1, name1 + len("สมชาย"), "สมชาย"),
        _match("presidio_ner", "pii", name2, name2 + len("สมหญิง"), "สมหญิง"),
        _match(
            "email", "pii", email1, email1 + len("somchai@gmail.com"),
            "somchai@gmail.com",
        ),
        _match(
            "email", "pii", email2, email2 + len("somying@gmail.com"),
            "somying@gmail.com",
        ),
    ]
    _, span_map, _ = DLPTransformer().mask_text(text, actions)

    assert span_map["<<PERSON_A>>"] == "สมชาย"
    assert span_map["<<PERSON_B>>"] == "สมหญิง"
    assert span_map["<<EMAIL_1>>"] == "somchai@gmail.com"
    assert span_map["<<EMAIL_2>>"] == "somying@gmail.com"


# ---------------------------------------------------------------------------
# Test 6: restore reverses pseudonym tokens 100%
# ---------------------------------------------------------------------------


def test_restore_100_percent(pseudonym_on):
    """``restore`` swaps every pseudonym in span_map back to the original."""
    span_map = {
        "<<PERSON_A>>": "สมชาย",
        "<<EMAIL_1>>": "somchai@gmail.com",
    }
    llm_text = (
        "<<PERSON_A>> สั่งของยอด 45000 และจะส่งใบเสร็จไป <<EMAIL_1>>"
    )
    restored = DLPTransformer().restore(
        llm_text,
        span_map,
        allowed_tokens=set(span_map.keys()),
    )

    assert restored == "สมชาย สั่งของยอด 45000 และจะส่งใบเสร็จไป somchai@gmail.com"
    assert "<<" not in restored
    assert ">>" not in restored


# ---------------------------------------------------------------------------
# Test 7: vault carries pseudonyms across turns
# ---------------------------------------------------------------------------


def test_cross_turn_vault_consistency(pseudonym_on):
    """Turn 2 of the same conversation reuses turn 1's pseudonym.

    Simulates the vault round-trip by passing the prior turn's
    ``span_map`` flipped to a ``vault_reverse_map`` (value → prior
    token) into the mask call, and priming ``start_counters`` from
    the vault via the public ``recover_counters_from_vault`` helper.
    """
    from app.services.dlp.transformer import recover_counters_from_vault

    # --- Turn 1
    text1 = "สมชาย"
    actions1 = [
        _match("presidio_ner", "pii", 0, len("สมชาย"), "สมชาย"),
    ]
    _, span_map_t1, _ = DLPTransformer().mask_text(text1, actions1)
    assert span_map_t1 == {"<<PERSON_A>>": "สมชาย"}

    # Vault snapshot (token → value, as the vault stores).
    vault_snapshot = span_map_t1
    vault_reverse = {value: token for token, value in vault_snapshot.items()}
    primed_counters = recover_counters_from_vault(vault_snapshot)

    # --- Turn 2 — same value, must reuse <<PERSON_A>>
    text2 = "สมชาย กลับมาอีกครั้ง"
    actions2 = [
        _match("presidio_ner", "pii", 0, len("สมชาย"), "สมชาย"),
    ]
    masked2, span_map_t2, _ = DLPTransformer().mask_text(
        text2,
        actions2,
        start_counters=primed_counters,
        vault_reverse_map=vault_reverse,
    )

    assert "<<PERSON_A>>" in masked2
    assert "<<PERSON_B>>" not in masked2
    assert span_map_t2 == {"<<PERSON_A>>": "สมชาย"}


# ---------------------------------------------------------------------------
# Test 8: mixed identity + secret in one message
# ---------------------------------------------------------------------------


def test_mixed_identity_and_secret(pseudonym_on):
    """Pseudonym for identity, ``<rN>`` for secret, only identity restores."""
    text = "สมชาย ใช้ key sk-ant-api03-abc123 ส่ง email somchai@gmail.com"
    name_s = text.index("สมชาย")
    key_s = text.index("sk-ant-api03-abc123")
    email_s = text.index("somchai@gmail.com")
    actions = [
        _match("presidio_ner", "pii", name_s, name_s + len("สมชาย"), "สมชาย"),
        _match(
            "api_key_secret",
            "credential",
            key_s,
            key_s + len("sk-ant-api03-abc123"),
            "sk-ant-api03-abc123",
            severity=SensitivityLevel.CRITICAL,
        ),
        _match(
            "email", "pii", email_s, email_s + len("somchai@gmail.com"),
            "somchai@gmail.com",
        ),
    ]
    masked, span_map, _ = DLPTransformer().mask_text(text, actions)

    assert "<<PERSON_A>>" in masked
    assert "<<EMAIL_1>>" in masked
    # API key on the legacy shape — the per-category counter for
    # identity and the __global__ counter for secret are independent,
    # so the secret lands on <r0> regardless of how many identity
    # findings preceded it.
    assert "<r0>" in masked
    assert "<<CREDENTIAL_1>>" not in masked  # never a pseudonym for secret

    # Restore allowing only the identity tokens (mirrors the policy
    # evaluator's RESTORE allow-list).
    restored = DLPTransformer().restore(
        masked,
        span_map,
        allowed_tokens={"<<PERSON_A>>", "<<EMAIL_1>>"},
    )
    assert "สมชาย" in restored
    assert "somchai@gmail.com" in restored
    # API key must NEVER make it into restored output.
    assert "sk-ant-api03-abc123" not in restored
    assert "<r0>" in restored  # secret placeholder stays


# ---------------------------------------------------------------------------
# Test 9: LLM-invented PII (not in inbound span_map) is not restored
# ---------------------------------------------------------------------------


def test_llm_invented_pii_not_restored(pseudonym_on):
    """A pseudonym in the LLM reply that doesn't exist in the inbound
    span_map must NOT be substituted by ``restore``.

    Mirrors the orchestrator's outbound flow: section 8 mints a fresh
    ``<<EMAIL_N>>`` for LLM-invented PII into ``outbound_span_map``
    which is intentionally NOT merged into the inbound ``span_map``
    threaded into ``restore``. The restore call therefore sees the
    new token as unknown and leaves it as-is — the orphan-redact pass
    that runs afterward is what eventually strips it from the
    user-facing reply.
    """
    inbound_span_map = {"<<PERSON_A>>": "สมชาย"}
    llm_text = (
        "<<PERSON_A>> ฝากแจ้ง <<EMAIL_2>> ด้วย"  # EMAIL_2 invented
    )
    restored = DLPTransformer().restore(
        llm_text,
        inbound_span_map,
        allowed_tokens={"<<PERSON_A>>"},
    )

    # PERSON_A was restored, EMAIL_2 left as-is (no binding to swap to).
    assert "สมชาย" in restored
    assert "<<EMAIL_2>>" in restored


# ---------------------------------------------------------------------------
# Test 10: regex pattern shared across modules (no drift possible)
# ---------------------------------------------------------------------------


def test_regex_shared_constant(pseudonym_on):
    """``dlp_prompt._MASK_TOKEN_RE`` is the SAME object as
    ``transformer.ANY_MASK_TOKEN_RE`` — adding a new token shape
    can never silently miss one of the two call sites.
    """
    from app.services import dlp_prompt
    from app.services.dlp import transformer

    assert dlp_prompt._MASK_TOKEN_RE is transformer.ANY_MASK_TOKEN_RE
    assert ANY_MASK_TOKEN_RE is transformer.ANY_MASK_TOKEN_RE

    # Sanity: the pattern matches both shapes.
    assert ANY_MASK_TOKEN_RE.findall("hello <r0> world <<EMAIL_1>>!") == [
        "<r0>",
        "<<EMAIL_1>>",
    ]
