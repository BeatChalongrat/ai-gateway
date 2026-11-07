"""AM11b — a package's instructions and the platform's prompt-echo guard.

Carried from AM12: "the prompt-echo guard was cutting pieces out of model answers — were the other five
agents affected?" One was. The guard (``app.services.prompt_echo_guard``) removes from an answer every run
of 40 characters that repeats the system message. A package's AGENT.md is the system message of the
commentary its report carries, and the report agent's AGENT.md printed, as an example of how to name a
figure, a sentence a commentary really writes:

    “ค่าใช้จ่ายในการขายและบริหารสูงกว่างบ {operating_expenses_var_budget_pct}”

A model that said that, in those words, had 44 characters taken out of its answer. What was printed in
the executive summary was "…ค่าใช้จ่ายในการขายและบริหารที่:abs}" — on live, in 4 of the 27 commentaries a
model wrote between 1 and 6 October 2026.

Held here, with the guard itself, for every package: the instructions do not print a sentence with a
figure's name in it, and a figure's name they do show can be written into a sentence the way a commentary
writes one and come back whole.
"""
from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

import pytest

from app.services.prompt_echo_guard import ECHO_MIN_RUN, build_echo_fingerprints, scrub_echo

PACKAGES = pathlib.Path(__file__).resolve().parents[1] / "agent_market" / "packages"
NAME = re.compile(r"\{([A-Za-z][A-Za-z0-9_.\-/]*)\}")
# how a commentary leads up to a figure, and goes on after it
BEFORE = ("สูงกว่างบ ", "ต่ำกว่างบ ", "ที่สูงกว่างบ ", "สูงกว่างบประมาณ ", "ต่ำกว่างบประมาณ ", "เพิ่มขึ้น ", "ลดลง ", "อยู่ที่ ", "คิดเป็น ", "เท่ากับ ")
AFTER = (" จากงวดก่อน", " เมื่อเทียบกับงบประมาณ", " ของรายได้", " ในงวดนี้", "")


def _instructions():
    return [pytest.param(p.read_text(encoding="utf-8"), id=p.parent.name) for p in sorted(PACKAGES.glob("*/AGENT.md"))]


def _fingerprints(text: str):
    return build_echo_fingerprints([SimpleNamespace(role="system", content=text)])


def test_every_package_has_instructions_to_hold_to_this():
    assert len(_instructions()) >= 6


def test_the_sentence_that_was_cut_on_live_is_cut_no_more():
    text = (PACKAGES / "financial_report" / "AGENT.md").read_text(encoding="utf-8")
    said = "ปัจจัยหลักที่ทำให้กำไรสุทธิต่ำกว่างบคือค่าใช้จ่ายในการขายและบริหารที่สูงกว่างบ {operating_expenses_var_budget_pct:abs}"
    kept, removed, runs = scrub_echo(_fingerprints(text), said)
    assert (kept, removed, runs) == (said, 0, 0), f"the guard still takes {removed} characters out of the commentary: {kept!r}"
    # … and the guard is what this test thinks it is: the instructions as they were lose the same 44 characters
    old = text + "\nเช่น “ค่าใช้จ่ายในการขายและบริหารสูงกว่างบ {operating_expenses_var_budget_pct}”\n"
    kept, removed, _ = scrub_echo(_fingerprints(old), said)
    assert removed >= ECHO_MIN_RUN and kept.endswith("ที่:abs}"), (removed, kept)


@pytest.mark.parametrize("text", _instructions())
def test_no_example_sentence_carries_a_figures_name(text):
    quoted = re.findall(r"“[^”\n]*”", text)
    carrying = [q for q in quoted if NAME.search(q)]
    assert not carrying, f"an example sentence with a figure's name in it is a sentence an answer repeats: {carrying}"


@pytest.mark.parametrize("text", _instructions())
def test_a_name_the_instructions_show_is_written_into_a_sentence_whole(text):
    fingerprints = _fingerprints(text)
    for name in sorted(set(NAME.findall(text))):
        for mod in ("", ":abs"):
            for before in BEFORE:
                for after in AFTER:
                    said = f"{before}{{{name}{mod}}}{after}"
                    kept, removed, _ = scrub_echo(fingerprints, said)
                    assert removed == 0, f"{said!r} came back as {kept!r}"
