"""``DLP_HMAC_KEY`` is gone, and stays gone.

Mask tokens were once derived from an HMAC of the value under this key. They have been counters for a long
time (``<rN>`` / ``<<CATEGORY_N>>``, kept stable across turns by the conversation vault), and the setting was
read by nothing — yet it sat in the settings, in the compose file with a "change in production" default, in
the README's table of secrets and in the architecture diagram, looking like a key in use. On live it ran
on that default. A key nobody uses is a thing someone will one day misunderstand: rotate it and expect an
effect, or find the default and report a hole. So it was removed (8 Oct 2026), and this keeps it out of the
application and of the compose files this lane can see; scripts/tests/test_no_dead_dlp_hmac_key.py sweeps
the whole checkout (templates and documents included).
"""

from __future__ import annotations

from pathlib import Path

import pytest

NAME = "DLP_HMAC_KEY"
# the checkout's root — or "/", where the backend test lane mounts the compose files read-only
ROOT = next((p for p in (Path(__file__).resolve().parents[2], Path("/")) if (p / "docker-compose.webchat.yml").is_file()),
            Path(__file__).resolve().parents[2])
COMPOSE = ["docker-compose.yml", "docker-compose.prod.yml", "docker-compose.webchat.yml", "docker-compose.agent-market.yml"]


def test_the_settings_have_no_such_field_and_ignore_the_variable(monkeypatch):
    from app.core.config import Settings

    assert "dlp_hmac_key" not in Settings.model_fields
    monkeypatch.setenv(NAME, "anything-at-all")
    assert not hasattr(Settings(), "dlp_hmac_key"), "a stray variable in an old environment is ignored, not adopted"


def test_nothing_in_the_application_reads_it():
    app = Path(__file__).resolve().parents[1] / "app"
    hits = []
    for p in app.rglob("*.py"):
        src = p.read_text(encoding="utf-8", errors="replace")
        if "settings.dlp_hmac_key" in src or f'"{NAME}"' in src or "dlp_hmac_key:" in src or "dlp_hmac_key =" in src:
            hits.append(str(p.relative_to(app)))
    assert hits == []


def test_the_compose_files_this_lane_can_see_do_not_pass_it():
    seen = 0
    for name in COMPOSE:
        path = ROOT / name
        if not path.is_file():
            continue
        seen += 1
        lines = [f"{name}:{i}" for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1) if NAME in line]
        assert lines == [], f"{NAME} is passed to a service again — it is read by nothing: {lines}"
    if seen < 3:
        pytest.fail(f"only {seen} compose file(s) reachable: this check would pass on nothing")
