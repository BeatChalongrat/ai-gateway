"""infra/caddy/Caddyfile — a service with one copy is never taken out of rotation for a penalty time.

On 2026-10-07 web-chat was ready 3.5 s after its swap and the front door served it again after 16 s: one
failed connection had put its only upstream out for fail_duration (15 s), and every request in between
got "no upstreams available". Taking the only copy out sends nobody anywhere else. So an upstream list of
one imports (single) — no passive penalty, a request held and retried while the service restarts, a
health check every second — and (balanced) is kept for a list of two or more, where sending requests
elsewhere is the point.

    python -m unittest discover -s scripts/tests -p "test_front_door_single_copy.py" -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

CADDYFILE = Path(__file__).resolve().parents[2] / "infra" / "caddy" / "Caddyfile"


def seconds(value: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)?", value)
    assert m, value
    return float(m.group(1)) * {"ms": 0.001, "s": 1, "m": 60, None: 1}[m.group(2)]


def block(text: str, opening: str) -> str:
    """The body of the first block that starts with `opening {` (braces balanced)."""
    start = text.index(opening + " {")
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1:j]
    raise AssertionError(f"unbalanced block: {opening}")


def settings(body: str) -> dict[str, str]:
    out = {}
    for line in body.splitlines():
        parts = line.split("#")[0].split()
        if len(parts) >= 2 and parts[1] != "{":
            out[parts[0]] = parts[1]
    return out


class FrontDoorSingleCopyTest(unittest.TestCase):
    def setUp(self):
        self.text = CADDYFILE.read_text(encoding="utf-8")

    def proxies(self):
        for m in re.finditer(r"^\treverse_proxy ([^{\n]+)\{", self.text, re.M):
            upstreams = m.group(1).split()
            yield upstreams, block(self.text[m.start():], "reverse_proxy " + m.group(1).strip())

    def test_an_upstream_list_of_one_has_no_penalty_time(self):
        seen = 0
        for upstreams, body in self.proxies():
            seen += 1
            imports = re.findall(r"^\s*import (\w+)", body, re.M)
            if len(upstreams) == 1:
                self.assertIn("single", imports, f"{upstreams[0]} has one copy and must import (single)")
                self.assertNotIn("balanced", imports, f"{upstreams[0]} has one copy: (balanced) takes it out of rotation")
            else:
                self.assertIn("balanced", imports, f"{upstreams} has several copies and must import (balanced)")
                self.assertNotIn("single", imports, f"{upstreams}: (single) never takes a broken copy out")
        self.assertGreaterEqual(seen, 5, "the front door's five services")

    def test_single_holds_a_request_through_a_restart_and_notices_the_service_within_a_second(self):
        s = settings(block(self.text, "(single)"))
        self.assertEqual(seconds(s["fail_duration"]), 0, "a passive penalty on the only upstream is the 15 s gap")
        self.assertNotIn("max_fails", s, "max_fails means nothing without fail_duration")
        self.assertGreaterEqual(seconds(s["lb_try_duration"]), 10, "a restart takes 3.5 s; a request waits for it")
        self.assertLessEqual(seconds(s["lb_try_interval"]), 0.5)
        self.assertLessEqual(seconds(s["health_interval"]), 1, "back within a second of answering")
        self.assertGreaterEqual(seconds(s["health_timeout"]), 5, "FIX134 C1: one slow answer is not a failure")

    def test_balanced_is_still_there_for_a_second_copy(self):
        s = settings(block(self.text, "(balanced)"))
        self.assertGreater(seconds(s["fail_duration"]), 0)
        self.assertEqual(s["lb_policy"], "least_conn")


if __name__ == "__main__":
    unittest.main()
