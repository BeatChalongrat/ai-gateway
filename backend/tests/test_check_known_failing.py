"""FIX61 — the release-gate checker must not call an unfinished lane green.

Seen live: a lane interrupted at 12 % left an output with no FAILED lines
and no summary line; the checker counted zero failures and printed
GATE GREEN. A run that did not finish proves nothing and must exit 2,
the same code as "could not read the input".
"""
from __future__ import annotations

import importlib.util
import pathlib

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "check_known_failing.py"
_spec = importlib.util.spec_from_file_location("check_known_failing", _SCRIPT)
ckf = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ckf)


def _run(tmp_path, text: str) -> int:
    p = tmp_path / "lane.out"
    p.write_text(text, encoding="utf-8")
    return ckf.main(["check_known_failing.py", str(p)])


def test_interrupted_lane_is_not_green(tmp_path):
    # dots, a couple of F markers, no FAILED lines, no summary — the shape
    # an interrupted `pytest -q` leaves behind
    text = "....F....\n.....F..... [ 12%]\n"
    assert ckf.lane_finished(text) is False
    assert _run(tmp_path, text) == 2


def test_empty_output_is_not_green(tmp_path):
    assert _run(tmp_path, "") == 2


def _ledger(tmp_path, monkeypatch, *names: str) -> None:
    """Point the checker at a ledger of these names — the test's own, not the
    repo's. FIX133: the repo ledger is meant to shrink to nothing (it did),
    and this test used to read a name from it, so an empty ledger failed the
    test that guards the gate."""
    p = tmp_path / "known_failing.txt"
    p.write_text("# test ledger\n" + "".join(f"{n}  # 2026-01-01 reason\n" for n in names), encoding="utf-8")
    monkeypatch.setattr(ckf, "_KNOWN", p)


def test_finished_lane_with_only_known_failures_is_green(tmp_path, monkeypatch):
    name = "tests/unit/test_known.py::test_documented_breakage"
    _ledger(tmp_path, monkeypatch, name)
    text = f"FAILED {name} - AssertionError\n1 failed, 10 passed, 2 warnings in 3.21s\n"
    assert ckf.lane_finished(text) is True
    assert _run(tmp_path, text) == 0


def test_finished_clean_lane_with_an_empty_ledger_is_green(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    assert _run(tmp_path, "4756 passed, 58 skipped, 6 xfailed in 614.66s (0:10:14)\n") == 0


def test_the_repo_ledger_parses(tmp_path):
    # whatever it holds today, the real file must be readable by the gate
    assert isinstance(ckf.load_known(ckf._KNOWN), dict)


def test_finished_lane_with_a_new_failure_is_red(tmp_path):
    text = (
        "FAILED tests/unit/test_this_name_is_not_in_the_ledger.py::test_x - boom\n"
        "===== 1 failed, 10 passed in 3.21s =====\n"
    )
    assert _run(tmp_path, text) == 1


def test_summary_line_shapes():
    assert ckf.lane_finished("3995 passed in 265.70s\n")
    assert ckf.lane_finished("21 failed, 3995 passed, 27 skipped, 6 xfailed in 515.43s (0:08:35)\n")
    assert ckf.lane_finished("no tests ran in 0.01s\n")
    assert not ckf.lane_finished("collected 4000 items\n")
