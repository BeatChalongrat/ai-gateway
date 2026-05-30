"""CI gate: DLP Python↔Rust detector parity (re-audit Track D.5).

Wraps ``tests/dlp_parity_runner.py`` as a pytest so the parity gate is
no longer a manual ``docker exec`` step. It asserts 100% FULL parity —
identical ``{detector_name, entity_type, start, end}`` sets between the
in-process Python registry and the Rust ``dlp-sidecar`` — across every
fixture plus the evasion + gap cases (which now include the re-audit
remediation cases: base64 from_decoded relaxation [C1], labelled
allergy [H9], and the ≥3-entity-type credential bundle [H10]).

Skip behaviour (so this is safe in a CI job WITHOUT the sidecar):
* If grpcio / the Rust stubs can't be imported, or the sidecar is
  unreachable, the test SKIPS with a clear message rather than failing.

To run locally against the compose stack::

    docker compose up -d dlp-sidecar
    docker compose run --rm --no-deps \
        -v "$PWD/backend:/app" -w /app -e PYTHONPATH=/app \
        --entrypoint python backend -m pytest tests/test_dlp_parity.py -v

The standalone Markdown report is still available via::

    docker compose run --rm --no-deps -v "$PWD/backend:/app" -w /app \
        -e PYTHONPATH=/app --entrypoint python backend \
        tests/dlp_parity_runner.py
"""

from __future__ import annotations

import asyncio
import glob
import json
import os

import pytest

from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.rust_dlp_client import RustDlpClient
from tests.dlp_parity_runner import (
    EVASION_CASES,
    GAP_CASES,
    py_scan,
    rust_scan,
)


def _all_gated_cases() -> list[tuple[str, str, str]]:
    """Fixture cases + evasion + gap (the same set the runner gates on).

    DOC_CASES (M6/M7 documentation) are intentionally excluded — they
    are non-gated in the runner too.
    """
    cases: list[tuple[str, str, str]] = []
    fixture_dirs = [
        "/work/dlp-sidecar/tests/parity/fixtures",
        os.path.join(
            os.path.dirname(__file__), "..", "..",
            "dlp-sidecar", "tests", "parity", "fixtures",
        ),
    ]
    fixture_dir = next((d for d in fixture_dirs if os.path.isdir(d)), None)
    if fixture_dir:
        for fp in sorted(glob.glob(os.path.join(fixture_dir, "*.json"))):
            with open(fp, encoding="utf-8") as fh:
                data = json.load(fh)
            for c in data.get("cases", []):
                cases.append((data["detector"], c["id"], c["input"]))
    for c in EVASION_CASES + GAP_CASES:
        cases.append((c["detector"], c["id"], c["input"]))
    return cases


def test_dlp_python_rust_parity() -> None:
    async def _run() -> None:
        registry = get_default_registry(ner_enabled=False)
        client = RustDlpClient.instance()

        # Availability probe — skip cleanly if the sidecar isn't up.
        probe = await rust_scan(client, "ping")
        if probe is None:
            pytest.skip("rust dlp-sidecar unreachable; skipping parity gate")

        cases = _all_gated_cases()
        assert cases, "no parity cases discovered (fixtures missing?)"

        mismatches: list[tuple[str, str, list, list]] = []
        for grp, cid, text in cases:
            pm = set(py_scan(registry, text))
            rm = await rust_scan(client, text)
            rs = set(rm or [])
            if rm is None or rs != pm:
                mismatches.append(
                    (grp, cid, sorted(pm - rs), sorted(rs - pm))
                )

        assert not mismatches, (
            f"DLP Python↔Rust parity FAILED on {len(mismatches)} case(s): "
            + "; ".join(
                f"{g}/{c} only_py={op} only_rust={orr}"
                for g, c, op, orr in mismatches
            )
        )

    asyncio.run(_run())
