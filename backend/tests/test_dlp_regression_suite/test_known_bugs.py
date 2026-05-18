"""Locked-in regressions from the DLP audit.

Every test in this file corresponds to a real production bug that has
since been fixed. The naming convention is
``test_<symptom>_<surface>`` so the failure message reads like a
mini-incident report when one of these breaks. Keep the comment at
the top of each test pointing at the *symptom* (what the user saw)
and the *fix* (commit / change that resolved it).
"""

from __future__ import annotations

from ..test_dlp_detectors._helpers import has_match, mask, restore


# ---------------------------------------------------------------------------
# Religion detector — ``the following`` substring false positive
# (2026-05-13 production trace)
# ---------------------------------------------------------------------------


def test_religion_false_positive_on_the_following_en():
    # Before the fix, the bare-verb branch of the religion regex
    # matched ``follow`` inside ``following`` and the pronoun branch
    # matched ``he`` inside ``the``, producing
    # ``"Please complete t[RELIGION_0]ing tasks"`` for innocuous prose.
    # The fix anchors every English alternation with ``\b``.
    masked, _, _ = mask("Please complete the following tasks")
    assert masked == "Please complete the following tasks"


def test_religion_false_positive_on_thai_polite_phrase():
    # Thai equivalent ("the following details") — must also stay
    # quiet. The Thai keyword list never matched this phrase in the
    # production trace, so this is a guard against future Thai
    # keyword additions accidentally widening the surface.
    masked, _, _ = mask("รายการดังต่อไปนี้")
    assert masked == "รายการดังต่อไปนี้"


# ---------------------------------------------------------------------------
# Phone vs NER priority on Thai-format numbers
# ---------------------------------------------------------------------------


def test_phone_priority_over_ner_en():
    # Bug: ``+66 81-234-5678`` rendered as a NAME token because NER
    # PERSON and phone matched the same span and NER had stable
    # ordering. The phone detector now carries priority=5 which beats
    # NER's default priority=0.
    _, _, matches = mask("+66 81-234-5678")
    assert has_match(matches, "phone")
    assert not has_match(matches, "presidio_ner", "thai_name", "thai_name_no_prefix")


# ---------------------------------------------------------------------------
# Health condition span over-greedy: ate following field
# ---------------------------------------------------------------------------


def test_health_condition_does_not_eat_salary_en():
    text = (
        "Medical: diabetes and high blood pressure, "
        "Salary: 120,000 THB/month"
    )
    masked, span_map, matches = mask(text)
    assert has_match(matches, "health_condition")
    assert has_match(matches, "income")
    # Round-trip is the strongest guarantee: nothing got eaten.
    assert restore(masked, span_map) == text


def test_health_condition_does_not_eat_salary_th():
    text = "ผมป่วยเป็นเบาหวาน, เงินเดือน: 85,000 บาท"
    _, _, matches = mask(text)
    assert has_match(matches, "health_condition")
    assert has_match(matches, "income")


# ---------------------------------------------------------------------------
# IP inside a credential URL — double-tagging bug
# ---------------------------------------------------------------------------


def test_ip_address_not_double_tagged_in_credential():
    text = "postgresql://admin:pass@192.168.1.105:5432/prod"
    _, span_map, _ = mask(text)
    # Overlap resolution must keep exactly one span — the full URL —
    # with no separate IP span minted alongside it.
    assert len(span_map) == 1
    [value] = span_map.values()
    assert value == text


# ---------------------------------------------------------------------------
# NER denylist — ``Thai`` and ``Salary`` mis-tagged as PERSON
# ---------------------------------------------------------------------------


def test_thai_word_not_tagged_as_name():
    # Bug: NER tagged the bare word ``Thai`` (NRP / nationality
    # adjective) as a PERSON inside ``Thai ID:`` field labels.
    # Denylist now drops it pre-emit.
    text = "Customer: Somchai Jaidee, Thai ID: 3100112345674"
    _, span_map, _ = mask(text)
    for tok, val in span_map.items():
        assert val.strip().lower() != "thai", (
            f"NER leaked 'Thai' as {tok!r}"
        )


def test_salary_word_not_tagged_as_name():
    # Bug: ``Salary`` as a field label (PERSON), surfaced once the
    # health_condition over-match stopped swallowing the line.
    text = "Salary: 85,000 THB/month"
    _, span_map, _ = mask(text)
    for tok, val in span_map.items():
        assert val.strip().lower() != "salary", (
            f"NER leaked 'Salary' as {tok!r}"
        )


# ---------------------------------------------------------------------------
# Mask tokens echoed back must pass through untouched
# ---------------------------------------------------------------------------


def test_own_tokens_not_retagged_by_presidio():
    # The orchestrator scans the inbound prompt AFTER inbound masking
    # has run. Tokens like ``<r0>`` are already-redacted markers and
    # must NOT be re-classified as names.
    text = "Your email is <r0> and your name is <r1>"
    masked, _, _ = mask(text)
    assert masked == text


# ---------------------------------------------------------------------------
# Double-angle sanitizer — model emits ``<<r0>>``
# ---------------------------------------------------------------------------


def test_double_angle_sanitizer_en():
    # Some models occasionally over-wrap the masked tokens we passed
    # in. The sanitizer unwraps ``<<…>>`` to ``<…>`` when the inner
    # token is in span_map; otherwise it replaces with ``[REDACTED]``
    # so the panel never shows ``Phone: <<r1>>``.
    from app.services.dlp.transformer import sanitize_hallucinated_brackets

    span_map = {"<r0>": "+66 81-234-5678"}
    assert (
        sanitize_hallucinated_brackets("Phone: <<r0>>", span_map)
        == "Phone: <r0>"
    )


def test_double_angle_sanitizer_redacts_unknown():
    # Inner token NOT in span_map — sanitizer falls back to literal
    # ``[REDACTED]``.
    from app.services.dlp.transformer import sanitize_hallucinated_brackets

    assert (
        sanitize_hallucinated_brackets("Phone: <<r1>>", {})
        == "Phone: [REDACTED]"
    )


# ---------------------------------------------------------------------------
# Income / password labels were being eaten on mask
# ---------------------------------------------------------------------------


def test_income_label_preserved_en():
    masked, _, _ = mask("Salary: 85,000 THB/month")
    assert masked.startswith("Salary:")


def test_income_label_preserved_th():
    masked, _, _ = mask("เงินเดือน: 85,000 บาท/เดือน")
    assert "เงินเดือน:" in masked


def test_password_label_preserved_en():
    masked, _, _ = mask("Password: P@ssw0rd123!")
    assert masked.startswith("Password:")


# ---------------------------------------------------------------------------
# Network category — output-restore policy wiring
# ---------------------------------------------------------------------------


def test_network_category_restored_to_user():
    # Bug: outbound restore policy didn't include the ``network``
    # category, so ``[IP_ADDRESS_0]`` placeholders leaked to the user
    # when the AI echoed them back. The fix added every PII category
    # (including network) to ``category_actions_output`` with action
    # RESTORE.
    from app.domain.dlp import EnforcementAction
    from app.services.policy.profiles import DEFAULT_PROFILE

    assert DEFAULT_PROFILE.category_actions_output is not None
    network_action = DEFAULT_PROFILE.category_actions_output.get("network")
    assert network_action == EnforcementAction.RESTORE


# ---------------------------------------------------------------------------
# Thai national ID checksum gate
# ---------------------------------------------------------------------------


def test_thai_national_id_invalid_checksum_not_detected():
    # The bare-id detection path requires mod-11. Random 13-digit
    # runs that look like an ID but fail the checksum must not be
    # masked unless the keyword path is also satisfied — this case
    # has no keyword so the checksum gate alone must reject.
    invalid = "3100112345671"  # last digit wrong; valid would be 4
    masked, _, _ = mask(invalid)
    assert masked == invalid


def test_thai_national_id_valid_checksum_detected():
    valid = "3100112345674"  # mod-11 verified
    _, _, matches = mask(valid)
    assert has_match(matches, "thai_national_id")
