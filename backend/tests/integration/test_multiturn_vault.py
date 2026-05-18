"""Multi-turn vault integration: turn 1 mints a token, turn 2 echoes it.

End-to-end-shape (no FastAPI): exercises ``ConversationVault.extend`` +
``DLPTransformer.restore`` with a resolver wired to the same vault.
Verifies the user sees the original value in turn 2 even though that
turn's ``span_map`` is empty (the AI invented the echo from memory).
"""

from __future__ import annotations

import fakeredis

from app.services.dlp.conversation_vault import ConversationVault
from app.services.dlp.transformer import DLPTransformer


def test_multiturn_orphan_resolved():
    redis_client = fakeredis.FakeStrictRedis(decode_responses=True)
    vault = ConversationVault(redis_client=redis_client)
    transformer = DLPTransformer()
    conv_id = "conv-multiturn-1"

    # --- Turn 1 ---
    # User sent "my email is x@y.com"; gateway masked to <r0>.
    turn1_span_map = {"<r0>": "x@y.com"}
    vault.extend(conv_id, turn1_span_map)

    # AI's turn 1 response says "Got it, will reach out to <r0>".
    # Restore on turn 1 succeeds locally — same-turn span_map is present.
    turn1_restored = transformer.restore(
        "Got it, will reach out to <r0>",
        span_map=turn1_span_map,
    )
    assert turn1_restored == "Got it, will reach out to x@y.com"

    # --- Turn 2 ---
    # User sends a fresh message with no PII; current turn's span_map
    # is therefore empty. AI's response still echoes <r0> from
    # its memory of turn 1. Without the vault this would leak the
    # literal token; with the vault, restore looks it up and returns
    # the original value.
    turn2_span_map: dict[str, str] = {}
    vault_resolver = lambda tokens: vault.resolve(conv_id, tokens)

    turn2_restored = transformer.restore(
        "Sending the receipt to <r0> as discussed.",
        span_map=turn2_span_map,
        vault_resolver=vault_resolver,
    )
    assert turn2_restored == "Sending the receipt to x@y.com as discussed."
    assert "<r0>" not in turn2_restored, (
        "Cross-turn orphan must be replaced from the vault — user "
        "never sees the literal mask token"
    )
