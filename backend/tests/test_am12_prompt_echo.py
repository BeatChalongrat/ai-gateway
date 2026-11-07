"""AM12 — an agent's prompts and the platform's prompt-echo guard.

The guard (``app.services.prompt_echo_guard``, FIX130) removes from a model's
answer every run of 40 characters that repeats the system message, so a system
prompt never reaches a person as answer text. An Agent Market package asks a
model for one JSON object in a given shape — and the shape is the one part of
a prompt the answer is meant to repeat. Printed among the rules, an answer
that followed it to the letter came back with pieces of its own JSON removed
(measured on the test stack: two of three saved pages "read by no model", a
round of four minutes).

So a package prompt is two parts, split by one line: the rules (the system
message) and, below the line, what the runner sends at the end of the user
message — the shape. This test holds every package to that with the guard
itself: an answer that follows a prompt's shape, however it is spaced, passes
the guard whole.
"""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from app.services.prompt_echo_guard import ECHO_MIN_RUN, build_echo_fingerprints, scrub_echo

PACKAGES = pathlib.Path(__file__).resolve().parents[1] / "agent_market" / "packages"
MARK = "\n=== with the question ===\n"


def _prompts():
    out = []
    for manifest in sorted(PACKAGES.glob("*/agent.json")):
        m = json.loads(manifest.read_text(encoding="utf-8"))
        for name, rel in sorted(((m.get("web") or {}).get("prompts") or {}).items()):
            text = (manifest.parent / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
            out.append(pytest.param(text, id=f"{manifest.parent.name}/{name}"))
    return out


def _system(text: str):
    return [SimpleNamespace(role="system", content=text)]


def _answers(shape: str):
    """The shape as a model may send it back: as printed, on one line, indented."""
    obj = json.loads(shape)
    return [
        shape,
        json.dumps(obj, ensure_ascii=False),
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
        json.dumps(obj, ensure_ascii=False, indent=2),
    ]


def test_there_are_prompts_to_hold_to_this():
    assert len(_prompts()) >= 4


@pytest.mark.parametrize("text", _prompts())
def test_the_shape_of_an_answer_goes_with_the_question(text):
    assert text.count(MARK) == 1, "a package prompt is its rules, the marker line, then the shape"
    rules, ask = text.split(MARK)
    assert "{" not in rules, "no JSON among the rules: the shape belongs below the marker"
    shape = ask[ask.index("{"):].strip()
    assert isinstance(json.loads(shape), dict)


@pytest.mark.parametrize("text", _prompts())
def test_an_answer_that_follows_the_shape_passes_the_guard_whole(text):
    rules, ask = text.split(MARK)
    shape = ask[ask.index("{"):].strip()
    fingerprints = build_echo_fingerprints(_system(rules))
    for answer in _answers(shape):
        kept, removed, runs = scrub_echo(fingerprints, answer)
        assert (kept, removed, runs) == (answer, 0, 0), f"the guard cut {removed} characters out of a faithful answer"


@pytest.mark.parametrize("text", _prompts())
def test_the_same_shape_printed_among_the_rules_is_cut(text):
    """Why the rule exists — and what makes the test above one that can fail."""
    rules, ask = text.split(MARK)
    shape = ask[ask.index("{"):].strip()
    if len("".join(shape.split())) < 2 * ECHO_MIN_RUN:
        pytest.skip("a shape this short has no run the guard would take")
    fingerprints = build_echo_fingerprints(_system(rules + "\n\n" + shape))
    kept, removed, runs = scrub_echo(fingerprints, shape)
    assert removed > 0 and runs > 0 and kept != shape
    with pytest.raises(ValueError):
        json.loads(kept)
