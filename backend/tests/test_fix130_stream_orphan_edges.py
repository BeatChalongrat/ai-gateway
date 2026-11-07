"""FIX130 — a streamed answer never loses the space at a chunk edge.

The streaming emitter strips orphan mask tokens (``<rN>`` the model
invented, outbound masks with no binding) from EACH emitted slice with
``redact_orphan_tokens`` — whose whitespace cleanup ``.strip()``-ed the
slice. Right for a whole answer (the non-stream path), wrong for a slice
of one: when the slice started with the space between two words, the two
words were glued ("the staging box" → "the stagingbox"). Which slice
starts with a space depends only on where the 120-char DLP tail happens
to cut, so it came and went with chunk sizes; the FIX130 echo guard
changed the chunking and turned
``test_streaming_dlp::…test_pem_private_key_split_across_chunks_never_leaks``
red through this, which is how it was found.
"""

from __future__ import annotations

import pytest

from app.services.dlp.transformer import redact_orphan_tokens
from tests.test_streaming_dlp import (
    _PEM_KEY,
    _chat_stream,
    _chunks_of,
    _content_deltas,
    _install_stream_spy,
    _parse_sse,
)


def test_orphan_redaction_of_a_slice_keeps_its_edges():
    assert redact_orphan_tokens(" box:\n<r0>\nStore", {}, trim_edges=False) == " box:\n\nStore"
    # A whole answer is still trimmed (non-stream behaviour unchanged).
    assert redact_orphan_tokens(" box <r0> ", {}) == "box"


def test_final_tail_that_starts_with_a_space_keeps_it(monkeypatch):
    # The last 120 chars of the answer are the DLP tail flushed at the end;
    # make it start with the space between "friend" and "word" and carry an
    # invented token the orphan pass removes.
    tail = " word <r7> " + "x" * 100
    tail = tail + "y" * (120 - len(tail))
    answer = "Hello there friend" + tail
    assert len(tail) == 120 and tail[0] == " "
    _install_stream_spy(monkeypatch, chunks=_chunks_of(answer, 10))
    joined = "".join(_content_deltas(_parse_sse(_chat_stream("hi").content)))
    assert "Hello there friend word" in joined
    assert "<r7>" not in joined


@pytest.mark.parametrize("size", [7, 9, 11, 13, 17, 23, 29, 31])
def test_pem_answer_keeps_every_space_at_any_chunk_size(size, monkeypatch):
    head = "Sure — here is the generated deploy key for the staging box:\n"
    tail = "\nStore it in your secrets manager and never commit it to the repository. Rotate it every 90 days per the security policy."
    _install_stream_spy(monkeypatch, chunks=_chunks_of(head + _PEM_KEY + tail, size))
    joined = "".join(_content_deltas(_parse_sse(_chat_stream("give me the deploy key").content)))
    assert "MIIEpAIBAAKCAQEA" not in joined
    assert "deploy key for the staging box:" in joined
    assert "Store it in your secrets manager and never commit it to the repository." in joined

