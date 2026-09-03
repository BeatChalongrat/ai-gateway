"""FIX50 P1-B — code and พ.ศ. years must survive the DLP pipeline.

The FIX48 parity audit measured the product corrupting answers the bare
API got right (BACKLOG item 10). The diagnosis split it three ways:

  * ``::DATE`` eaten (``'2068-01-01'TE``) — the ipv6 pattern matches
    ``::DA`` inside SQL casts; the prepared fix lives on the
    ``fix50/ipv6-cast-eater`` branch awaiting the owner (it touches an
    egress-masking detector).
  * 2568 → 2068 — the model's own arithmetic; the question reaches the
    provider unmodified (pinned here).
  * mem-3's missing code block — the provider genuinely stopped at 17
    completion tokens; nothing of ours ate it.

These tests pin the parts that are TRUE TODAY on the full mask
pipeline (scan → classify → policy → transform), so a future detector
or policy change that starts rewriting years or clean code blocks
fails loudly instead of surfacing as another FIX48-style judged loss.
"""

from __future__ import annotations

from app.schemas.common import Message
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.transformer import DLPTransformer
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import get_profile


def _mask_full_pipeline(text: str, direction: str) -> str:
    registry = get_default_registry()
    messages = [Message(role="user", content=text)]
    detection = registry.scan_messages(messages, direction=direction)
    classifier = SensitivityClassifier()
    if detection.has_sensitive_content:
        detection.matches = classifier.classify(detection.matches)
    enforcement = PolicyEvaluator().evaluate(detection, get_profile("default"))
    new_msgs, _, _ = DLPTransformer().mask_messages(
        messages, enforcement.match_actions
    )
    return new_msgs[0].content


TP1_QUESTION = (
    "เขียน SQL (PostgreSQL) หายอดขายรวมต่อเดือนของปี 2568 จากตาราง "
    "orders(id, created_at, amount_satang) เดือนที่ไม่มีออร์เดอร์ต้องขึ้นเป็น 0 ด้วย "
    "ตอบเป็นโค้ดอย่างเดียว"
)

LONG_CLEAN_CODE_BLOCK = (
    "Here's how to read a JSON file and print one field.\n\n"
    "```python\n"
    "import json\n\n"
    "def read_field(path: str, field: str):\n"
    "    with open(path, encoding='utf-8') as f:\n"
    "        data = json.load(f)\n"
    "    value = data[field]\n"
    "    print(value)\n"
    "    return value\n\n"
    "if __name__ == '__main__':\n"
    "    read_field('data.json', 'productName')\n"
    "```\n\n"
    "The same idea works in TypeScript with JSON.parse over fs.readFileSync."
)


def test_buddhist_year_in_question_reaches_provider_unchanged():
    # The year 2568 is a year, not PII: the input leg must pass the
    # question through verbatim. (The 2068 in FIX48's corrupted answer
    # was the MODEL's arithmetic — output_raw in the ledger proves the
    # question arrived intact.)
    assert _mask_full_pipeline(TP1_QUESTION, "input") == TP1_QUESTION


def test_buddhist_year_in_answer_survives_output_leg():
    text = "ปี 2568 คือ ค.ศ. 2025 ครับ ยอดรวมของปี 2568 อยู่ในตารางด้านล่าง"
    assert _mask_full_pipeline(text, "output") == text


def test_long_clean_code_block_survives_output_leg():
    assert (
        _mask_full_pipeline(LONG_CLEAN_CODE_BLOCK, "output")
        == LONG_CLEAN_CODE_BLOCK
    )
