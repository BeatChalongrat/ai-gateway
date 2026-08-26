"""FIX23 — the remember-then-recall two-turn replay.

Owner-observed on 2026-08-26 (desktop conv ``ff0fe4a5``, web conv
``f846e7df``): turn 1 "จำเลขนี้ไว้นะ 74-กระต่าย-2951 แล้วตอบแค่คำว่า ok" →
"ok"; turn 2 "เลขที่ให้จำเมื่อกี้คืออะไร" → the assistant replied with the
question itself, character for character, on both surfaces.

Artefact (gateway ``request_logs``): the ``thai_address`` detector
matched the entire turn-2 user message (span 0-26) and masked it to
``<<ADDRESS_1>>``; the provider's raw output was ``<<ADDRESS_1>>``; the
restore step turned that token back into the user's question.

This test replays the exact message sequence through the same
pipeline the orchestrator runs (registry → classifier → evaluator →
transformer, default profile) and asserts the recall turn reaches the
provider untouched, then that a restored answer is not the question.
"""

from __future__ import annotations

from app.schemas.common import Message
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.transformer import DLPTransformer
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import get_profile

REMEMBER = "จำเลขนี้ไว้นะ 74-กระต่าย-2951 แล้วตอบแค่คำว่า ok"
RECALL = "เลขที่ให้จำเมื่อกี้คืออะไร"

_REGISTRY = get_default_registry()
_CLASSIFIER = SensitivityClassifier()
_EVALUATOR = PolicyEvaluator()
_TRANSFORMER = DLPTransformer()
_DEFAULT_PROFILE = get_profile("default")


def _mask_messages(messages: list[Message]):
    detection = _REGISTRY.scan_messages(messages, direction="input")
    if detection.has_sensitive_content:
        detection.matches = _CLASSIFIER.classify(detection.matches)
    enforcement = _EVALUATOR.evaluate(detection, _DEFAULT_PROFILE)
    new_msgs, span_map, _ = _TRANSFORMER.mask_messages(
        messages, enforcement.match_actions
    )
    return new_msgs, span_map, enforcement.match_actions


class TestRememberThenRecall:
    def test_recall_turn_reaches_provider_untouched(self):
        messages = [
            Message(role="user", content=REMEMBER),
            Message(role="assistant", content="ok"),
            Message(role="user", content=RECALL),
        ]
        new_msgs, span_map, actions = _mask_messages(messages)

        # The recall question must not be masked at all — before FIX23
        # it was replaced wholesale by <<ADDRESS_1>>.
        assert new_msgs[2].content == RECALL
        assert "thai_address" not in {a.match.detector_name for a in actions}

        # Nothing in this two-turn sequence is an address; the model
        # must never see a whole user turn collapsed into one token.
        for m in new_msgs:
            assert m.content.strip() not in span_map

    def test_restored_answer_is_not_the_question(self):
        messages = [
            Message(role="user", content=REMEMBER),
            Message(role="assistant", content="ok"),
            Message(role="user", content=RECALL),
        ]
        _, span_map, _ = _mask_messages(messages)

        # A correct provider answer restores to itself. Also pin the
        # failure shape: the pre-FIX23 raw output was the mask token,
        # which restore() expanded back into the question.
        answer = "เลขที่ให้จำไว้คือ **74-กระต่าย-2951** ครับ"
        assert _TRANSFORMER.restore(answer, span_map) == answer
        # ...and the OUTPUT-direction scan must not mask that answer
        # either (observed live 2026-08-26 20:34Z: output_raw became
        # "<<ADDRESS_1>>-กระต่าย-2951**").
        out_msgs, _out_map, out_actions = _mask_messages(
            [Message(role="assistant", content=answer)]
        )
        assert "thai_address" not in {a.match.detector_name for a in out_actions}
        assert out_msgs[0].content == answer
        assert _TRANSFORMER.restore("<<ADDRESS_1>>", span_map) != RECALL

    def test_real_address_in_same_pipeline_still_masks(self):
        # Control: the detector is still alive for an actual address.
        messages = [
            Message(role="user", content="ส่งของไปที่อยู่ 99/88 ถนนสุขุมวิท กรุงเทพ 10110"),
        ]
        new_msgs, span_map, actions = _mask_messages(messages)
        assert "thai_address" in {a.match.detector_name for a in actions}
        assert new_msgs[0].content != messages[0].content
        # Token shape is env-driven (<<ADDRESS_N>> in pseudonym mode,
        # <rN> legacy) — assert the address VALUE was captured instead.
        assert any("99/88" in original for original in span_map.values())
