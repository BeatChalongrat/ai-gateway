"""FIX61 Part 1.3 — every ``==`` pin in requirements.txt is what is installed.

Why this exists: FIX57 pinned ``pytest-randomly==5.0.0`` and put
``--randomly-seed=0`` in pyproject, and for three rounds the gate stayed
"green" only because the package had been installed BY HAND into a
container whose image predated the pin. Recreating the container broke
the lane (``unrecognized arguments: --randomly-seed=0``). Nothing checked
that a pin had actually taken effect; this does. It runs wherever the
suite runs — the compose container and the CI image both install
``requirements-dev.txt`` — and goes red the moment the environment and
the file disagree, in either direction.

Only exact pins are checked. Ranges (``>=``) are deliberately loose in
the file and are not this test's business.
"""
from __future__ import annotations

import pathlib
import re
from importlib import metadata

import pytest

_REQ = pathlib.Path(__file__).resolve().parent.parent / "requirements.txt"
_PIN_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*(?P<version>[^\s;#]+)"
)


def _pins() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in _REQ.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = _PIN_RE.match(line)
        if m:
            out.append((m.group("name"), m.group("version")))
    return out


def _canonical(name: str) -> str:
    # PEP 503 normalisation: case-insensitive, runs of -_. collapse to -
    return re.sub(r"[-_.]+", "-", name).lower()


def test_requirements_file_has_exact_pins():
    # A guard on the guard: if someone rewrites the file into ranges only,
    # this test would silently check nothing. 28 pins at the time of writing.
    assert len(_pins()) >= 20


@pytest.mark.parametrize(("name", "pinned"), _pins(), ids=lambda v: v)
def test_installed_version_matches_pin(name: str, pinned: str):
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        # Some distributions register under a differently normalised name.
        installed = None
        wanted = _canonical(name)
        for dist in metadata.distributions():
            if _canonical(dist.metadata["Name"]) == wanted:
                installed = dist.version
                break
    assert installed is not None, (
        f"{name}=={pinned} is pinned in requirements.txt but is not installed "
        f"— the image that runs this suite predates the pin; rebuild it "
        f"(docker compose build backend), do not pip install by hand"
    )
    assert installed == pinned, (
        f"{name}: requirements.txt pins {pinned}, the environment has {installed} "
        f"— the image is stale (rebuild it) or the file was edited without a rebuild"
    )
